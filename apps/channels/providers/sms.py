"""
api_key / account_sid -> authentication
auth_token -> pass/token
from_number -> sender phone number
api_url -> provider endpoint


"""

from .base import BaseProvider
from ..exceptions import ChannelError
from .validators import validate_phone_number
import requests

"""
        example for body_template
        {
                "phone": "{to}",
                "sender": "{from_number}",
                "text": "{message}"
        }
"""


class SMSProvider(BaseProvider):

        CREDENTIALS_SCHEMA = {
                "api_url" : {
                        "type" : "string",
                        "required" : True,
                },
                "auth_type" : {
                        "type" : "string",
                        "required" : True,
                        "default" : "basic",
                        "choices" : ["basic","bearer","api_key"],
                },
                "auth_credentials" : {
                        "type" : "dict",
                        "required" : True,
                        "description": "Credentials for auth, consist of username and password field or api key etc., depending on auth type" 
                },
                "from_number" : {
                        "type" : "string",
                        "required" : True, 
                        "validator" : validate_phone_number       
                },
                "body_template" : {
                        "type" : "dict",
                        "required" : True,
                },

        }


        
        # depends on auth_type in credentials
        AUTH_HANDLERS = {
                'basic' : lambda creds : {
                        'auth' : (creds.get('username') , creds.get('password'))
                },
                'bearer' : lambda creds : {
                        'headers' : {'Authorization' : f"Bearer {creds.get('token')}"}
                },
                'api_key' : lambda creds : {
                        'headers' : {'Authorization' : f"Api-Key {creds.get('api_key')}"}
                }
        }



        def send(self, credentials, payload):
                """
                payload carries the "to" and "message" data.
                """
                
                template = credentials.get('body_template') 
                values = {**credentials , **payload}
                body = { k  :v.format_map(values) for k,v in template.items()}
                
                auth_handler = self.AUTH_HANDLERS.get(credentials.get('auth_type'))
                request_kwargs = auth_handler(credentials.get('auth_credentials'))


                try:
                        requests.request(
                                method='POST',
                                url=credentials.get('api_url'),
                                data= body ,
                                **request_kwargs
                        ) 
                except Exception as e:
                        raise ChannelError("An error occured while sending request to SMS api.",str(e))
                


                
