from .base import BaseAction
from apps.channels.providers import InstagramProvider

class SendInstagramDM(BaseAction):

        provider_class = InstagramProvider()
        requires_channel = True                