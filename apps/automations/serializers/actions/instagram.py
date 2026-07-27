from ..base import BaseConfigSerializer
from ..validators import url_validator

class InstagramDMActionSerializer(BaseConfigSerializer):

        CONFIG_SCHEMA = {
                "to" :{
                        "type" :"string",
                        "required" : False,
                        "required_at_runtime" : True ,
                },
                "message" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : True ,
                },
                "image_url" :{
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : False ,
                        "validator" : url_validator,
                        "error_message" : "Invalid URL"
                },
                "post_id" : {
                        "type" : "string",
                        "required" : False,
                        "required_at_runtime" : False ,
                }
        }