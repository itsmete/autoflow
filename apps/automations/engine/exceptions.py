# Exceptions for logic layer.


class EngineError(Exception):
        def __init__(self,message,*args):
                self.message = message
                super().__init__(message,*args)


class ExecutionError(EngineError):
        pass

class EvaluationError(EngineError):
        pass


