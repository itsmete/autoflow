from .base import BaseAction
from ..exceptions import ExecutionError
import requests


class SendWebhook(BaseAction):
        requires_channel = False
        
        def _execute_without_channel(self,config):
                method = config.get('method')
                url = config.get('url')
                headers = config.get('headers')
                payload = config.get('payload')
                # A webhook that never answers must not hold the run open; the same
                # default the http_request action uses.
                timeout = config.get('timeout', 10)

                
                try :
                        requests.request(
                        method=method,
                        headers=headers,
                        url=url,
                        json = payload,
                        timeout = timeout
                        )
                except Exception as e:
                        raise ExecutionError(f"An error occured while sending a webhook to {url} [{method}]")

                return True                

