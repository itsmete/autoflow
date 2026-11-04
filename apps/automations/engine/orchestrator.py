from ..models import Automation,AutomationLog,AutomationStatus
from .exceptions import EvaluationError,ExecutionError
from .evaluator import ConditionEvaluator
from .executor import AutomationExecutor

import logging

logger = logging.getLogger(__name__)


class AutomationOrchestrator:

        def run(self,automation_id,data):
                
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

                        