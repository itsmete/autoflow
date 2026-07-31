"""
Schema-driven validation for trigger and action configs.

This is the architectural spine described in CLAUDE.md §3.7: configs live in a
JSONField and are validated against a per-integration schema rather than being
declared as serializer fields. Adding a channel means adding a schema here, so these
tests are the contract that keeps that extension point honest.
"""
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.automations.models import AutomationTrigger, TriggerType
from apps.automations.serializers.actions import get_action_serializer
from apps.automations.serializers.base import validate_schema
from apps.automations.serializers.triggers import get_trigger_serializer_class
from apps.automations.serializers.validators import validate_model_path
from tests import factories


class ValidateSchemaTests(TestCase):
    """The generic walker every config serializer delegates to."""

    def test_a_valid_payload_comes_back_unchanged(self):
        schema = {"name": {"type": "string", "required": True}}
        self.assertEqual(validate_schema(schema, {"name": "ok"}), {"name": "ok"})

    def test_a_missing_required_field_is_rejected(self):
        schema = {"name": {"type": "string", "required": True}}
        with self.assertRaises(ValidationError):
            validate_schema(schema, {})

    def test_a_missing_optional_field_is_allowed(self):
        schema = {"name": {"type": "string", "required": False}}
        self.assertEqual(validate_schema(schema, {}), {})

    def test_an_optional_field_gets_its_default(self):
        schema = {"mode": {"type": "string", "required": False, "default": "basic"}}
        self.assertEqual(validate_schema(schema, {}), {"mode": "basic"})

    def test_a_wrong_type_is_rejected(self):
        schema = {"count": {"type": "integer", "required": True}}
        with self.assertRaises(ValidationError):
            validate_schema(schema, {"count": "3"})

    def test_a_value_outside_choices_is_rejected(self):
        schema = {"mode": {"type": "string", "required": True, "choices": ["a", "b"]}}
        with self.assertRaises(ValidationError):
            validate_schema(schema, {"mode": "c"})

    def test_a_custom_validator_runs(self):
        def must_be_upper(value):
            if value != value.upper():
                raise ValueError("nope")

        schema = {"code": {"type": "string", "required": True, "validator": must_be_upper}}
        validate_schema(schema, {"code": "ABC"})
        with self.assertRaises(ValidationError):
            validate_schema(schema, {"code": "abc"})

    def test_a_validators_error_message_is_surfaced(self):
        def always_fails(value):
            raise ValueError()

        schema = {
            "code": {
                "type": "string", "required": True,
                "validator": always_fails, "error_message": "Custom explanation",
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_schema(schema, {"code": "x"})
        self.assertIn("Custom explanation", str(ctx.exception))

    def test_nested_dict_schemas_are_walked(self):
        schema = {
            "auth": {
                "type": "dict", "required": True,
                "fields": {"token": {"type": "string", "required": True}},
            }
        }
        validate_schema(schema, {"auth": {"token": "t"}})
        with self.assertRaises(ValidationError):
            validate_schema(schema, {"auth": {}})

    def test_unknown_keys_are_ignored(self):
        schema = {"name": {"type": "string", "required": True}}
        result = validate_schema(schema, {"name": "ok", "surplus": 1})
        self.assertIn("name", result)


class ModelPathValidatorTests(TestCase):

    def test_a_real_model_path_passes(self):
        validate_model_path("tenants.Tenant")
        validate_model_path("users.User")

    def test_an_unknown_model_is_rejected(self):
        with self.assertRaises(LookupError):
            validate_model_path("tenants.Unicorn")

    def test_a_malformed_path_is_rejected(self):
        with self.assertRaises((LookupError, ValueError)):
            validate_model_path("not-a-path")


class TriggerSerializerTests(TestCase):

    def test_every_trigger_type_has_a_serializer(self):
        for value, _label in TriggerType.choices:
            self.assertIsNotNone(
                get_trigger_serializer_class(value), f"no serializer for {value}"
            )

    def test_unknown_trigger_type_returns_none(self):
        self.assertIsNone(get_trigger_serializer_class("telepathy"))

    # -- scheduled -------------------------------------------------------------

    def _scheduled(self, config):
        serializer = get_trigger_serializer_class("scheduled")()
        return serializer.validate_config(config)

    def test_a_forever_schedule_needs_a_cron_expression(self):
        self._scheduled({"schedule_type": "forever", "cron_expression": "0 1 * * *"})
        with self.assertRaises(ValidationError):
            self._scheduled({"schedule_type": "forever"})

    def test_an_invalid_cron_expression_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._scheduled({"schedule_type": "forever", "cron_expression": "not a cron"})

    def test_a_counted_schedule_needs_a_positive_count(self):
        self._scheduled({"schedule_type": "n", "count": 5, "cron_expression": "0 1 * * *"})
        with self.assertRaises(ValidationError):
            self._scheduled({"schedule_type": "n", "cron_expression": "0 1 * * *"})

    def test_a_one_time_schedule_needs_a_start_time(self):
        self._scheduled({"schedule_type": "one_time", "start_time": "2026-08-01T10:00:00"})
        with self.assertRaises(ValidationError):
            self._scheduled({"schedule_type": "one_time"})

    def test_an_unknown_schedule_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._scheduled({"schedule_type": "whenever"})

    # -- signal ----------------------------------------------------------------

    def test_a_signal_trigger_validates_its_model_path_and_event(self):
        serializer = get_trigger_serializer_class("signal")()
        serializer.validate_config({"model_name": "tenants.Branch", "event_type": "created"})

        with self.assertRaises(ValidationError):
            serializer.validate_config({"model_name": "tenants.Nope", "event_type": "created"})

        with self.assertRaises(ValidationError):
            serializer.validate_config({"model_name": "tenants.Branch", "event_type": "exploded"})

    # -- webhook / manual ------------------------------------------------------

    def test_webhook_and_manual_configs_are_free_form(self):
        self.assertEqual(
            get_trigger_serializer_class("webhook")().validate_config({"anything": 1}),
            {"anything": 1},
        )
        self.assertEqual(
            get_trigger_serializer_class("manual")().validate_config({}), {}
        )


class TriggerModelTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def test_a_webhook_trigger_mints_a_secret_key_on_save(self):
        trigger = factories.make_trigger(self.automation, trigger_type="webhook", config={})
        self.assertTrue(trigger.config.get("secret_key"))
        self.assertGreaterEqual(len(trigger.config["secret_key"]), 32)

    def test_an_existing_secret_key_is_preserved(self):
        trigger = factories.make_trigger(
            self.automation, trigger_type="webhook", config={"secret_key": "keep-me"}
        )
        self.assertEqual(trigger.config["secret_key"], "keep-me")

    def test_non_webhook_triggers_get_no_secret(self):
        trigger = factories.make_trigger(self.automation, trigger_type="manual", config={})
        self.assertNotIn("secret_key", trigger.config)

    def test_secret_keys_are_unique_per_trigger(self):
        a = factories.make_trigger(self.automation, trigger_type="webhook", config={})
        b = factories.make_trigger(self.automation, trigger_type="webhook", config={})
        self.assertNotEqual(a.config["secret_key"], b.config["secret_key"])


class ActionSerializerTests(TestCase):

    def test_every_action_type_has_a_serializer(self):
        from apps.automations.models import ActionTypes

        for value, _label in ActionTypes.choices:
            self.assertIsNotNone(
                get_action_serializer(value), f"no serializer for {value}"
            )

    def test_unknown_action_type_returns_none(self):
        self.assertIsNone(get_action_serializer("smoke_signal"))

    def test_a_webhook_action_requires_url_method_and_headers(self):
        serializer = get_action_serializer("webhook")()
        serializer.validate_config(
            {"url": "https://example.com/hook", "method": "POST", "headers": {}}
        )

        with self.assertRaises(ValidationError):
            serializer.validate_config({"method": "POST", "headers": {}})

    def test_a_webhook_action_rejects_a_bad_url(self):
        serializer = get_action_serializer("webhook")()
        with self.assertRaises(ValidationError):
            serializer.validate_config(
                {"url": "not a url", "method": "POST", "headers": {}}
            )

    def test_a_webhook_action_rejects_an_unsupported_method(self):
        serializer = get_action_serializer("webhook")()
        with self.assertRaises(ValidationError):
            serializer.validate_config(
                {"url": "https://example.com", "method": "TRACE", "headers": {}}
            )

    def test_email_action_fields_are_optional_at_config_time(self):
        # They are marked required_at_runtime: the trigger payload may supply them.
        serializer = get_action_serializer("email")()
        self.assertEqual(serializer.validate_config({}), {})

    def test_an_email_action_still_validates_a_supplied_address(self):
        serializer = get_action_serializer("email")()
        serializer.validate_config({"to": "someone@example.com"})
        with self.assertRaises(ValidationError):
            serializer.validate_config({"to": "not-an-email"})
