import importlib
import logging

from collections.abc import Mapping

from .exceptions import EvaluationError,ExecutionError
from .evaluator import ConditionEvaluator
from .executor import AutomationExecutor
from .expressions import build_execution_context


logger = logging.getLogger(__name__)

# The run's own vocabulary for how it ended. These are plain strings, not
# AutomationStatus members: reading the values off the model would mean importing it,
# and the engine is not allowed to know what an Automation is
# (tests/test_engine_purity.py). They match AutomationStatus to the letter, which is
# checked by the tests that read the log back.
STATUS_SUCCESS = 'success'
STATUS_FAILURE = 'failure'
STATUS_PENDING = 'pending'
STATUS_SKIPPED = 'skipped'

# Statuses worth telling the rest of the system about. A paused run is not one of
# them: the second half of it has not happened yet.
TERMINAL_STATUSES = frozenset({STATUS_SUCCESS,STATUS_FAILURE,STATUS_SKIPPED})

# How each graph outcome is recorded. A paused run is 'pending', not 'success': the
# second half of it has not happened yet, and a log that claims otherwise is a log
# nobody can trust when the continuation never fires.
GRAPH_LOG_STATUS = {
        'success' : STATUS_SUCCESS,
        'skipped' : STATUS_SKIPPED,
        'waiting' : STATUS_PENDING,
        'failed' : STATUS_FAILURE,
}

# The log vocabulary is 'failure'; a run's own state machine says 'failed'. Mapped
# here rather than imported from models.RunStatus: the engine stays a plain-string
# consumer (tests/test_engine_purity.py), and the values are checked by the tests
# that read the rows back.
RUN_STATUS = {
        STATUS_SUCCESS : 'success',
        STATUS_FAILURE : 'failed',
        STATUS_PENDING : 'waiting',
        STATUS_SKIPPED : 'skipped',
}


def _default_runtime():
        # Lazy on purpose: `apps.automations.runtime` imports the ORM, celery and
        # Django, and the engine must stay importable without any of them.
        return importlib.import_module('apps.automations.runtime')


class AutomationOrchestrator:

        def __init__(self,runtime = None):
                # The adapter is injectable so a test - or the second consumer the
                # engine is being extracted for - can supply its own persistence.
                self._runtime = runtime

        @property
        def runtime(self):
                if self._runtime is None:
                        self._runtime = _default_runtime()
                return self._runtime

        def run(self,automation_id,data,start_step_key = None,schedule_continuation = None,
                run_id = None,mode = 'live',replay_of_id = None):

                runtime = self.runtime
                run = None
                # Bound before the try: the catch-all below must be able to ask "is
                # there an automation to close this run around" without an
                # UnboundLocalError masking the error that got us there.
                automation = None

                try:
                        logger.info("Automation Started")

                        try:
                                automation = runtime.get_automation(automation_id)
                        except runtime.AutomationNotFound:
                                logger.error("Automation not found")
                                return None

                        # One durable identity per attempt (ROADMAP section 5.2). With a
                        # run_id this resumes that run and hands back what it already
                        # completed; without one it opens a new run. AutomationLog is
                        # still written below - it stays the API's summary.
                        #
                        # `mode` travels all the way down: a dry run must not have an
                        # effect anywhere, and "anywhere" includes the fan-out at the
                        # end of this method.
                        run,completed_steps = runtime.start_or_resume_run(
                                automation,data,run_id = run_id,mode = mode,
                                replay_of_id = replay_of_id,
                        )

                        # The row is the record of what this attempt IS. `mode` can
                        # arrive as the task default ('live'), from a continuation
                        # that did not carry one, or from a caller that changed its
                        # mind; none of those may relabel a dry run as live. A newly
                        # created run was built from `mode`, so this is a no-op for
                        # the first attempt and only matters on a resume.
                        mode = run.mode

                        # Explicit crash recovery: `run_id` without a continuation's
                        # `start_step_key` means "finish the attempt that started with
                        # this payload", not "run this automation with whatever the
                        # retry sent this time". A changed payload would evaluate the
                        # conditions and render the templates against data the run
                        # never saw. A continuation is different: it carries the whole
                        # context and must keep using it.
                        if run_id is not None and start_step_key is None:
                                persisted = run.trigger_payload
                                data = persisted if isinstance(persisted,Mapping) else {}

                        if not automation.is_active:
                                self._record(
                                        runtime,automation,STATUS_FAILURE,data,
                                        error_message = "Automation is not active",
                                        # A disabled automation cannot start a chain;
                                        # saying otherwise would resurrect it through
                                        # the back door.
                                        notify = False,
                                        run = run,
                                        mode = mode,
                                )
                                return False


                        # AutomationCondition declares related_name='conditions', so the
                        # default automationcondition_set accessor does not exist and
                        # raised AttributeError on every run.
                        # AutomationCondition.Meta declares ordering, which grouping
                        # depends on: 'or' opens a new AND-group, so the meaning of the
                        # set is a function of the order it is read in.
                        conditions = automation.conditions.all()

                        decision = ConditionEvaluator().evaluate_conditions(conditions,data)

                        if not decision:
                                logger.info("Automation conditions are not satisfied, exiting")
                                self._record(
                                        runtime,automation,STATUS_SKIPPED,data,
                                        error_message = "Automation conditions are not satisfied",
                                        decision = decision.as_dict(),
                                        run = run,
                                        mode = mode,
                                )
                                return False

                        logger.info("All conditions are satisfied,moving to the next stage")

                        # The graph takes over only where it must - see
                        # _should_execute_graph. A plain chain of action steps keeps
                        # running through execute_actions(), which is the dual-write
                        # promise of ROADMAP section 5.1 in practice. A run that already
                        # has successful steps is the exception: only the graph can skip
                        # them.
                        if self._should_execute_graph(automation,start_step_key,completed_steps):
                                return self._run_graph(
                                        runtime,automation,data,decision,start_step_key,
                                        schedule_continuation,run = run,
                                        completed_steps = completed_steps,
                                        mode = mode,
                                )

                        # ordered actions. The same `(order, created_at)` order
                        # sync_steps_from_actions() numbers the derived steps in, so
                        # the legacy step keys and the graph's keys agree.
                        actions = automation.actions.order_by('order','created_at')

                        result = AutomationExecutor().execute_actions(
                                actions,data,
                                on_action_complete = self._step_recorder(runtime,run),
                                mode = mode,
                                # A resumed legacy run finishes what is left: the
                                # actions whose step run already says success are
                                # skipped instead of sent a second time.
                                completed_steps = completed_steps,
                        )

                        if result :
                                logger.info("Automation has completed succesfully")
                                self._record(
                                        runtime,automation,STATUS_SUCCESS,data,
                                        decision = decision.as_dict(),
                                        run = run,
                                        mode = mode,
                                )
                                return True

                        logger.info("Automation has been failed")
                        # No log on this path (unchanged), but the run must not be
                        # left saying 'running' forever.
                        if run is not None:
                                runtime.finish_run(run,status = RUN_STATUS[STATUS_FAILURE],context = data)

                        return False



                except EvaluationError as e:
                        logger.error("An evaluation error happened : " + str(e))
                        self._record(
                                runtime,automation,STATUS_FAILURE,data,
                                error_message = str(e),
                                # The evaluator hands over whatever it managed to
                                # decide before it gave up; that partial trace is
                                # the most useful one there is.
                                #
                                # `is not None`, not a truth test: Decision.__bool__
                                # reports the RESULT, and a partial trace from a
                                # failed run always carries result=False. Testing
                                # it for truth threw away the very thing being
                                # rescued.
                                decision = e.decision.as_dict() if e.decision is not None else None,
                                run = run,
                                mode = mode,
                        )

                except ExecutionError as e:
                        logger.error("An execution error happened : " + str(e))

                        # The step that raised was already recorded by
                        # on_step_complete (a step run is written even when `retry`
                        # exhausts its attempts); this closes the run around it.
                        self._record(
                                runtime,automation,STATUS_FAILURE,data,
                                error_message = str(e),
                                run = run,
                                mode = mode,
                        )

                except Exception as error:
                        # logger.error(msg, arg) treats arg as a %-format parameter; with
                        # no placeholder in the message this failed inside logging itself
                        # and swallowed the very error it was meant to report.
                        logger.exception("An unexpected error happened")

                        # A run left saying 'running' forever is worse than a failed
                        # one: nothing will ever pick it up again. Close it through the
                        # same path every other failure takes - but only when there is
                        # an automation and a run to close it around.
                        if run is not None and automation is not None:
                                try:
                                        self._record(
                                                runtime,automation,STATUS_FAILURE,data,
                                                error_message = "An unexpected error happened: %s" % (error,),
                                                run = run,
                                                mode = mode,
                                        )
                                except Exception:
                                        # Closing the run must never mask the error
                                        # that caused it; the traceback above is the
                                        # one someone needs.
                                        logger.exception("Failed to close a run after an unexpected error")


        def _record(self,runtime,automation,status,execution_context,error_message = None,
                        decision = None,notify = True,run = None,mode = 'live'):
                """
                Write the run's log and, for a finished run, tell the followers.

                Chaining and logging share this one exit on purpose: a status written
                to the log and a status fanned out to `automation_completed` triggers
                drifting apart is exactly the kind of bug nobody sees, because both
                sides look fine on their own.

                `run` is the AutomationRun the log summarizes. It is finalized here for
                the same reason: two statuses written on two exits is two chances to
                disagree.

                A dry run never fans out (`mode != 'test'` below): a test of automation
                A must not start the LIVE automation B that follows it. That is the one
                place where "no effect" costs something other than a provider call, and
                it is the one that would actually hurt.
                """
                runtime.create_log(
                        automation = automation,
                        status = status,
                        error_message = error_message,
                        execution_context = execution_context,
                        decision = decision,
                )

                if run is not None:
                        runtime.finish_run(
                                run,
                                status = RUN_STATUS.get(status,'failed'),
                                context = execution_context,
                                decision = decision,
                                error_message = error_message or '',
                        )

                if notify and mode != 'test' and status in TERMINAL_STATUSES \
                                and automation.is_active:
                        runtime.notify_automation_completed(automation,status,execution_context)


        def _step_recorder(self,runtime,run):
                """
                The callback the executor calls once per finished step.

                Returns None when there is no run to write to, so the caller can pass
                it unconditionally.
                """
                if run is None:
                        return None

                def record(step,step_result):
                        runtime.record_step_run(run,step,step_result)

                return record


        #
        # Graph path (Faz 1.D)
        #

        def _should_execute_graph(self,automation,start_step_key,completed_steps = None):
                """
                Graph or legacy action list?

                The graph only takes over where the action list cannot follow: a
                resumed run, a step type that is not an action, or a branch edge. An
                automation whose graph is nothing but the linear chain derived from
                its own actions keeps using execute_actions() - the actions API is
                still the documented one (ROADMAP section 5.1), and until the graph
                can do everything it can, it must behave identically.

                A run with completed steps and a graph takes the graph: memoization is
                the one thing the flat list cannot do, and it is the reason the run
                was resumed instead of restarted. A run with completed steps but NO
                steps at all is the opposite case - it is a legacy automation whose
                history lives on its actions, and walking an empty graph would report
                a success for work that never happened. execute_actions() skips those
                actions itself.
                """
                if start_step_key is not None:
                        return True

                steps = list(automation.steps.all())

                if not steps:
                        return False

                if completed_steps:
                        return True

                if any((step.step_type or 'action') != 'action' for step in steps):
                        return True

                if automation.edges.exclude(branch_key = '').exists():
                        return True

                # No action rows at all: there is nothing for the legacy executor to
                # run, so the graph is the only thing that can execute this automation.
                return not automation.actions.exists()


        def _execution_context(self,automation,data,start_step_key):
                """
                The context the graph resolves expressions against (ROADMAP 4.5).

                A resumed run is different from a first run: it is handed the context
                the paused run left behind - steps, vars and all - so it must not be
                rebuilt from itself, which would bury the old run inside `trigger`.
                """
                if start_step_key is not None and isinstance(data,Mapping) \
                                and 'trigger' in data and 'steps' in data:
                        context = dict(data)
                        context['steps'] = dict(context.get('steps') or {})
                        context['vars'] = dict(context.get('vars') or {})
                        return context

                return build_execution_context(data,automation)


        def _run_graph(self,runtime,automation,data,decision,start_step_key,schedule_continuation,
                        run = None,completed_steps = None,mode = 'live'):
                context = self._execution_context(automation,data,start_step_key)

                result = AutomationExecutor().execute_graph(
                        automation.steps.all(),
                        automation.edges.all(),
                        context,
                        evaluator = ConditionEvaluator(),
                        # The continuation must come back as the SAME run, otherwise a
                        # resumed run would start from a blank history and lose the
                        # steps it already paid for.
                        schedule_continuation = self._continuation_for(schedule_continuation,run),
                        start_step_key = start_step_key,
                        completed_steps = completed_steps,
                        on_step_complete = self._step_recorder(runtime,run),
                        # A dry run simulates waits instead of parking the run, so the
                        # whole graph is walked in one pass and nothing is scheduled.
                        mode = mode,
                )

                status = GRAPH_LOG_STATUS.get(result.status,STATUS_FAILURE)
                error_message = self._first_error(result)

                if status == STATUS_SUCCESS:
                        logger.info("Automation has completed succesfully")
                elif status == STATUS_PENDING:
                        logger.info(
                                "Automation is waiting until %s, resuming from step %s",
                                result.waiting_until,
                                result.resume_from_step_key,
                        )

                        if schedule_continuation is None:
                                # Nothing will bring this run back. Loud, because the
                                # log now says 'pending' and that is someone's problem.
                                logger.warning(
                                        "A wait step paused automation %s but no continuation "
                                        "scheduler was supplied",
                                        automation.id,
                                )
                elif status == STATUS_SKIPPED:
                        logger.info("Automation stopped at a filter step")
                else:
                        logger.info("Automation has been failed")

                self._record(
                        runtime,automation,status,result.context,
                        error_message = error_message,
                        decision = decision.as_dict(),
                        run = run,
                        mode = mode,
                )

                # 'waiting' is not a failure: the run did everything it could and the
                # rest is scheduled. The caller gets True, the log says PENDING.
                return status in (STATUS_SUCCESS,STATUS_PENDING)


        def _continuation_for(self,schedule_continuation,run):
                """
                The continuation, told which run it is bringing back.

                A wrapper rather than an extra argument to the executor: the engine has
                no business knowing what a run row is, but it does need the scheduler
                it was handed to learn the id (ROADMAP section 5.2, memoization).
                """
                if schedule_continuation is None or run is None:
                        return schedule_continuation

                run_id = str(run.id)

                def schedule(next_step_key,context,delay_seconds = None,eta = None):
                        return schedule_continuation(
                                next_step_key,context,
                                delay_seconds = delay_seconds,
                                eta = eta,
                                run_id = run_id,
                        )

                return schedule


        def _first_error(self,result):
                for step_result in result.step_results:
                        if step_result.get('status') != 'failed':
                                continue

                        entry = result.context.get('steps',{}).get(step_result.get('key')) or {}
                        return entry.get('error') or ''

                return ''
