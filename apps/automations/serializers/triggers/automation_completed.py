import uuid

from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

from ..base import BaseConfigSerializer

"""
        Chaining automations - the Sub-Zap case (ROADMAP section 4.1).

        "When the order-fulfilment automation succeeds, start the notification one"
        is not a schedule and not an external event: the orchestrator emits it when a
        run finishes.

        config :
                source_automation_id = the automation to follow (uuid)

                trigger_on_status = success | failure | skipped (default: success)

        The source is referenced by id inside a JSON config, so no foreign key enforces
        the tenant boundary. apps/automations/runtime.py filters on it at fan-out time.
"""


class AutomationCompletedTriggerSerializer(BaseConfigSerializer):
        CONFIG_SCHEMA = {
                "source_automation_id" : {
                        "type" : "string",
                        "required" : True,
                },
                "trigger_on_status" : {
                        "type" : "string",
                        "required" : False,
                        "default" : "success",
                        "choices" : ["success","failure","skipped"],
                },
        }

        def validate_config(self,value):
                data = super().validate_config(value)

                # A malformed id would never match a real automation, so the trigger
                # would be accepted, stored and never fire - the worst outcome.
                try:
                        uuid.UUID(str(data.get('source_automation_id')))
                except (AttributeError,TypeError,ValueError):
                        raise serializers.ValidationError(
                                _("source_automation_id must be a valid automation id")
                        )

                return data
