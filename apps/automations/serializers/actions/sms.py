from ..base import BaseConfigSerializer
from apps.channels.providers.validators import validate_phone_number


class SMSActionSerializer(BaseConfigSerializer):

        CONFIG_SCHEMA = {
                "to" : { # "to" information can be either come in payload or config
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                        "validator" : validate_phone_number,
                        "error_message" : "Invalid phone number"
                },
                "message" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                }
        }