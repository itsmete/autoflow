# signal based triggers, 
from .base import BaseTriggerSerializer
from ...engine.signal_registry import get_signal_for_event
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from django.apps import apps


"""
        When a model object is changed, automatipns must be triggered by a django signal

        config :
                model_name = app_label.model_name , users.User, tenants.Branch

                type = post_save, post_delete, pre_save, pre_delete ,etc. 
                
"""



class SignalTriggerSerializer(BaseTriggerSerializer):

        def validate_config(self, value):

                if not get_signal_for_event(value.get('event_type')):
                        raise serializers.ValidationError(_("Invalid signal event type."))
                
                try:
                        apps.get_model(value.get('model_name'))
                except LookupError as e:
                        raise serializers.ValidationError(_("Invalid model name"))
                

                return value