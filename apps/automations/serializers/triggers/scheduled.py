from django.utils.translation import gettext_lazy as _
from .base import BaseTriggerSerializer
from rest_framework import serializers
from croniter import croniter
"""
Type.ScheduledTriggerSerializer
        .config {
                type =  choice field {one_time,n,forever} // n : counted

                count = positive_integer // if type is n , else not required

                cron_expression  = string , celery will take this as a ,  cron exp.

                start_time = DateTime , when it is first triggered, UNIX TIMESTAMP
                        //If one_time it must be provided, 
                        // if n , or forever, the closest time (according to cron expression) is the start

                        
        }


"croniter" package is a standart for validating cron expressions. 

"""
REQUIRES_CRON = {'n' ,'forever'}
VALID_TYPES =  ['one_time','n','forever']


class ScheduledTriggerSerializer(BaseTriggerSerializer):

        def validate_config(self,value):
                # value -> JSON Objecct


                schedule_type = value.get('type') 
                count = value.get('count')
                start_time = value.get('start_time')
                cron_experssion = value.get('cron_expression')

                if  schedule_type not in VALID_TYPES:
                        raise serializers.ValidationError(_("Invalid scheduling type"))
                
                
                if  (schedule_type == 'n'):
                        if not count:
                                raise serializers.ValidationError(_("Repeat count must be specified for this scheduling type."))
                        if count < 0 :
                                raise serializers.ValidationError(_("Repeat count must be positive"))

                if (not start_time) and (schedule_type == 'one_time'):
                        raise serializers.ValidationError(_("Start time must be provided for this scheduling type."))                
                
                if (schedule_type in REQUIRES_CRON):
                        if not cron_experssion:
                                raise serializers.ValidationError(_("Time interval must be defined for this scheduling type"))
                        
                        if not croniter.is_valid(cron_experssion):
                                raise serializers.ValidationError(_("Invalid cron expression"))                




                return value 
