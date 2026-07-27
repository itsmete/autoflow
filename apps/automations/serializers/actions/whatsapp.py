from ..base import BaseConfigSerializer


class WhatsappActionSerializer(BaseConfigSerializer):

        CONFIG_SCHEMA = {
                "to" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                },
                "message": {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,

                }
        }