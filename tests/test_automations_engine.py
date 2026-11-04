"""
apps/automations/engine: the runtime that actually executes a workflow.

Trigger -> Condition -> Action. This file covers the last two plus the template
renderer; the orchestrator test stitches them together.

None of this had any coverage, and none of it worked: the orchestrator read
relation accessors that do not exist, actions dereferenced a model field that was
never declared, and the retry loop could spin forever.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.automations.engine.actions import get_action_class
from apps.automations.engine.evaluator import ConditionEvaluator
from apps.automations.engine.exceptions import (
    EvaluationError,
    ExecutionError,
    TemplateRenderError,
)
from apps.automations.engine.executor import AutomationExecutor
from apps.automations.engine.orchestrator import AutomationOrchestrator
from apps.automations.engine.signal_registry import get_signal_for_event
from apps.automations.engine.template_manager import TemplateManager
from apps.automations.models import ActionTypes, Automation, AutomationLog, AutomationStatus
from tests import factories


class ConditionEvaluatorTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator()

    def _cond(self, **kwargs):
        return factories.make_condition(self.automation, **kwargs)

    def test_no_conditions_means_proceed(self):
        self.assertTrue(self.evaluator.evaluate_conditions([], {}))

    def test_equals(self):
        c = self._cond(field="status", operator="eq", value="paid")
        self.assertTrue(self.evaluator.evaluate_conditions([c], {"status": "paid"}))
        self.assertFalse(self.evaluator.evaluate_conditions([c], {"status": "open"}))

    def test_not_equals(self):
        c = self._cond(field="status", operator="neq", value="paid")
        self.assertTrue(self.evaluator.evaluate_conditions([c], {"status": "open"}))
        self.assertFalse(self.evaluator.evaluate_conditions([c], {"status": "paid"}))

    def test_greater_and_less_coerce_to_int(self):
        gt = self._cond(field="amount", operator="gt", value="100")
        lt = self._cond(field="amount", operator="lt", value="100")
        self.assertTrue(self.evaluator.evaluate_conditions([gt], {"amount": "150"}))
        self.assertFalse(self.evaluator.evaluate_conditions([gt], {"amount": "50"}))
        self.assertTrue(self.evaluator.evaluate_conditions([lt], {"amount": 50}))

    def test_contains_looks_for_the_value_inside_the_field(self):
        c = self._cond(field="note", operator="contains", value="urgent")
        self.assertTrue(self.evaluator.evaluate_conditions([c], {"note": "this is urgent"}))
        self.assertFalse(self.evaluator.evaluate_conditions([c], {"note": "routine"}))

    def test_in_splits_the_configured_value_on_commas(self):
        c = self._cond(field="city", operator="in", value="ankara,izmir")
        self.assertTrue(self.evaluator.evaluate_conditions([c], {"city": "izmir"}))
        self.assertFalse(self.evaluator.evaluate_conditions([c], {"city": "bursa"}))

    def test_and_requires_both(self):
        first = self._cond(field="a", operator="eq", value="1")
        second = self._cond(field="b", operator="eq", value="2", logical_operator="and")
        self.assertTrue(self.evaluator.evaluate_conditions([first, second], {"a": "1", "b": "2"}))
        self.assertFalse(self.evaluator.evaluate_conditions([first, second], {"a": "1", "b": "9"}))

    def test_or_requires_either(self):
        first = self._cond(field="a", operator="eq", value="1")
        second = self._cond(field="b", operator="eq", value="2", logical_operator="or")
        self.assertTrue(self.evaluator.evaluate_conditions([first, second], {"a": "9", "b": "2"}))
        self.assertFalse(self.evaluator.evaluate_conditions([first, second], {"a": "9", "b": "9"}))

    def test_the_first_conditions_logical_operator_is_ignored(self):
        only = self._cond(field="a", operator="eq", value="1", logical_operator="or")
        self.assertTrue(self.evaluator.evaluate_conditions([only], {"a": "1"}))

    def test_a_field_missing_from_the_payload_is_an_evaluation_error(self):
        c = self._cond(field="absent", operator="eq", value="1")
        with self.assertRaises(EvaluationError):
            self.evaluator.evaluate_conditions([c], {"other": "1"})

    def test_a_failed_numeric_coercion_is_an_evaluation_error(self):
        c = self._cond(field="amount", operator="gt", value="100")
        with self.assertRaises(EvaluationError):
            self.evaluator.evaluate_conditions([c], {"amount": "not-a-number"})

    def test_an_unknown_operator_is_an_evaluation_error(self):
        c = self._cond(field="a", operator="eq", value="1")
        c.operator = "bogus"
        with self.assertRaises(EvaluationError):
            self.evaluator.evaluate_conditions([c], {"a": "1"})


class TemplateManagerTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.manager = TemplateManager()

    def test_fetch_keys_the_result_by_field_name(self):
        # Regression: fetch() assigned resolved['field_name'], a literal string, so
        # every field collapsed into one entry and no placeholder ever matched.
        template = factories.make_template(
            self.tenant,
            raw_text="Hi {{customer}}",
            fields={
                "customer": {"type": "string", "required": True, "source": "payload",
                             "description": "name"},
            },
        )
        resolved = self.manager.fetch(template, {}, {"customer": "Ada"})
        self.assertEqual(resolved, {"customer": "Ada"})

    def test_fields_can_be_sourced_from_config_or_payload(self):
        template = factories.make_template(
            self.tenant,
            raw_text="{{greeting}} {{customer}}",
            fields={
                "greeting": {"type": "string", "required": True, "source": "config",
                             "description": "g"},
                "customer": {"type": "string", "required": True, "source": "payload",
                             "description": "c"},
            },
        )
        resolved = self.manager.fetch(template, {"greeting": "Merhaba"}, {"customer": "Ada"})
        self.assertEqual(resolved, {"greeting": "Merhaba", "customer": "Ada"})

    def test_a_missing_required_field_raises(self):
        template = factories.make_template(
            self.tenant,
            fields={"customer": {"type": "string", "required": True, "source": "payload",
                                 "description": "c"}},
        )
        with self.assertRaises(TemplateRenderError):
            self.manager.fetch(template, {}, {})

    def test_a_missing_required_field_falls_back_to_its_default(self):
        template = factories.make_template(
            self.tenant,
            fields={"customer": {"type": "string", "required": True, "source": "payload",
                                 "description": "c", "default": "there"}},
        )
        self.assertEqual(self.manager.fetch(template, {}, {}), {"customer": "there"})

    def test_an_unknown_source_raises(self):
        template = factories.make_template(
            self.tenant,
            fields={"customer": {"type": "string", "required": True, "source": "moon",
                                 "description": "c"}},
        )
        with self.assertRaises(TemplateRenderError):
            self.manager.fetch(template, {}, {"customer": "Ada"})

    def test_ai_sourced_fields_are_skipped_for_now(self):
        template = factories.make_template(
            self.tenant,
            fields={"blurb": {"type": "string", "required": True, "source": "ai",
                              "description": "b"}},
        )
        self.assertEqual(self.manager.fetch(template, {}, {}), {})

    def test_fill_substitutes_placeholders(self):
        template = factories.make_template(self.tenant, raw_text="Hi {{customer}}!")
        self.assertEqual(self.manager.fill(template, {"customer": "Ada"}), "Hi Ada!")

    def test_fill_keeps_conditional_blocks_whose_field_resolved(self):
        template = factories.make_template(
            self.tenant, raw_text="Hi{%if bonus%} - you earned {{bonus}}{%endif%}"
        )
        self.assertEqual(
            self.manager.fill(template, {"bonus": "10TL"}), "Hi - you earned 10TL"
        )

    def test_fill_drops_conditional_blocks_whose_field_is_absent(self):
        template = factories.make_template(
            self.tenant, raw_text="Hi{%if bonus%} - you earned {{bonus}}{%endif%}"
        )
        self.assertEqual(self.manager.fill(template, {}), "Hi")

    def test_render_is_fetch_then_fill(self):
        template = factories.make_template(
            self.tenant,
            raw_text="Merhaba {{customer}}",
            fields={"customer": {"type": "string", "required": True, "source": "payload",
                                 "description": "c"}},
        )
        self.assertEqual(
            self.manager.render(template, {}, {"customer": "Ada"}), "Merhaba Ada"
        )


class ActionRegistryTests(TestCase):

    def test_every_action_type_has_a_class(self):
        for value, _label in ActionTypes.choices:
            self.assertIsNotNone(get_action_class(value), f"no action class for {value}")

    def test_unknown_action_type_returns_none(self):
        self.assertIsNone(get_action_class("smoke_signal"))

    def test_channel_backed_actions_declare_a_provider_class_not_an_instance(self):
        # provider_class was assigned EmailProvider() etc.; base.execute then called
        # self.provider_class(), i.e. it tried to call an instance.
        for action_type in ("email", "sms", "whatsapp", "instagram"):
            cls = get_action_class(action_type)
            self.assertTrue(cls.requires_channel)
            self.assertIsInstance(cls.provider_class, type, action_type)


class ActionExecutionTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)

    def test_prepare_merges_config_with_the_trigger_payload(self):
        action = factories.make_action(
            self.automation, action_type="webhook",
            config={"url": "https://x/y", "method": "POST", "headers": {}, "payload": {}},
        )
        prepared = get_action_class("webhook")(action).prepare({"order_id": 7})
        self.assertEqual(prepared["url"], "https://x/y")
        self.assertEqual(prepared["order_id"], 7)

    def test_config_wins_over_the_payload_on_a_key_clash(self):
        action = factories.make_action(
            self.automation, action_type="webhook",
            config={"url": "https://config", "method": "POST", "headers": {}, "payload": {}},
        )
        prepared = get_action_class("webhook")(action).prepare({"url": "https://payload"})
        self.assertEqual(prepared["url"], "https://config")

    def test_prepare_renders_the_attached_template_into_message(self):
        # AutomationAction.template did not exist as a field, so this raised
        # AttributeError before any action could run.
        template = factories.make_template(
            self.tenant,
            raw_text="Merhaba {{customer}}",
            fields={"customer": {"type": "string", "required": True, "source": "payload",
                                 "description": "c"}},
        )
        channel = factories.make_channel(self.tenant, self.branch)
        action = factories.make_action(
            self.automation, action_type="email", channel=channel,
            config={"to": "a@b.com", "subject": "Hi"},
        )
        action.template = template
        action.save()

        prepared = get_action_class("email")(action).prepare({"customer": "Ada"})
        self.assertEqual(prepared["message"], "Merhaba Ada")

    def test_prepare_without_a_template_leaves_message_alone(self):
        action = factories.make_action(self.automation, action_type="webhook")
        prepared = get_action_class("webhook")(action).prepare({})
        self.assertNotIn("message", prepared)

    @patch("apps.channels.providers.email.EmailBackend")
    def test_a_channel_action_hands_credentials_and_payload_to_its_provider(self, mock_backend):
        mock_backend.return_value.send_messages.return_value = 1
        channel = factories.make_channel(self.tenant, self.branch)
        action = factories.make_action(
            self.automation, action_type="email", channel=channel,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

        self.assertTrue(get_action_class("email")(action).run({}))
        mock_backend.assert_called_once()

    def test_a_channel_action_without_a_channel_is_an_execution_error(self):
        action = factories.make_action(
            self.automation, action_type="email", channel=None,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )
        with self.assertRaises(ExecutionError):
            get_action_class("email")(action).run({})

    @patch("apps.automations.engine.actions.send_webhook.requests.request")
    def test_the_webhook_action_needs_no_channel(self, mock_request):
        action = factories.make_action(
            self.automation, action_type="webhook",
            config={"url": "https://x/y", "method": "POST", "headers": {"A": "b"},
                    "payload": {"k": "v"}},
        )
        self.assertTrue(get_action_class("webhook")(action).run({}))

        _args, kwargs = mock_request.call_args
        self.assertEqual(kwargs["url"], "https://x/y")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["json"], {"k": "v"})

    @patch("apps.automations.engine.actions.send_webhook.requests.request",
           side_effect=OSError("unreachable"))
    def test_a_failing_webhook_becomes_an_execution_error(self, _mock_request):
        action = factories.make_action(self.automation, action_type="webhook")
        with self.assertRaises(ExecutionError):
            get_action_class("webhook")(action).run({})


class ExecutorTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.executor = AutomationExecutor()

    def test_no_actions_is_an_execution_error(self):
        with self.assertRaises(ExecutionError):
            self.executor.execute_actions([], {})

    def test_actions_run_in_order(self):
        first = factories.make_action(self.automation, order=1)
        second = factories.make_action(self.automation, order=2)
        seen = []

        with patch.object(AutomationExecutor, "_execute_single",
                          side_effect=lambda a, d: seen.append(a.order) or True):
            self.executor.execute_actions(
                self.automation.actions.order_by("order"), {}
            )

        self.assertEqual(seen, [1, 2])

    def test_on_failure_stop_halts_the_run(self):
        factories.make_action(self.automation, order=1, on_failure="stop")
        factories.make_action(self.automation, order=2, on_failure="stop")
        calls = []

        def boom(action, data):
            calls.append(action.order)
            raise ExecutionError("nope")

        with patch.object(AutomationExecutor, "_execute_single", side_effect=boom):
            result = self.executor.execute_actions(
                self.automation.actions.order_by("order"), {}
            )

        self.assertFalse(result)
        self.assertEqual(calls, [1])

    def test_on_failure_continue_runs_the_rest(self):
        factories.make_action(self.automation, order=1, on_failure="continue")
        factories.make_action(self.automation, order=2, on_failure="continue")
        calls = []

        def boom(action, data):
            calls.append(action.order)
            raise ExecutionError("nope")

        with patch.object(AutomationExecutor, "_execute_single", side_effect=boom):
            result = self.executor.execute_actions(
                self.automation.actions.order_by("order"), {}
            )

        self.assertTrue(result)
        self.assertEqual(calls, [1, 2])

    def test_on_failure_retry_stops_as_soon_as_an_attempt_succeeds(self):
        factories.make_action(self.automation, order=1, on_failure="retry")
        attempts = []

        def flaky(action, data):
            attempts.append(1)
            if len(attempts) < 3:
                raise ExecutionError("transient")
            return True

        with patch.object(AutomationExecutor, "_execute_single", side_effect=flaky):
            result = self.executor.execute_actions(
                self.automation.actions.order_by("order"), {}
            )

        self.assertTrue(result)
        self.assertEqual(len(attempts), 3)  # 1 initial + 2 retries

    def test_on_failure_retry_gives_up_after_max_retries(self):
        factories.make_action(self.automation, order=1, on_failure="retry")

        with patch.object(AutomationExecutor, "_execute_single",
                          side_effect=ExecutionError("always")):
            with self.assertRaises(ExecutionError):
                self.executor.execute_actions(
                    self.automation.actions.order_by("order"), {}
                )

    def test_retry_terminates_when_an_attempt_returns_falsy_without_raising(self):
        # Regression: the retry counter only advanced inside `except`, so an action
        # that returned False without raising looped forever and hung the worker.
        factories.make_action(self.automation, order=1, on_failure="retry")
        attempts = []

        def always_falsy(action, data):
            attempts.append(1)
            if len(attempts) == 1:
                raise ExecutionError("first failure enters the retry path")
            return False

        with patch.object(AutomationExecutor, "_execute_single", side_effect=always_falsy):
            with self.assertRaises(ExecutionError):
                self.executor.execute_actions(
                    self.automation.actions.order_by("order"), {}
                )

        self.assertEqual(len(attempts), 1 + AutomationExecutor.MAX_RETRIES)


class OrchestratorTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)
        self.orchestrator = AutomationOrchestrator()

    def test_a_run_with_no_conditions_executes_the_actions_and_logs_success(self):
        # Regression: the orchestrator read automation.automationcondition_set and
        # automationaction_set, which do not exist because both relations declare a
        # related_name. Every run died on AttributeError, and the catch-all logged it
        # with logger.error(msg, arg), which failed inside logging and hid the cause.
        factories.make_action(self.automation, order=1)

        with patch.object(AutomationExecutor, "_execute_single", return_value=True):
            result = self.orchestrator.run(str(self.automation.id), {"any": "payload"})

        self.assertTrue(result)
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.SUCCESS)

    def test_an_inactive_automation_is_skipped_and_logged_as_failure(self):
        self.automation.is_active = False
        self.automation.save()

        result = self.orchestrator.run(str(self.automation.id), {})

        self.assertFalse(result)
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.FAILURE)
        self.assertIn("not active", log.error_message)

    def test_unmet_conditions_skip_the_actions(self):
        factories.make_condition(self.automation, field="amount", operator="gt", value="100")
        factories.make_action(self.automation, order=1)

        with patch.object(AutomationExecutor, "_execute_single") as executed:
            result = self.orchestrator.run(str(self.automation.id), {"amount": "5"})

        self.assertFalse(result)
        executed.assert_not_called()
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.SKIPPED)

    def test_met_conditions_let_the_actions_run(self):
        factories.make_condition(self.automation, field="amount", operator="gt", value="100")
        factories.make_action(self.automation, order=1)

        with patch.object(AutomationExecutor, "_execute_single", return_value=True):
            result = self.orchestrator.run(str(self.automation.id), {"amount": "500"})

        self.assertTrue(result)

    def test_an_evaluation_error_is_logged_as_a_failure(self):
        factories.make_condition(self.automation, field="absent", operator="eq", value="1")
        factories.make_action(self.automation, order=1)

        result = self.orchestrator.run(str(self.automation.id), {"other": "1"})

        self.assertIsNone(result)
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.FAILURE)

    def test_an_execution_error_is_logged_as_a_failure(self):
        factories.make_action(self.automation, order=1, on_failure="retry")

        with patch.object(AutomationExecutor, "_execute_single",
                          side_effect=ExecutionError("always")):
            self.orchestrator.run(str(self.automation.id), {})

        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.FAILURE)

    def test_an_unknown_automation_id_is_handled(self):
        import uuid

        self.assertIsNone(self.orchestrator.run(str(uuid.uuid4()), {}))

    def test_it_accepts_a_string_pk_because_celery_serializes_as_json(self):
        factories.make_action(self.automation, order=1)
        with patch.object(AutomationExecutor, "_execute_single", return_value=True):
            self.assertTrue(self.orchestrator.run(str(self.automation.id), {}))


class SignalRegistryTests(TestCase):

    def test_known_events_map_to_a_signal(self):
        from django.db.models.signals import post_delete, post_save

        self.assertIs(get_signal_for_event("created"), post_save)
        self.assertIs(get_signal_for_event("updated"), post_save)
        self.assertIs(get_signal_for_event("deleted"), post_delete)

    def test_an_unknown_event_maps_to_nothing(self):
        self.assertIsNone(get_signal_for_event("exploded"))


class SignalHandlerTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_created_handler_ignores_updates(self, mock_delay):
        from apps.automations.engine.signal_handler import create_signal_handler

        handler = create_signal_handler(self.automation.id, event="created")
        handler(sender=None, instance=self.tenant, created=False)
        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_an_updated_handler_ignores_creates(self, mock_delay):
        from apps.automations.engine.signal_handler import create_signal_handler

        handler = create_signal_handler(self.automation.id, event="updated")
        handler(sender=None, instance=self.tenant, created=True)
        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_the_task_receives_a_json_safe_pk_and_payload(self, mock_delay):
        # CELERY_TASK_SERIALIZER is 'json': a UUID pk or a UUID inside the payload
        # raises "Object of type UUID is not JSON serializable" at dispatch time.
        from apps.automations.engine.signal_handler import create_signal_handler
        import json

        handler = create_signal_handler(self.automation.id, event="created")
        handler(sender=None, instance=self.tenant, created=True)

        automation_id, payload = mock_delay.call_args[0]
        self.assertIsInstance(automation_id, str)
        json.dumps(payload)  # must not raise


class FieldChangedSignalTests(TestCase):
    """
    `field_changed`: fire when one field's VALUE changes, not when the row is saved.

    The old value cannot be read after the fact, so the wiring takes a snapshot in
    pre_save (apps/automations/signals.py) and compares against it here. These tests
    drive the handler directly; FieldChangedWiringTests below proves the snapshot is
    actually taken.
    """

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.branch = factories.make_branch(self.tenant)

    def _handler(self, **config):
        from apps.automations.engine.signal_handler import create_signal_handler

        return create_signal_handler(
            self.automation.id, event="field_changed", config={"field": "name", **config},
        )

    def _saved_with(self, handler, previous_name, new_name, **kwargs):
        instance = self.branch
        instance._autoflow_prev = {"name": previous_name}
        instance.name = new_name
        handler(sender=None, instance=instance, created=False, **kwargs)
        return instance

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_changed_field_dispatches_the_change(self, mock_delay):
        self._saved_with(self._handler(), "Old", "New")

        automation_id, payload = mock_delay.call_args[0]
        self.assertEqual(automation_id, str(self.automation.id))
        self.assertEqual(payload["_changes"], {"name": {"from": "Old", "to": "New"}})
        # The new values are in the payload too, so a condition can compare them
        # without reaching into `_changes`.
        self.assertEqual(payload["name"], "New")

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_save_that_left_the_field_alone_is_ignored(self, mock_delay):
        # The whole point: an `updated` trigger fires here, a `field_changed` one must
        # not.
        handler = self._handler()
        instance = self.branch
        instance._autoflow_prev = {"name": instance.name}
        instance.address = "a new address"
        handler(sender=None, instance=instance, created=False)

        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_create_has_no_previous_value_and_is_ignored(self, mock_delay):
        handler = self._handler()
        handler(sender=None, instance=self.branch, created=True)

        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_no_snapshot_means_no_guessing(self, mock_delay):
        # Without a pre_save snapshot the handler cannot know what changed, and
        # "assume everything changed" would fire on every unrelated save.
        handler = self._handler()
        handler(sender=None, instance=self.branch, created=False)

        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_previous_value_can_be_supplied_in_kwargs(self, mock_delay):
        handler = self._handler()
        instance = self.branch
        instance.name = "New"
        handler(sender=None, instance=instance, created=False, previous_data={"name": "Old"})

        self.assertEqual(mock_delay.call_args[0][1]["_changes"]["name"]["from"], "Old")

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_from_value_narrows_the_transition(self, mock_delay):
        self._saved_with(self._handler(from_value="Old"), "Old", "New")
        mock_delay.assert_called_once()

        mock_delay.reset_mock()
        self._saved_with(self._handler(from_value="Something else"), "Old", "New")
        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_to_value_narrows_the_target(self, mock_delay):
        self._saved_with(self._handler(to_value="New"), "Old", "New")
        mock_delay.assert_called_once()

        mock_delay.reset_mock()
        self._saved_with(self._handler(to_value="Elsewhere"), "Old", "New")
        mock_delay.assert_not_called()

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_the_string_form_of_a_comparison_value_matches(self, mock_delay):
        # A config written by hand says "True" while the column holds a boolean;
        # refusing that would be surprising rather than safe.
        handler = self._handler(field="is_hq", from_value="False", to_value="True")
        instance = self.branch
        instance._autoflow_prev = {"is_hq": False}
        instance.is_hq = True
        handler(sender=None, instance=instance, created=False)

        mock_delay.assert_called_once()


class FieldChangedWiringTests(TestCase):
    """The pre_save snapshot, connected by register_existing_signal_triggers."""

    def setUp(self):
        self.automation = None

    def tearDown(self):
        from django.db.models.signals import post_save, pre_save
        from apps.tenants.models import Tenant

        if self.automation is None:
            return

        post_save.disconnect(
            sender=Tenant,
            dispatch_uid=f"automation_{self.automation.id}_tenants.Tenant_field_changed",
        )
        pre_save.disconnect(
            sender=Tenant, dispatch_uid="automation_presave_tenants.Tenant",
        )

    @patch("apps.automations.engine.signal_handler.run_automation.delay")
    def test_a_real_save_only_fires_when_the_watched_field_changes(self, mock_delay):
        tenant = factories.make_tenant()
        self.automation = factories.make_automation(tenant)
        factories.make_trigger(self.automation, trigger_type="signal", config={
            "model_name": "tenants.Tenant",
            "event_type": "field_changed",
            "field": "name",
        })

        tenant.slug = "unrelated-change"
        tenant.save()
        mock_delay.assert_not_called()

        tenant.name = "Renamed"
        tenant.save()

        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][1]
        self.assertEqual(payload["_changes"]["name"]["to"], "Renamed")


class AutomationCompletedChainingTests(TestCase):
    """
    `automation_completed`: the Sub-Zap case (ROADMAP section 4.1).

    A -> B is the feature; A -> B -> A is the bug it comes with. The depth counter
    travels inside the payload, so it survives the Celery boundary.
    """

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.source = factories.make_automation(self.tenant, name="Source")
        self.target = factories.make_automation(self.tenant, name="Target")

        factories.make_action(self.source, order=1)
        factories.make_action(self.target, order=1)

    def _follow(self, follower, source, status="success"):
        return factories.make_trigger(follower, trigger_type="automation_completed", config={
            "source_automation_id": str(source.id),
            "trigger_on_status": status,
        })

    def _run(self, automation, payload=None, expect=True):
        with patch.object(AutomationExecutor, "_execute_single", return_value=True):
            return AutomationOrchestrator().run(str(automation.id), payload or {})

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_successful_run_starts_the_follower(self, dispatch):
        self._follow(self.target, self.source)

        self._run(self.source)

        dispatch.assert_called_once()
        automation_id, payload = dispatch.call_args[0]
        self.assertEqual(str(automation_id), str(self.target.id))
        self.assertEqual(payload["source_automation_id"], str(self.source.id))
        self.assertEqual(payload["source_automation_name"], "Source")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["_chain_depth"], 1)

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_follower_waiting_for_another_status_is_not_started(self, dispatch):
        self._follow(self.target, self.source, status="failure")

        self._run(self.source)

        dispatch.assert_not_called()

    @patch("apps.automations.runtime.dispatch_automation")
    def test_an_unmet_condition_starts_a_skipped_follower(self, dispatch):
        factories.make_condition(self.source, field="amount", operator="gt", value="100")
        self._follow(self.target, self.source, status="skipped")

        self._run(self.source, {"amount": "5"})

        self.assertEqual(dispatch.call_args[0][1]["status"], "skipped")

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_disabled_automation_does_not_start_a_chain(self, dispatch):
        self._follow(self.target, self.source)

        self.source.is_active = False
        self.source.save()

        self._run(self.source)

        dispatch.assert_not_called()

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_paused_run_does_not_start_a_chain(self, dispatch):
        # 'waiting' is not finished - the second half has not happened yet.
        factories.make_step(
            self.source, key="wait", step_type="wait", action_type="",
            config={"duration": "1h"},
        )
        self._follow(self.target, self.source)

        with patch.object(AutomationExecutor, "execute_graph") as graph:
            from apps.automations.engine.executor import GraphExecutionResult

            graph.return_value = GraphExecutionResult(
                status="waiting", context={"steps": {}, "vars": {}}, step_results=[],
            )
            AutomationOrchestrator().run(str(self.source.id), {}, schedule_continuation=MagicMock())

        dispatch.assert_not_called()

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_trigger_may_not_reach_across_tenants(self, dispatch):
        other_tenant = factories.make_tenant(name="Other")
        outsider = factories.make_automation(other_tenant, name="Outsider")
        self._follow(outsider, self.source)

        self._run(self.source)

        dispatch.assert_not_called()

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_follower_may_not_borrow_another_tenants_automation(self, dispatch):
        # The trigger lives in this tenant but names an automation from elsewhere:
        # the config is the only link, so the fan-out has to filter on the source's
        # tenant as well.
        other_tenant = factories.make_tenant(name="Other")
        outsider = factories.make_automation(other_tenant, name="Outsider")
        self._follow(self.target, outsider)

        self._run(self.source)

        dispatch.assert_not_called()

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_loop_stops_at_the_depth_limit(self, dispatch):
        from apps.automations.runtime import MAX_CHAIN_DEPTH

        # A -> B and B -> A. Nothing in the data model forbids this pair.
        self._follow(self.target, self.source)
        self._follow(self.source, self.target)

        current = self.source
        payload = {}
        hops = []

        for _ in range(MAX_CHAIN_DEPTH + 3):
            dispatch.reset_mock()
            self._run(current, payload)

            if not dispatch.called:
                break

            next_id, payload = dispatch.call_args[0]
            hops.append(payload["_chain_depth"])
            current = Automation.objects.get(id=next_id)

        self.assertEqual(hops, list(range(1, MAX_CHAIN_DEPTH + 1)))
        self.assertEqual(payload["_chain_depth"], MAX_CHAIN_DEPTH)

    def test_chain_depth_never_goes_below_zero(self):
        # The depth is read out of a tenant-authored context, so it can be any
        # integer - including a negative one.
        from apps.automations.runtime import chain_depth

        self.assertEqual(chain_depth({"_chain_depth": -999999}), 0)
        self.assertEqual(chain_depth({"trigger": {"_chain_depth": -3}}), 0)
        self.assertEqual(chain_depth({"_chain_depth": 2}), 2)

    @patch("apps.automations.runtime.dispatch_automation")
    def test_a_negative_depth_cannot_bypass_the_depth_limit(self, dispatch):
        # Read as-is, -999999 would make `depth >= MAX_CHAIN_DEPTH` false on every
        # hop and an A -> B -> A pair would fan out forever. Clamped, the next hop
        # is 1 - the same counter a fresh run gets.
        self._follow(self.target, self.source)

        self._run(self.source, {"_chain_depth": -999999})

        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args[0][1]["_chain_depth"], 1)
