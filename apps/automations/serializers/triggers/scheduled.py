from django.utils.translation import gettext_lazy as _
from ..base import BaseConfigSerializer
from rest_framework import serializers
from croniter import croniter
"""
Type.ScheduledTriggerSerializer
        .config {
                schedule_type =  choice field {one_time,n,forever} // n : counted

                count = positive_integer // if type is n , else not required

                cron_expression  = string , celery will take this as a ,  cron exp.

                start_time = DateTime , when it is first triggered, UNIX TIMESTAMP
                        //If one_time it must be provided, 
                        // if n , or forever, the closest time (according to cron expression) is the start

                        
        }


"croniter" package is a standart for validating cron expressions. 

"""
REQUIRES_CRON = ('n' ,'forever')



class ScheduledTriggerSerializer(BaseConfigSerializer):

        CONFIG_SCHEMA = {
                "schedule_type": {
                        "type": "string",
                        "required": True,
                        "choices": ["one_time", "n", "forever"]
                },
                "count": {
                        "type": "integer",
                        "required": False,
                },
                "start_time": {
                        "type": "string",
                        "required": False,
                },
                "cron_expression": {
                        "type": "string",
                        "required": False,
                        "validator": croniter.is_valid,
                        "error_message" : "Invalid cron expression"
                }
        }

        def validate_config(self,value):
                # value -> JSON Objecct
                value = super().validate_config(value)

                schedule_type = value.get('schedule_type') 
                count = value.get('count')
                start_time = value.get('start_time')
                cron_expression = value.get('cron_expression')

                
                
                if  (schedule_type == 'n'):
                        if not count:
                                raise serializers.ValidationError(_("Repeat count must be specified for this scheduling type."))
                        if count < 0 :
                                raise serializers.ValidationError(_("Repeat count must be positive"))

                if (not start_time) and (schedule_type == 'one_time'):
                        raise serializers.ValidationError(_("Start time must be provided for this scheduling type."))                
                

                if schedule_type in REQUIRES_CRON and not cron_expression : 
                
                        raise serializers.ValidationError(_("Time interval must be defined for this scheduling type"))
                        


                return value 
