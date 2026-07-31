from apps.channels.providers import WhatsAppProvider
from .base import BaseAction

class SendWhatsappMessage(BaseAction):

        provider_class = WhatsAppProvider()
        requires_channel = True