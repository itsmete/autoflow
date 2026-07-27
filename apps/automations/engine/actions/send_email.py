from .base import BaseAction
from ..exceptions import ExecutionError
from django.core.mail import send_mail

class SendEmail(BaseAction):

        def execute(self, config):
                to = config.get('to')   
                subject = config.get('subject')
                body = config.get('body')

                try:
                        
                        send_mail(
                                subject=subject,
                                message=body,
                                from_email="WILL_BE_PASSED_TO_CHANNELS@AUTFLOW.COM",
                                fail_silently=False,
                                recipient_list=[to]        
                        )
                except Exception as e:
                        raise ExecutionError(f"An error occured while sending mail to {to},with the subject {subject}")

                return True



