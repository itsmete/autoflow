from ..base import BaseConfigSerializer
from core.roles import ROLE_WEIGHTS
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

class ManualTriggerSerializer(BaseConfigSerializer):
        """
                {
                        "allowed_roles" : ["owner","branch_manager"] 
                }
        
        """
        def validate_config(self, value):

                # refactored : trigger will not take allowed_roles anymore, base Automation model will handle  
                # allowed_list = value.get("allowed_roles")

                # if not allowed_list:
                #         raise serializers.ValidationError(_("Allowed roles can't be empty"))
                
                # for role in allowed_list:
                #         if not ROLE_WEIGHTS.get(role):
                #                 raise serializers.ValidationError(_("Invalid role is entered")) 


                return value                  

                        