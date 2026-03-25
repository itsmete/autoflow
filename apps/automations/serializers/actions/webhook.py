
from ..base import BaseConfigSerializer

from django.utils.translation import gettext_lazy as _
from ..validators import url_validator



class WebhookActionSerializer(BaseConfigSerializer):
        CONFIG_SCHEMA = {
                "url" : {
                        "type" : "string",
                        "required" : True,
                        "validator" : url_validator
                },
                "method" : {
                        "type" : "string",
                        "required" : True,
                        "choices" : ['GET','POST','PUT','DELETE']
                },
                "payload": {
                        "type" : "dict",
                        "required" : False,
                        "required_at_runtime" : True
                },
                "headers" : {
                        "type":"dict",
                        "required": True
                }
        }

        