"""

        AutoamationTriggerSerializer
        AutomationConditionSerializer
        AutomationActionSerilizer
        AutomationLogSerializer
        AutomationSerializer
        
"""

from actions import get_action_serializer
from triggers import get_trigger_serializer_class
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from ..models import AutomationCondition

class AutomationTriggerSerializer(serializers.Serializer):
        
        def validate(self,data):

                trigger_type = data.get('trigger_tpye')
                config = data.get('config')

                serializer_cls = get_trigger_serializer_class(trigger_type)

                if not serializer_cls:
                        raise serializers.ValidationError(_("Invalid Serializer type"))
                
                trigger_serializer = serializer_cls(data=config)

                trigger_serializer.is_valid(raise_exception = True)





class AutomationConditionSerializer(serializers.ModelSerializer):

        class Meta:
                model = AutomationCondition
                fields= ['field','operator','value']
        

                




class AutomationActionSerializer(serializers.Serializer):

        def validate(self,data):

                action_type = data.get('action_type')

                config = data.get('config')


                serializer_cls = get_action_serializer(action_type)

                if not serializer_cls:
                        raise serializers.ValidationError(
                                _("Invalid action type")
                        )
                

                action_serializer = serializer_cls(data=config)
                action_serializer.is_valid(raise_exception = True)


                return data