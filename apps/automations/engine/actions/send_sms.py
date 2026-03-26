from apps.channels.providers import SMSProvider
from .base import BaseAction

class SendSMS(BaseAction):
        provider_class = SMSProvider()
        requires_channel = True