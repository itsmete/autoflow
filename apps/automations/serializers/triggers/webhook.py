from ..base import BaseConfigSerializer

"""
InboundWebookTriggerSerializer

        Config
                secret-key : abcd 
                #       When a automation is registered, system gives a secret key,
                #       And webhook sends the key in headers while posting to backend for triggering


TO VERIFY SECRET KEY,
We're gonna use Custom middlewares , 
since serializers are not supposed to ask "if the request is authorized or not".
we will handle it via WebhookAuthMiddleware
"""
class WebhookTriggerSerializer(BaseConfigSerializer):

        def validate_config(self,value):
                return value


