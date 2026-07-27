from abc import abstractmethod

class BaseAction:

        @abstractmethod
        def execute(self, config) -> bool:
                pass
