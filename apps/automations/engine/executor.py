from ..models import AutomationAction,OnFailureChoices
from .exceptions import ExecutionError
from .actions import get_action_class

class AutomationExecutor:

        MAX_RETRIES = 3

        def _execute_single(self,action : AutomationAction,data):
                
                
                action_cls = get_action_class(action.action_type)
                status = action_cls(action).run(data)
               
                return status


        def execute_actions(self,actions:AutomationAction,data):
                if not actions:
                        raise ExecutionError("No action is found")

                
                for action in actions:
                        try:
                                self._execute_single(action,data)
                        except ExecutionError:
                                if action.on_failure == OnFailureChoices.RETRY:
                                        self._retry(action,data)

                                elif action.on_failure == OnFailureChoices.STOP:
                                        return False

                                elif action.on_failure == OnFailureChoices.CONTINUE:
                                        pass


                return True


        def _retry(self,action,data):
                """
                Re-run a failed action up to MAX_RETRIES times.

                The counter has to advance on EVERY attempt, not only on the ones that
                raise. The original loop only incremented inside `except`, so an action
                that returned falsy without raising spun forever and hung the worker.
                """
                for _attempt in range(self.MAX_RETRIES):
                        try:
                                if self._execute_single(action,data):
                                        return True
                        except ExecutionError:
                                continue

                raise ExecutionError(
                        f"An action ({action.id}) failed on all {self.MAX_RETRIES} attempts"
                )
        


