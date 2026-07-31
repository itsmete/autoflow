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
                        conditions = automation.conditions.all()
                        
                        if not ( ConditionEvaluator().evaluate_conditions(conditions,data)):
                                logger.info("Automation conditions are not satisfied, exiting")
                                AutomationLog.objects.create(
                                        automation = automation,
                                        status = AutomationStatus.SKIPPED,
                                        error_message = "Automation conditions are not satisfied",
                                        execution_context = data,
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
                                        execution_context = data
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

                        