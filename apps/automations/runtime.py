"""
The Django adapter the engine talks to.

`apps/automations/engine/` is meant to be a framework-free execution core that a
second consumer (a single-user local orchestrator, ROADMAP section 7) can share.
Everything the engine genuinely cannot do without a framework - read a row, write a
log, dispatch a task, resolve a `post_save` - lives here instead, and the engine
reaches it through `importlib.import_module('apps.automations.runtime')`
(tests/test_engine_purity.py enforces the direction of the dependency).

Two rules for this module:

  * import whatever it likes - django, DRF, models, celery. It is outside the engine.
  * do not be imported at module scope by the engine. A lazy import keeps `engine/`
    importable without a configured Django project, which is the whole point.
"""
import json
from collections.abc import Mapping

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save
from django.forms.models import model_to_dict
from django.utils import timezone
from rest_framework.utils.encoders import JSONEncoder

from .models import (
        Automation,
        AutomationLog,
        AutomationRun,
        AutomationStep,
        AutomationStepRun,
        AutomationTrigger,
        RunStatus,
        RunMode,
        StepRunStatus,
        TriggerType,
)
from .tasks import run_automation

import logging


logger = logging.getLogger(__name__)


# How many `automation_completed` hops a single chain may take. A -> B -> A is a
# perfectly legal pair of triggers and an infinite loop at the same time; the depth
# is carried in the payload, so it survives the Celery boundary.
MAX_CHAIN_DEPTH = 5

# The engine's own name for "this row is not there", so it can report it without
# importing an ORM exception class.
AutomationNotFound = Automation.DoesNotExist

DJANGO_SIGNALS = {
        'post_save' : post_save,
        'post_delete' : post_delete,
}


#
# What the engine asks of us
#

def get_automation(automation_id):
        return Automation.objects.get(id = automation_id)


def create_log(automation,status,error_message = None,execution_context = None,decision = None):
        return AutomationLog.objects.create(
                automation = automation,
                status = status,
                error_message = error_message or '',
                execution_context = execution_context if execution_context is not None else {},
                decision = decision,
        )


def get_django_signal(signal_name):
        return DJANGO_SIGNALS.get(signal_name)


#
# Run persistence (Faz 2.A)
#
# `AutomationLog` is the summary the API reads, and it keeps being written exactly as
# before. `AutomationRun` is the durable identity of one attempt, and
# `AutomationStepRun` is why a crashed run can be resumed instead of restarted: the
# executor is handed the outputs already achieved and skips those steps (ROADMAP
# section 5.2). Both live here because the engine is not allowed to touch the ORM.
#

# A status after which nothing is coming for this run. 'waiting' is deliberately not
# here: a continuation still owns the rest of it, and stamping finished_at would make
# a paused run look like a closed one.
TERMINAL_RUN_STATUSES = frozenset({
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.FAILED_PERMANENTLY,
        RunStatus.SKIPPED,
        RunStatus.CANCELLED,
})


def completed_steps_from_run(run):
        """
        `{step_key: {'status': 'success', 'output': ..., 'decision': ...}}`.

        Only `success` rows are memoizable. A failed step must be attempted again -
        skipping it would let a retry "succeed" without ever having done the work.
        """
        steps = {}

        for step_run in run.step_runs.filter(status = StepRunStatus.SUCCESS):
                steps[step_run.step_key] = {
                        'status' : 'success',
                        'output' : step_run.output,
                        'decision' : step_run.decision,
                }

        return steps


def start_or_resume_run(automation,data,*,run_id = None,mode = 'live'):
        """
        The run this execution belongs to: the one it was asked to resume, or a new one.

        Returns `(run, completed_steps)`. A non-empty `completed_steps` is the signal
        the orchestrator uses to take the graph path even for an automation that
        would otherwise run through the legacy action list - a half-done run can only
        be resumed step by step.
        """
        run = None

        if run_id is not None:
                try:
                        run = AutomationRun.objects.filter(
                                id = run_id,automation = automation,
                        ).first()
                except (TypeError,ValueError,ValidationError):
                        # A malformed id is not a crash: it means "no such run", and
                        # the honest answer is a fresh one. UUIDField.to_python raises
                        # Django's ValidationError, not ValueError.
                        run = None

        if run is not None:
                if run.started_at is None:
                        run.started_at = timezone.now()

                run.status = RunStatus.RUNNING
                run.save(update_fields = ['status','started_at','updated_at'])

                return run,completed_steps_from_run(run)

        if mode not in RunMode.values:
                mode = RunMode.LIVE

        run = AutomationRun.objects.create(
                automation = automation,
                status = RunStatus.RUNNING,
                mode = mode,
                trigger_payload = dict(data) if isinstance(data,Mapping) else {},
                started_at = timezone.now(),
        )

        return run,{}


def record_step_run(run,step,step_result):
        """
        Persist one step of `run`.

        Keyed by `(run, step_key)` rather than appended: a retried step must update
        its own row (attempt, duration, final status), not leave a trail of one row
        per try that a reader would have to reconcile. `update_or_create` also means a
        resumed run that re-executes a step cannot duplicate its history.
        """
        step_key = str(step_result.get('key') or '')

        defaults = {
                'step' : step if isinstance(step,AutomationStep) else None,
                'step_type' : step_result.get('step_type') or 'action',
                'status' : step_result.get('status') or StepRunStatus.RUNNING,
                'attempt' : step_result.get('attempt') or 1,
                'input' : step_result.get('input') or {},
                'output' : step_result.get('output'),
                'decision' : step_result.get('decision'),
                'error' : step_result.get('error') or '',
                'duration_ms' : step_result.get('duration_ms'),
        }

        step_run,_created = AutomationStepRun.objects.update_or_create(
                run = run,step_key = step_key,defaults = defaults,
        )

        return step_run


def finish_run(run,*,status,context = None,decision = None,error_message = ''):
        """
        Close (or park) a run. Returns the row, saved.
        """
        run.status = status

        if context is not None:
                run.context = context if isinstance(context,Mapping) else {}

        if decision is not None:
                run.decision = decision

        run.error_message = error_message or ''
        run.finished_at = timezone.now() if status in TERMINAL_RUN_STATUSES else None
        run.save()

        return run


def serialize_model_instance(instance):
        """
        A model instance as a JSON-safe dict.

        model_to_dict returns live python objects - UUIDs, dates, Decimals - and
        CELERY_TASK_SERIALIZER is 'json', so handing them straight to .delay() raises
        "Object of type UUID is not JSON serializable" and the automation never runs.
        Round-tripping through DRF's encoder produces the same payload the API would
        have sent.
        """
        if instance is None:
                return None

        return json.loads(json.dumps(model_to_dict(instance),cls = JSONEncoder))


def resolve_model(model_name):
        if not model_name or '.' not in model_name:
                raise LookupError(f"Invalid model path: {model_name}")
        return apps.get_model(model_name)


def dispatch_automation(automation_id,data,start_step_key = None):
        """
        Celery's JSON serializer means the pk must be a string before it is queued.
        """
        if start_step_key is None:
                return run_automation.delay(str(automation_id),data)

        return run_automation.apply_async(
                args = (str(automation_id),data),
                kwargs = {'start_step_key' : start_step_key},
        )


#
# automation_completed fan-out
#

def chain_depth(execution_context):
        """
        How many `automation_completed` hops got us here.

        It is read from the top level of the context (the chained payload is copied
        there by build_execution_context) and from `trigger` (the namespaced copy),
        so a run that took either path counts its hops.
        """
        if not isinstance(execution_context,Mapping):
                return 0

        depth = execution_context.get('_chain_depth')

        if depth is None:
                trigger = execution_context.get('trigger')
                if isinstance(trigger,Mapping):
                        depth = trigger.get('_chain_depth')

        try:
                # max(0, ...) is not cosmetic: a negative depth read as-is would make
                # `depth >= MAX_CHAIN_DEPTH` false for every hop, and a tenant-authored
                # payload ({"_chain_depth": -999999}) would turn a legal A -> B loop
                # into an unbounded fan-out. A depth below zero is nonsense; treat it
                # as the bottom of the chain.
                return max(0,int(depth or 0))
        except (TypeError,ValueError):
                # A tenant-authored payload can put anything here. A depth we cannot
                # read is 0 - refusing to fan out on garbage would silently break
                # chaining, and trusting it as-is would break the guard.
                return 0


def notify_automation_completed(automation,status,execution_context):
        """
        Fan a finished run out to the automations that asked to follow it.

        Returns how many downstream runs were queued; 0 is normal and is not an error.
        """
        depth = chain_depth(execution_context)

        if depth >= MAX_CHAIN_DEPTH:
                logger.warning(
                        "automation_completed chain from %s stopped at depth %s (max %s)",
                        automation.id,depth,MAX_CHAIN_DEPTH,
                )
                return 0

        triggers = AutomationTrigger.objects.filter(
                trigger_type = TriggerType.AUTOMATION_COMPLETED,
                automation__is_active = True,
                # A trigger may only name a source automation in its OWN tenant. The
                # row references the source by id in a JSON config, so there is no
                # foreign key to enforce that for us.
                automation__tenant_id = automation.tenant_id,
        )

        dispatched = 0

        for trigger in triggers:
                config = trigger.config or {}

                if config.get('source_automation_id') != str(automation.id):
                        continue

                # `success` is the default the serializer writes, and the default the
                # reader assumes for rows that predate it.
                if config.get('trigger_on_status','success') != status:
                        continue

                payload = {
                        'source_automation_id' : str(automation.id),
                        'source_automation_name' : automation.name,
                        'status' : status,
                        'upstream_context' : execution_context,
                        '_chain_depth' : depth + 1,
                }

                dispatch_automation(trigger.automation_id,payload)
                dispatched += 1

        return dispatched
