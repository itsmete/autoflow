from celery import shared_task
from .engine.orchestrator import AutomationOrchestrator
from .scheduling import record_scheduled_run


def _schedule_continuation(automation_id):
        """
        The callable the engine uses to bring a paused run back.

        It lives here, outside `engine/`, because dispatching is a Celery concern and
        the engine is not allowed to know Celery exists (tests/test_engine_purity.py).
        The payload is the run's whole execution context, so the continuation resumes
        with the same `steps` and `vars` instead of starting the automation over.
        """
        def schedule(next_step_key,context,delay_seconds = None,eta = None):
                options = {}

                if eta is not None:
                        options['eta'] = eta
                elif delay_seconds:
                        options['countdown'] = delay_seconds

                run_automation.apply_async(
                        args = (str(automation_id),context),
                        kwargs = {'start_step_key' : next_step_key},
                        **options
                )

        return schedule


@shared_task
def run_automation(automation_id ,data ,source = 'manual' ,start_step_key = None):

        # `start_step_key` is only set by a wait step scheduling its own continuation.
        # The plain call stays plain, so no existing caller - or test asserting on it -
        # has to know that resumable runs exist.
        if start_step_key is None:
                result = AutomationOrchestrator().run(
                        automation_id,data,
                        schedule_continuation = _schedule_continuation(automation_id),
                )
        else:
                result = AutomationOrchestrator().run(
                        automation_id,data,
                        start_step_key = start_step_key,
                        schedule_continuation = _schedule_continuation(automation_id),
                )

        # Only beat passes source='scheduled' (see scheduling.sync_periodic_task); a
        # manual or webhook run must not spend a counted schedule's budget.
        if source == 'scheduled':
                record_scheduled_run(automation_id)

        return result
