"""

WhatsApp Business Cloud API





Credentials for whatsapp
{
        access_token -> 
        phone_number_id ->
        api_version -> v23,0 etc.
}

Payload coming from Action
        to -> receiver phone_number
        message -> message context

        
"""

from .base import BaseProvider
from ..exceptions import ChannelError
import requests
import logging


logger = logging.getLogger(__name__)

class WhatsAppProvider(BaseProvider):

        
        


        CREDENTIALS_SCHEMA =  {
                "access_token" :  {
                        "type" : "string",
                        "required" : True,
                        "description" : "Bearer token given by Meta"
                },
                "phone_number_id" : {
                        "type" : "string",
                        "required" : True,
                        "description" : " WhatsApp Business Phone Number ID",
                },
                "api_version" : {
                        "type" : "string",
                        "required" : False,
                        "description" : "WhatsApp Business Cloud API version",
                        "default" : "v23.0"
                }

        }


        def send(self, credentials, payload):
                api_version = credentials.get('api_version')
                phone_number_id = credentials.get('phone_number_id')

                url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"

                data = {
                        "messaging_product": "whatsapp",
                        "recipient_type" : "individual",

                        "to" : payload.get('to'),
                        "type" : "text",
                        "text" : {
                                "body" : payload.get('message'),
                                "preview_url" : False
                        }
                }
                
                

                headers= {
                                        'Authorization' : f"Bearer {credentials.get('access_token')}", 
                                        'Content-Type' : "application/json", 
                }

                try :
                        response = requests.request(
                                method='POST',
                                url= url,
                                json=data,
                                headers=headers
                        )
                        logger.info("WP API Response : " + response.text)

                        response.raise_for_status()
                except Exception as e:
                        logger.error(f"An error occured while sending Whatsapp message to {payload.get('to')} error :" , str(e))
                        raise ChannelError(f"An error occured while sending whatsapp message to {payload.get('to')}")


