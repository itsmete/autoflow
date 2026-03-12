from .base import BaseTriggerSerializer


class ManualTriggerSerializer(BaseTriggerSerializer):

        def validate_config(self, value):
                return value