from rest_framework import serializers
from abc import abstractmethod


class BaseTriggerSerializer(serializers.Serializer):
        

        @abstractmethod
        def validate_config(self,value):
                pass