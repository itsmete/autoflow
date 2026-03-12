from .base import BaseTriggerSerializer


class EventTriggerSerializer(BaseTriggerSerializer):

        def validate_config(self, value):
                return value