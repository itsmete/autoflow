# signal based triggers,
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

from ..base import BaseConfigSerializer
from ...engine.signal_registry import SIGNAL_EVENT_MAP
from ..validators import validate_model_path

"""
        When a model object is changed, automatipns must be triggered by a django signal

        config :
                model_name = app_label.model_name , users.User, tenants.Branch

                signal_type = post_save, post_delete, pre_save, pre_delete ,etc. 

                event_type = created, updated, deleted, field_changed

                field, from_value, to_value - only for event_type = field_changed

        `field_changed` is the "siparis durumu hazirlaniyor -> yolda oldugunda" case
        (ROADMAP section 4.1). `updated` cannot express it: it fires on every save.
"""

# from_value / to_value are comparison constants and may be a string, a number or a
# boolean - whatever the watched column holds.
COMPARISON_KEYS = ('from_value','to_value')



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
                        
                },
                "field" : {
                        "type" : "string",
                        "required" : False,
                }

        }

        def validate_config(self,value):
                config = dict(value or {})

                # validate_schema insists on exactly one primitive type per key, and a
                # status is as likely to be a number as a word. So the comparison
                # constants are set aside, checked here, and put back - keeping them
                # out of the schema also keeps them from being reported as unknown.
                comparisons = {key : config.pop(key) for key in COMPARISON_KEYS if key in config}

                data = super().validate_config(config)

                for key,comparison in comparisons.items():
                        if isinstance(comparison,(dict,list)):
                                raise serializers.ValidationError(
                                        _("Comparison values must be a single value")
                                )

                if data.get('event_type') == 'field_changed':
                        # Without a field there is nothing to compare, and the trigger
                        # would silently never fire.
                        field = (data.get('field') or '').strip()

                        if not field:
                                raise serializers.ValidationError(
                                        _("A field name is required for a field_changed signal")
                                )

                        data['field'] = field
                elif not data.get('field'):
                        data.pop('field',None)

                data.update(comparisons)

                return data
