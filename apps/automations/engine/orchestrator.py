from collections.abc import Mapping

from ..models import Automation,AutomationLog,AutomationStatus
from .exceptions import EvaluationError,ExecutionError
from .evaluator import ConditionEvaluator
from .executor import AutomationExecutor
from .expressions import build_execution_context

import logging

logger = logging.getLogger(__name__)

# How each graph outcome is recorded. A paused run is 'pending', not 'success': the
# second half of it has not happened yet, and a log that claims otherwise is a log
# nobody can trust when the continuation never fires.
GRAPH_LOG_STATUS = {
        'success' : AutomationStatus.SUCCESS,
        'skipped' : AutomationStatus.SKIPPED,
        'waiting' : AutomationStatus.PENDING,
        'failed' : AutomationStatus.FAILURE,
}


class AutomationOrchestrator:

        def run(self,automation_id,data,start_step_key = None,schedule_continuation = None):
                
                try:
                        logger.info("Automation Started")
                        
                        automation = Automation.objects.get(id = automation_id)


                        if not automation.is_active:
                                AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.FAILURE,
                                        error_message = "Automation is not active",
                                        execution_context = data,
                                        
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
                                AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.SKIPPED,
                                        error_message = "Automation conditions are not satisfied",
                                        execution_context = data,
                                        decision = decision.as_dict(),
                                )
                                return False

                        logger.info("All conditions are satisfied,moving to the next stage")

                        # The graph takes over only where it must - see
                        # _should_execute_graph. A plain chain of action steps keeps
                        # running through execute_actions(), which is the dual-write
                        # promise of ROADMAP section 5.1 in practice.
                        if self._should_execute_graph(automation,start_step_key):
                                return self._run_graph(
                                        automation,data,decision,start_step_key,
                                        schedule_continuation
                                )

                        # ordered actions
                        actions = automation.actions.order_by('order')

                        result = AutomationExecutor().execute_actions(actions,data)

                        if result :
                                logger.info("Automation has completed succesfully")
                                AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.SUCCESS,
                                        execution_context = data,
                                        decision = decision.as_dict()
                                )
                                return True
                        
                        logger.info("Automation has been failed")
                        return False
                        

                
                        


                except Automation.DoesNotExist as e:
                        logger.error("Automation not found")

                except EvaluationError as e:
                        logger.error("An evaluation error happened : " + str(e))
                        AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.FAILURE,
                                        error_message = str(e),
                                        execution_context = data,
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
                        )

                except ExecutionError as e:
                        logger.error("An execution error happened : " + str(e))

                        AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.FAILURE,
                                        error_message = str(e),
                                        execution_context = data,
                        )
                
                except Exception:
                        # logger.error(msg, arg) treats arg as a %-format parameter; with
                        # no placeholder in the message this failed inside logging itself
                        # and swallowed the very error it was meant to report.
                        logger.exception("An unexpected error happened")


        #
        # Graph path (Faz 1.D)
        #

        def _should_execute_graph(self,automation,start_step_key):
                """
                Graph or legacy action list?

                The graph only takes over where the action list cannot follow: a
                resumed run, a step type that is not an action, or a branch edge. An
                automation whose graph is nothing but the linear chain derived from
                its own actions keeps using execute_actions() - the actions API is
                still the documented one (ROADMAP section 5.1), and until the graph
                can do everything it can, it must behave identically.
                """
                if start_step_key is not None:
                        return True

                steps = list(automation.steps.all())

                if not steps:
                        return False

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


        def _run_graph(self,automation,data,decision,start_step_key,schedule_continuation):
                context = self._execution_context(automation,data,start_step_key)

                result = AutomationExecutor().execute_graph(
                        automation.steps.all(),
                        automation.edges.all(),
                        context,
                        evaluator = ConditionEvaluator(),
                        schedule_continuation = schedule_continuation,
                        start_step_key = start_step_key,
                )

                status = GRAPH_LOG_STATUS.get(result.status,AutomationStatus.FAILURE)
                error_message = self._first_error(result)

                if status == AutomationStatus.SUCCESS:
                        logger.info("Automation has completed succesfully")
                elif status == AutomationStatus.PENDING:
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
                elif status == AutomationStatus.SKIPPED:
                        logger.info("Automation stopped at a filter step")
                else:
                        logger.info("Automation has been failed")

                AutomationLog.objects.create(
                        automation = automation,
                        status = status,
                        error_message = error_message,
                        execution_context = result.context,
                        decision = decision.as_dict(),
                )

                # 'waiting' is not a failure: the run did everything it could and the
                # rest is scheduled. The caller gets True, the log says PENDING.
                return status in (AutomationStatus.SUCCESS,AutomationStatus.PENDING)


        def _first_error(self,result):
                for step_result in result.step_results:
                        if step_result.get('status') != 'failed':
                                continue

                        entry = result.context.get('steps',{}).get(step_result.get('key')) or {}
                        return entry.get('error') or ''

                return ''
