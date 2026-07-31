from ..models import AutomationAction,OnFailureChoices
from .exceptions import ExecutionError
from .actions import get_action_class

class AutomationExecutor:

        def _execute_single(self,action : AutomationAction,data):
                
                
                action_cls = get_action_class(action.action_type)
                status = action_cls(action).run(data)
               
                return status


        def execute_actions(self,actions:AutomationAction,data):
                if not actions:
                        raise ExecutionError("No action is found")

                
                for action in actions:
                        try:
                                status = self._execute_single(action,data)
                        except ExecutionError as e:
                                if action.on_failure == OnFailureChoices.RETRY:
                                        max_retries = 3
                                        i = 0
                                        status_retry = False
                                        while (i < 3):
                                                try:
                                                        status_retry = self._execute_single(action,data)
                                                        if status_retry:
                                                                break
                                                
                                                except ExecutionError as e:
                                                        i+=1
                                        if not status_retry:
                                                raise ExecutionError(f"An action ({action.id}) failed on all {max_retries} attempts")       

                                elif action.on_failure == OnFailureChoices.STOP:
                                        return False
                                
                                elif action.on_failure == OnFailureChoices.CONTINUE:
                                        pass

                        
                return True
        


