from .base import BaseAction
from apps.channels.providers import EmailProvider


class SendEmail(BaseAction):
        
        provider_class = EmailProvider()
        requires_channel = True


