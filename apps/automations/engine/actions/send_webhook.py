from .base import BaseAction
from ..exceptions import ExecutionError
import requests


class SendWebhook(BaseAction):
        
        def execute(self,config):
                method = config.get('method')
                url = config.get('url')
                headers = config.get('headers')
                payload = config.get('payload')

                
                try :
                        requests.request(
                        method=method,
                        headers=headers,
                        url=url,
                        json = payload
                        )
                except Exception as e:
                        raise ExecutionError(f"An error occured while sending a webhook to {url} [{method}]")

                return True                

