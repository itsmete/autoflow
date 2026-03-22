from abc import abstractmethod

class BaseProvider:
        @abstractmethod
        def send(self,credentials,payload):
                # credentials -> API KEY, token information coming from Channel model
                #payload -> to, subject, body information coming from Action Model
                pass
