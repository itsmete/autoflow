from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import AutomationTrigger,TriggerType
from django.utils.translation import gettext_lazy as _

class TriggerSecretKeyAuthentication(BaseAuthentication):

        def authenticate(self, request):

                secret_key = request.headers.get('X-Webhook-Secret')

                if not secret_key:
                        raise AuthenticationFailed(_("Secret key not found"))
                try:
                        # JSON fields could be accessed with "__"  
                        obj = AutomationTrigger.objects.get(trigger_type =TriggerType.WEBHOOK ,config__secret_key = secret_key)
                except AutomationTrigger.DoesNotExist as e:
                        raise AuthenticationFailed(_("Secret key doesn't matched"))

        
                # authenticate methods return a tuple which is (User,auth), view can access via these keyword, e.g. request.auth = obj 
                # We don't have user in trigger views, so needs to return Nıne
                return (None, obj)



