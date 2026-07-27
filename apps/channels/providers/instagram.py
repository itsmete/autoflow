from .base import BaseProvider
from ..exceptions import ChannelError
import requests
import logging

"""
Payload will include 
        to (ig_sid) 
        message text
        image_url  : image_url (OPTIONAL)
        post_id : post_id (OPTIONAL)



Credentials will include
        IG_ID - string 
        IG_SID -> Instagram sopec ID -> User ID who we want to send message
        Bearer Token <INSTAGRAM_USER_ACCESS_TOKEN>  

        
Data structure must be like:

        {

                "recipient" {
                        "id" : <IGSID>
                },
                "message" : {}
                        "text" : "<TEXT_OR_LINK>"
                        "attachments" : [
                                {
                                        "type" : "image",
                                        "payload" : {
                                                "url" : "<IMAGE_URL>"
                                        }
                                },
                                {
                                        "type": "MEDIA_SHARE",
                                        "payload" : {
                                                "id" : "<POST_ID>"
                                        }
                                }


                        ]
                }

        }

upon success :
        {
                "recipient_id" : "IGSID",
                "message_id" : "MESSAGE-ID",
        }


exception :

        {
                "error": {
                        "message": "The requested feature isn't available yet. Please check back later.",
                        "type": "IGApiException",
                        "is_transient": true,
                        "code": 2,
                        "error_subcode": 2534068,
                        "fbtrace_id": "AligRrOm344Dy7MGyP0Ua_C"
                }
        }

"""


logger = logging.getLogger(__name__)


class InstagramProvider(BaseProvider):

        CREDENTIALS_SCHEMA = {
                "ig_id" :{
                        "type" : "string",
                        "required" : True,
                        "description" :"Instagram ID of business" 
                },
                
                "access_token" : {
                        "type" : "string",
                        "required" : True,
                        "description" : "Authorization Bearer token to access graph API"
                },
                "api_version" : {
                        "type" : "string",
                        "required" : False,
                        "description" : "Instagram Graph API version",
                        "default" : "v25.0"
                }

        }



        def send(self, credentials, payload):
                

                ig_sid = payload.get('to')
                message = payload.get('message')

                image_url = payload.get('image_url')
                post_id = payload.get('post_id')

                ig_id = credentials.get('ig_id')
                access_token = credentials.get('access_token')
                api_version = credentials.get('api_version')

                url = f"https://graph.instagram.com/{api_version}/{ig_id}/messages"
                
                attachments = [
                       
                        *([ {"type": "image", "payload": {"url": image_url}} ] if image_url is not None else []),
                        *([ {"type": "MEDIA_SHARE", "payload": {"id": post_id}} ] if post_id is not None else []),

                ]
                
                data = {
                        "recipient" : {
                                "id" : ig_sid
                        },
                        "message" : {
                                "text" : message,
                                **({"attachments" : attachments} if attachments else {})               
                        }

                }



                
                try:
                        response = requests.request(
                                method= 'POST',
                                url=url,
                                headers= {
                                        "Authorization" : f"Bearer {access_token}",
                                        'Content-Type' : "application/json",
                                },
                                json= data 

                        )

                        logger.info(f"Response from GRAPH API : {response.text} ")

                        response.raise_for_status()
                except Exception as e:
                        logger.error("An Error occured while sending IG DM message", str(e))
                        raise ChannelError("An Error occured while sending IG DM message", str(e))










