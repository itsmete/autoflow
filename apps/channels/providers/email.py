from .base import BaseProvider
from django.core.mail.backends.smtp import EmailBackend
from django.core.mail.message import EmailMessage
from ..exceptions import ChannelError
from django.utils.translation import gettext_lazy as _
import logging


"""

Credentials for email:
host -> SMTP server address
port -> 587 (TLS) or 465 (SSL) 
username -> email address or username
password -> password or app password
use_tls -> Boolean


Payload for email:
to 
subject
body

"""

logger = logging.getLogger(__name__)



class EmailProvider(BaseProvider):

        # email credentials schema keywords should be compatible with Django's EmailBackend parameter names

        CREDENTIALS_SCHEMA = {
                "host": {
                        "type" : "string",
                        "required" : True,
                        "description" : "SMTP server address",
                },
                "port":{
                        "type" : "integer",
                        "required" : True,
                        "description" : "SMTP port",
                        "default" : 587
                },
                
                "username":{
                        "type" :"string",
                        "required": True,
                        "description" : "Email address or username",
                },
                "password":{
                        "type" :"string",
                        "required": True,
                        "description" : "Password / Apppassword",
                },
                "use_tls":{
                        "type" :"boolean",
                        "required": True,
                        "description" : "Whether TLS is enabled",
                },



        }

        def send(self,credentials,payload):

                try:
                        backend = EmailBackend(**credentials,fail_silently=False)
                        to = payload.get('to')
                        subject=payload.get('subject')
                        body= payload.get('body')
                        from_email= credentials.get('username')

                        message = EmailMessage(
                                to = [to],
                                subject=subject,
                                body= body,
                                from_email= from_email
                        )
                        num_sent = backend.send_messages([message])
                        logger.info(f" An email ({subject})has been sent to the {to} from {from_email}")
                        
                        return True if num_sent > 0 else False
                except Exception as e:
                        logger.error(f"An error occured while sending email to {to}, with the subject {subject}, error :" , str(e))
                        raise ChannelError(f"An error occured while sending email to {to}, with the subject {subject}")