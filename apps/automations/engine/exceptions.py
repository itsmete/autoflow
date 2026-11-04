# Exceptions for logic layer.


class EngineError(Exception):
        def __init__(self,message,*args):
                self.message = message
                super().__init__(message,*args)


class ExecutionError(EngineError):
        pass

class EvaluationError(EngineError):

        def __init__(self,message,*args,decision = None):
                # Carries the partial decision trace when one is available. A run that
                # died mid-evaluation is precisely the run whose reasoning someone will
                # want to read, and losing it to the raise would waste the trace.
                self.decision = decision
                super().__init__(message,*args)


class TemplateRenderError(EngineError):
        pass