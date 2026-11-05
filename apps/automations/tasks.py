from celery import shared_task
from django.utils import timezone
from .engine.orchestrator import AutomationOrchestrator
from .models import WebhookDelivery
from .scheduling import record_scheduled_run


def _schedule_continuation(automation_id):
        """
        The callable the engine uses to bring a paused run back.

        It lives here, outside `engine/`, because dispatching is a Celery concern and
        the engine is not allowed to know Celery exists (tests/test_engine_purity.py).
        The payload is the run's whole execution context, so the continuation resumes
        with the same `steps` and `vars` instead of starting the automation over.

        `run_id` comes from the orchestrator's wrapper: continuing the same
        AutomationRun is what lets a paused run skip the steps it already completed
        instead of starting a second history (ROADMAP section 5.2).
        """
        def schedule(next_step_key,context,delay_seconds = None,eta = None,run_id = None):
                options = {}

                if eta is not None:
                        options['eta'] = eta
                elif delay_seconds:
                        options['countdown'] = delay_seconds

                kwargs = {'start_step_key' : next_step_key}

                # Only send it when it is known: the plain continuation keeps the
                # payload it always had.
                if run_id is not None:
                        kwargs['run_id'] = str(run_id)

                run_automation.apply_async(
                        args = (str(automation_id),context),
                        kwargs = kwargs,
                        **options
                )

        return schedule


@shared_task
def run_automation(automation_id ,data ,source = 'manual' ,start_step_key = None ,
                run_id = None ,mode = 'live'):

        # `start_step_key` is only set by a wait step scheduling its own continuation.
        # `run_id` is set by the same continuation and by an explicit recovery of a
        # crashed run. The plain call stays plain, so no existing caller - or test
        # asserting on it - has to know that resumable runs exist.
        #
        # `mode` is set by a dry run (`RunMode.TEST`) and by a replay of one, and it
        # defaults to 'live' so `run_automation(str(automation.id), payload)` - the
        # call every existing caller and test makes - still means the same thing.
        options = {
                'schedule_continuation' : _schedule_continuation(automation_id),
                'run_id' : run_id,
                'mode' : mode,
        }

        if start_step_key is None:
                result = AutomationOrchestrator().run(automation_id,data,**options)
        else:
                result = AutomationOrchestrator().run(
                        automation_id,data,
                        start_step_key = start_step_key,
                        **options
                )

        # Only beat passes source='scheduled' (see scheduling.sync_periodic_task); a
        # manual or webhook run must not spend a counted schedule's budget.
        if source == 'scheduled':
                record_scheduled_run(automation_id)

        return result


@shared_task
def cleanup_webhook_deliveries():
        """
        Delete idempotency records whose window has passed.

        Expiry is enforced by the claim path itself - an expired row is reclaimed
        immediately, whether or not this ever runs - so cleanup is only about
        bounding table growth, not correctness. It returns the number of rows
        removed because "did the beat entry fire and do anything" is otherwise
        invisible.
        """
        deleted, _per_model = WebhookDelivery.objects.filter(
                expires_at__lte = timezone.now()
        ).delete()

        return deleted
