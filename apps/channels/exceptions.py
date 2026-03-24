"""
Channel raises its own exceptions, and the action calls the channel provider 
converts it to ExecutionError , so that Orchestrator still listens to only 
its exceptions (Execution, Evaluation).


"""
class ChannelError(Exception):
        def __init__(self,message,*args):
                self.message = message
                super().__init__(message,*args)