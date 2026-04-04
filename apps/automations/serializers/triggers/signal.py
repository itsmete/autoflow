# signal based triggers, 
from ..base import BaseConfigSerializer
from ...engine.signal_registry import SIGNAL_EVENT_MAP
from django.utils.translation import gettext_lazy as _
from ..validators import validate_model_path

"""
        When a model object is changed, automatipns must be triggered by a django signal

        config :
                model_name = app_label.model_name , users.User, tenants.Branch

                signal_type = post_save, post_delete, pre_save, pre_delete ,etc. 

                event_type = created,updated,deleted
                
"""



class SignalTriggerSerializer(BaseConfigSerializer):
        CONFIG_SCHEMA = {
                "model_name" : {
                        "type" : "string",
                        "required" : True,
                        "validator": validate_model_path,
                        "error_message" : "Invalid model or path"
                },
                "event_type" : {
                        "type" : "string",
                        "required" : True,
                        "choices" : list(SIGNAL_EVENT_MAP.keys()),
                        
                }

        }
        