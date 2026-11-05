import logging
import random
import uuid

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from .engine.orchestrator import AutomationOrchestrator
from .models import Automation, AutomationRun, RunMode, RunStatus, WebhookDelivery
from .scheduling import record_scheduled_run


logger = logging.getLogger(__name__)


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

                # A continuation deliberately carries no `retry_attempt`: it is a new
                # leg of the run, not a retry of the leg that paused, so its queue
                # budget starts fresh.
                run_automation.apply_async(
                        args = (str(automation_id),context),
                        kwargs = kwargs,
                        **options
                )

        return schedule


def retry_countdown(retry_attempt,base_seconds = None,max_seconds = None,jitter = None):
        """
        Seconds to wait before re-dispatching a failed run.

        Exponential: `base * 2**retry_attempt`, capped at `max_seconds`, then jittered
        by +/- `jitter` of the delay. Parameterised rather than reading settings
        directly so the shape can be asserted without touching the clock or the
        environment; the task passes the configured values.

        The final clamp is not cosmetic: jitter on a capped delay could otherwise
        produce `max * 1.25`, and a negative jitter could produce a negative countdown.
        """
        if base_seconds is None:
                base_seconds = getattr(settings,'AUTOFLOW_RUN_RETRY_BASE_SECONDS',30)
        if max_seconds is None:
                max_seconds = getattr(settings,'AUTOFLOW_RUN_RETRY_MAX_SECONDS',3600)
        if jitter is None:
                jitter = getattr(settings,'AUTOFLOW_RUN_RETRY_JITTER',0.25)

        try:
                attempt = max(0,int(retry_attempt))
        except (TypeError,ValueError):
                attempt = 0

        delay = float(base_seconds) * (2 ** attempt)
        delay = min(delay,float(max_seconds))

        if jitter:
                delay *= 1 + random.uniform(-float(jitter),float(jitter))

        return max(0.0,min(delay,float(max_seconds)))


def _attempt_run(automation_id,run_id,new_run_id):
        """
        The row this attempt wrote to, or None when it never created one.

        Every lookup is scoped to `automation_id`: a run id is not a capability, and a
        failed run from automation A must never be mutated or announced through
        automation B. The explicit `run_id` is tried first (a real resume); the
        pre-minted `new_run_id` second (the row the runtime created on that lookup
        miss). A malformed id is "no run", the same answer the runtime gives it.
        """
        for identifier in (run_id,new_run_id):
                if identifier is None:
                        continue

                try:
                        run = AutomationRun.objects.filter(
                                id = identifier,automation_id = automation_id,
                        ).first()
                except (TypeError,ValueError,ValidationError):
                        continue

                if run is not None:
                        return run

        return None


def _schedule_retry(automation_id,data,source,start_step_key,run,retry_attempt):
        """
        Re-dispatch the SAME run after a retryable failure.

        Returns True when the retry left. The row is marked QUEUED before the dispatch
        so a reader cannot mistake a run that is about to be retried for a finished
        one. Crash window: a process that dies between that write and `apply_async`
        leaves the run QUEUED with nothing coming. Closing it first is not an option -
        a failed dispatch then looks RUNNING - and a full outbox/recovery scheduler is
        out of scope for this task.
        """
        kwargs = {
                'run_id' : str(run.id),
                # The row is the authority on the mode; a retry must not relabel a
                # test run live (or the reverse) by falling back to the task default.
                'mode' : run.mode,
                'source' : source,
                'retry_attempt' : retry_attempt + 1,
        }

        # A wait continuation carries its whole context and the step to resume from.
        # Dropping either would make the retry rebuild the context from
        # trigger_payload and start the graph over.
        if start_step_key is not None:
                kwargs['start_step_key'] = start_step_key

        AutomationRun.objects.filter(
                pk = run.pk,automation_id = automation_id,
        ).update(
                status = RunStatus.QUEUED,
                finished_at = None,
                updated_at = timezone.now(),
        )

        try:
                run_automation.apply_async(
                        args = (str(automation_id),data),
                        kwargs = kwargs,
                        countdown = retry_countdown(retry_attempt),
                )
        except Exception:
                # `logger.error(msg, arg)` would treat the argument as a %-format
                # parameter and fail inside logging; logger.exception reports the
                # dispatch failure that made the retry impossible.
                logger.exception(
                        "Failed to queue retry %s of run %s",retry_attempt + 1,run.id,
                )
                return False

        return True


def _finish_failed_permanently(run,automation_id):
        """
        No further attempt is coming: the run is terminally failed.

        A targeted update, not a whole-row save: the orchestrator already wrote the
        error, context and decision that explain the failure, and a stale in-memory
        instance must not overwrite them. Scoped by automation as defence in depth -
        even a row object obtained by mistake cannot be closed through another
        automation.
        """
        now = timezone.now()

        AutomationRun.objects.filter(
                pk = run.pk,automation_id = automation_id,
        ).update(
                status = RunStatus.FAILED_PERMANENTLY,
                finished_at = now,
                updated_at = now,
        )

        run.status = RunStatus.FAILED_PERMANENTLY
        run.finished_at = now


def _notify_final_failure(automation,run):
        """
        Emit the one terminal failure fan-out the orchestrator was told to suppress.

        Only called when a retry was believed possible (`notify_failure=False`), so it
        is the task that owes the followers the announcement when the retry never
        leaves. Inactive automations and dry runs still announce nothing, exactly as
        the orchestrator's own `_record` would have.

        The automation check is defence in depth: a run from another automation must
        never be announced through this one, however it was obtained.
        """
        if automation is None or not automation.is_active:
                return

        if str(run.automation_id) != str(automation.id):
                return

        if run.mode == RunMode.TEST:
                return

        # Lazy: runtime imports this module at module scope, so a top-level import
        # here would be a cycle.
        from . import runtime

        runtime.notify_automation_completed(automation,'failure',run.context or {})


@shared_task
def run_automation(automation_id ,data ,source = 'manual' ,start_step_key = None ,
                run_id = None ,mode = 'live' ,retry_attempt = 0):

        # `start_step_key` is only set by a wait step scheduling its own continuation.
        # `run_id` is set by the same continuation, by an explicit recovery of a
        # crashed run, and by a queue retry. The plain call stays plain, so no
        # existing caller - or test asserting on it - has to know that resumable runs
        # or retries exist.
        #
        # `mode` is set by a dry run (`RunMode.TEST`) and by a replay of one, and it
        # defaults to 'live' so `run_automation(str(automation.id), payload)` - the
        # call every existing caller and test makes - still means the same thing.
        #
        # `retry_attempt` is internal: no API exposes it, and a fresh dispatch always
        # starts at 0.
        try:
                retry_attempt = max(0,int(retry_attempt))
        except (TypeError,ValueError):
                retry_attempt = 0

        # A plain dispatch cannot know the run it is about to open, but a retry has to
        # name the SAME row or every memoized step is thrown away. Mint the id here and
        # hand it to the runtime as `new_run_id`. It is minted even when an explicit
        # run_id was supplied: if that id misses (unknown, malformed, another
        # automation's), the runtime creates the fresh row WITH this id, so the failure
        # can still be retried and closed instead of being orphaned.
        new_run_id = str(uuid.uuid4())

        max_retries = getattr(settings,'AUTOFLOW_RUN_RETRY_MAX_RETRIES',3)

        # Only the queue budget decides whether another attempt is possible. The mode
        # is deliberately not read from the caller: once the orchestrator has started,
        # the persisted run.mode is the authority, and a stale/default task argument
        # must not turn a LIVE run into an early permanent failure.
        may_retry = retry_attempt < max_retries

        options = {
                'schedule_continuation' : _schedule_continuation(automation_id),
                'run_id' : run_id,
                'new_run_id' : new_run_id,
                'mode' : mode,
                'notify_failure' : not may_retry,
        }

        if start_step_key is None:
                result = AutomationOrchestrator().run(automation_id,data,**options)
        else:
                result = AutomationOrchestrator().run(
                        automation_id,data,
                        start_step_key = start_step_key,
                        **options
                )

        run = _attempt_run(automation_id,run_id,new_run_id)
        retried = False

        if run is not None and run.status == RunStatus.FAILED \
                        and run.mode != RunMode.TEST:
                automation = Automation.objects.filter(id = automation_id).first()

                # Retry only a row that exists, is FAILED, belongs to a live active
                # automation and still has budget. A missing automation or a dry run
                # has no queue lifecycle.
                retryable = (
                        may_retry
                        and automation is not None
                        and automation.is_active
                )

                if retryable:
                        retried = _schedule_retry(
                                automation_id,data,source,start_step_key,run,retry_attempt,
                        )

                        if not retried:
                                # The failure fan-out was suppressed for a retry that
                                # never left; close the run for good and say so once.
                                _finish_failed_permanently(run,automation_id)
                                _notify_final_failure(automation,run)
                else:
                        _finish_failed_permanently(run,automation_id)

                        # `may_retry` means the orchestrator suppressed the failure
                        # fan-out on our behalf. If we are not retrying after all, it
                        # is ours to emit - exactly once.
                        if may_retry:
                                _notify_final_failure(automation,run)

        # A beat-driven run spends its repeat budget on the final return, not once per
        # queue retry: the retry keeps source='scheduled' and would otherwise spend it
        # again (and again).
        if source == 'scheduled' and not retried:
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
