from ..base import BaseConfigSerializer
from django.utils.translation import gettext_lazy as _
from ..validators import validate_email_wrapped

class EmailActionSerializer(BaseConfigSerializer):

        CONFIG_SCHEMA = {
                "to" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True , # FOR trigger to action data flow , not for pre-defined config
                        "validator" : validate_email_wrapped,
                        "error_message" : "Invalid email format" 
                },
                "subject" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                },
                "body" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                }
        }

