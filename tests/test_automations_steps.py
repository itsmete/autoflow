"""
Faz 1.B: the step graph (AutomationStep + AutomationEdge).

AutomationAction is a linear list, and every way of putting branching on top of an
`order` integer ends up re-implementing a graph badly (ROADMAP §5.1). These tests
cover the graph itself and the two bridges that keep the old list authoritative:

- `sync_steps_from_actions()` - the runtime bridge the actions API will use.
- `migrate_actions_to_steps()` - the data migration that copies existing rows.

Both are copies, not moves: AutomationAction stays readable for a release, so a test
that only checked the new tables would not notice the old ones being clobbered.

The second half of the file (T-102) covers the HTTP surface: polymorphic step config
validation, whole-graph validation (keys, cycles, reachability), and the dual-write
that keeps `actions` and `steps`+`edges` from ever disagreeing.
"""
import importlib

from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.test import TestCase

# A module whose name starts with a digit cannot be written as an import statement,
# and the migration is the only place this function lives (migrations must stay
# self-contained - importing it from models.py would break once models move on).
migrate_actions_to_steps = importlib.import_module(
    "apps.automations.migrations.0009_automationstep_automationedge"
).migrate_actions_to_steps
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.automations.models import (
    Automation,
    AutomationAction,
    AutomationEdge,
    AutomationStep,
    StepTypes,
    sync_actions_from_steps,
    sync_steps_from_actions,
)
from apps.automations.serializers.steps import (
    AutomationEdgeWriteSerializer,
    AutomationStepWriteSerializer,
    validate_step_graph,
)
from tests import factories
from tests.utils import CacheTestMixin


# Not credentials for anything real: the providers are never called here, but the
# factories store them as-is and a channel_type mismatch is what several tests check.
WHATSAPP_CREDENTIALS = {
    "access_token": "test-token",
    "phone_number_id": "1234567890",
    "api_version": "v23.0",
}

WEBHOOK_CONFIG = {"url": "https://example.com/hook", "method": "POST", "headers": {}}


def webhook_step(key, **overrides):
    """A step dict that is valid as-is, so each test can override just the bit it means."""
    step = {
        "key": key,
        "step_type": "action",
        "action_type": "webhook",
        "config": dict(WEBHOOK_CONFIG),
    }
    step.update(overrides)
    return step



class StepModelTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, branch=self.branch)

    def test_defaults_describe_a_plain_action_step(self):
        # The default must be the boring case: an old-style action with nothing set.
        step = AutomationStep.objects.create(automation=self.automation, key="1")

        self.assertEqual(step.step_type, StepTypes.ACTION)
        self.assertEqual(step.action_type, "")
        self.assertEqual(step.on_failure, "stop")
        self.assertEqual(step.config, {})
        self.assertIsNone(step.position)
        self.assertIsNone(step.channel)
        self.assertIsNone(step.template)
        self.assertTrue(step.is_active)

    def test_position_round_trips_as_json(self):
        step = factories.make_step(self.automation, position={"x": 120, "y": 40})
        step.refresh_from_db()

        self.assertEqual(step.position, {"x": 120, "y": 40})

    def test_str_names_the_automation_the_key_and_the_type(self):
        step = factories.make_step(self.automation, key="send_sms", step_type="action")

        self.assertEqual(str(step), f"{self.automation.name} - send_sms (action)")

    def test_steps_are_ordered_by_creation(self):
        first = factories.make_step(self.automation, key="1")
        second = factories.make_step(self.automation, key="2")
        third = factories.make_step(self.automation, key="3")

        self.assertEqual(
            list(self.automation.steps.all()),
            [first, second, third],
        )

    def test_a_step_is_not_a_tenant_scoped_row_its_automation_is(self):
        # Same shape as AutomationAction: the model has no tenant/branch column, so
        # get_tenant() is None and the scope has to be read off the automation. The
        # AGENTS bug list is explicit about not reading `instance.tenant_id` here -
        # it does not exist.
        step = factories.make_step(self.automation)

        self.assertIsNone(step.get_tenant())
        self.assertEqual(step.get_scope_ids(), (None, None))
        self.assertEqual(step.automation.get_tenant(), self.tenant)

    def test_deleting_the_automation_takes_its_steps_and_edges_with_it(self):
        step = factories.make_step(self.automation)
        factories.make_edge(self.automation, None, step)

        self.automation.delete()

        self.assertEqual(AutomationStep.objects.count(), 0)
        self.assertEqual(AutomationEdge.objects.count(), 0)


class StepKeyUniquenessTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def test_the_same_key_twice_in_one_automation_is_rejected(self):
        # Edges and expressions point at steps by key, so a duplicate would make the
        # graph ambiguous rather than merely untidy.
        factories.make_step(self.automation, key="send_sms")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                factories.make_step(self.automation, key="send_sms")

    def test_the_same_key_in_two_automations_is_fine(self):
        other = factories.make_automation(self.tenant, name="Other")

        factories.make_step(self.automation, key="1")
        step = factories.make_step(other, key="1")

        self.assertEqual(step.key, "1")
        self.assertEqual(AutomationStep.objects.filter(key="1").count(), 2)


class EdgeModelTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def test_an_entry_edge_has_no_source_step(self):
        # from_step=None is how "the graph starts here" is spelled; a synthetic START
        # step would have to be excluded from execution everywhere.
        first = factories.make_step(self.automation, key="1")

        entry = factories.make_edge(self.automation, None, first)

        self.assertIsNone(entry.from_step)
        self.assertEqual(entry.to_step, first)
        self.assertEqual(entry.branch_key, "")
        self.assertEqual(str(entry), f"{self.automation.name}: START -> 1")

    def test_str_of_an_inner_edge_names_both_keys(self):
        first = factories.make_step(self.automation, key="1")
        second = factories.make_step(self.automation, key="2")

        edge = factories.make_edge(self.automation, first, second)

        self.assertEqual(str(edge), f"{self.automation.name}: 1 -> 2")

    def test_a_branch_step_can_fan_out_on_named_branches(self):
        branch = factories.make_step(self.automation, key="vip?", step_type="branch", action_type="")
        yes = factories.make_step(self.automation, key="wa", action_type="whatsapp")
        no = factories.make_step(self.automation, key="sms", action_type="sms")
        rest = factories.make_step(self.automation, key="log")

        factories.make_edge(self.automation, branch, yes, branch_key="true", order=0)
        factories.make_edge(self.automation, branch, no, branch_key="false", order=1)
        factories.make_edge(self.automation, branch, rest, branch_key="default", order=2)

        self.assertEqual(
            [(e.branch_key, e.to_step.key) for e in branch.outgoing.all()],
            [("true", "wa"), ("false", "sms"), ("default", "log")],
        )
        self.assertEqual(yes.incoming.count(), 1)

    def test_edges_are_ordered_by_order_then_creation(self):
        # Written out of order on purpose: Meta.ordering is what makes "the next
        # edge" deterministic, and created_at alone would not.
        first = factories.make_step(self.automation, key="1")
        second = factories.make_step(self.automation, key="2")
        third = factories.make_step(self.automation, key="3")

        factories.make_edge(self.automation, first, third, order=2)
        factories.make_edge(self.automation, first, second, order=1)

        self.assertEqual(
            [e.to_step.key for e in self.automation.edges.all()],
            ["2", "3"],
        )

    def test_an_edge_is_not_a_tenant_scoped_row_its_automation_is(self):
        step = factories.make_step(self.automation)
        edge = factories.make_edge(self.automation, None, step)

        self.assertIsNone(edge.get_tenant())
        self.assertEqual(edge.get_scope_ids(), (None, None))


class SyncStepsFromActionsTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def test_an_automation_without_actions_gets_no_graph(self):
        steps, edges = sync_steps_from_actions(self.automation)

        self.assertEqual(steps, [])
        self.assertEqual(edges, [])
        self.assertEqual(self.automation.steps.count(), 0)
        self.assertEqual(self.automation.edges.count(), 0)

    def test_a_single_action_becomes_one_step_and_one_entry_edge(self):
        action = factories.make_action(self.automation, action_type="sms", order=1)

        steps, edges = sync_steps_from_actions(self.automation)

        self.assertEqual(len(steps), 1)
        self.assertEqual(len(edges), 1)
        self.assertEqual(steps[0].key, "1")
        self.assertEqual(steps[0].action_type, action.action_type)
        self.assertEqual(edges[0].from_step, None)
        self.assertEqual(edges[0].to_step, steps[0])
        self.assertEqual(edges[0].order, 0)

    def test_three_actions_become_a_chain_in_order(self):
        channel = factories.make_channel(self.tenant)
        template = factories.make_template(self.tenant)

        # Deliberately not in order=1,2,3 insertion order: the chain must follow
        # `order`, not the row order the database happened to hand back.
        third = factories.make_action(self.automation, action_type="sms", order=3,
                                      on_failure="continue", config={"to": "+90", "body": "hi"})
        first = factories.make_action(self.automation, action_type="email", order=1,
                                      on_failure="retry")
        second = AutomationAction.objects.create(
            automation=self.automation,
            action_type="whatsapp",
            order=2,
            on_failure="stop",
            config={"to": "+90", "body": "yo"},
            channel=channel,
            template=template,
        )

        steps, edges = sync_steps_from_actions(self.automation)

        self.assertEqual([s.key for s in steps], ["1", "2", "3"])
        self.assertEqual([s.action_type for s in steps], ["email", "whatsapp", "sms"])
        self.assertEqual([s.on_failure for s in steps], ["retry", "stop", "continue"])
        self.assertEqual([s.config for s in steps], [first.config, second.config, third.config])
        self.assertEqual(steps[1].channel, channel)
        self.assertEqual(steps[1].template, template)
        self.assertIsNone(steps[0].channel)
        self.assertTrue(all(s.step_type == StepTypes.ACTION for s in steps))

        self.assertEqual(len(edges), 3)
        self.assertIsNone(edges[0].from_step)
        self.assertEqual(edges[0].to_step, steps[0])
        self.assertEqual(edges[0].order, 0)
        for i in (1, 2):
            self.assertEqual(edges[i].from_step, steps[i - 1])
            self.assertEqual(edges[i].to_step, steps[i])
            self.assertEqual(edges[i].order, i)
        self.assertEqual(
            [(e.from_step.key if e.from_step else None, e.to_step.key)
             for e in self.automation.edges.all()],
            [(None, "1"), ("1", "2"), ("2", "3")],
        )

    def test_calling_it_twice_does_not_duplicate_the_graph(self):
        factories.make_action(self.automation, order=1)
        factories.make_action(self.automation, order=2, action_type="sms")

        sync_steps_from_actions(self.automation)
        steps, edges = sync_steps_from_actions(self.automation)

        self.assertEqual([s.key for s in steps], ["1", "2"])
        self.assertEqual(self.automation.steps.count(), 2)
        self.assertEqual(self.automation.edges.count(), 2)

    def test_it_rebuilds_after_the_actions_change(self):
        # The graph is derived state: re-running is how it follows the list.
        action = factories.make_action(self.automation, order=1)
        sync_steps_from_actions(self.automation)

        action.delete()
        steps, _ = sync_steps_from_actions(self.automation)

        self.assertEqual(steps, [])
        self.assertEqual(self.automation.steps.count(), 0)
        self.assertEqual(self.automation.edges.count(), 0)


class ActionToStepDataMigrationTests(TestCase):
    """
    The forward data migration, exercised directly.

    Passing django's real app registry keeps the historical `apps.get_model` lookup
    honest for the field names; a migration test that hand-built the models would
    not catch a typo in `channel_id`/`template_id`.
    """

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def _migrate(self):
        migrate_actions_to_steps(django_apps, None)

    def test_existing_actions_are_copied_into_a_linear_chain(self):
        channel = factories.make_channel(self.tenant)
        template = factories.make_template(self.tenant)
        factories.make_action(self.automation, action_type="sms", order=1,
                              on_failure="continue", config={"to": "+90", "body": "a"})
        AutomationAction.objects.create(
            automation=self.automation,
            action_type="email",
            order=2,
            on_failure="retry",
            config={"to": "a@b.c"},
            channel=channel,
            template=template,
        )

        self._migrate()

        steps = list(self.automation.steps.all())
        self.assertEqual([s.key for s in steps], ["1", "2"])
        self.assertEqual([s.step_type for s in steps], ["action", "action"])
        self.assertEqual([s.action_type for s in steps], ["sms", "email"])
        self.assertEqual([s.on_failure for s in steps], ["continue", "retry"])
        self.assertEqual(steps[1].channel_id, channel.id)
        self.assertEqual(steps[1].template_id, template.id)

        self.assertEqual(
            [(e.from_step.key if e.from_step else None, e.to_step.key, e.order)
             for e in self.automation.edges.all()],
            [(None, "1", 0), ("1", "2", 1)],
        )

    def test_the_old_rows_are_kept(self):
        # A copy, not a move: AutomationAction has to stay readable for a release.
        factories.make_action(self.automation, order=1)

        self._migrate()

        self.assertEqual(self.automation.actions.count(), 1)
        self.assertEqual(self.automation.steps.count(), 1)

    def test_an_automation_that_already_has_steps_is_left_alone(self):
        factories.make_action(self.automation, order=1)
        existing = factories.make_step(self.automation, key="handwritten")

        self._migrate()

        self.assertEqual([s.key for s in self.automation.steps.all()], ["handwritten"])
        self.assertEqual(self.automation.steps.get(), existing)
        self.assertEqual(self.automation.edges.count(), 0)

    def test_an_automation_without_actions_is_skipped(self):
        self._migrate()

        self.assertEqual(AutomationStep.objects.count(), 0)
        self.assertEqual(AutomationEdge.objects.count(), 0)

    def test_running_it_again_is_a_no_op(self):
        factories.make_action(self.automation, order=1)
        factories.make_action(self.automation, order=2, action_type="sms")

        self._migrate()
        self._migrate()

        self.assertEqual(self.automation.steps.count(), 2)
        self.assertEqual(self.automation.edges.count(), 2)

    def test_each_automation_gets_its_own_chain(self):
        other = factories.make_automation(self.tenant, name="Other")
        factories.make_action(self.automation, order=1)
        factories.make_action(other, order=1, action_type="sms")

        self._migrate()

        self.assertEqual(self.automation.steps.get().key, "1")
        self.assertEqual(other.steps.get().key, "1")
        self.assertEqual(other.edges.get().automation, other)


class SyncActionsFromStepsTests(TestCase):
    """
    The reverse bridge: a graph-only payload still has to produce AutomationAction
    rows, because the executor that exists today reads that table.
    """

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def test_a_graph_without_action_steps_produces_no_actions(self):
        factories.make_step(self.automation, key="wait", step_type="wait",
                            action_type="", config={"duration": "1h"})

        self.assertEqual(sync_actions_from_steps(self.automation), [])
        self.assertEqual(self.automation.actions.count(), 0)

    def test_only_action_steps_become_actions_in_creation_order(self):
        channel = factories.make_channel(self.tenant)
        template = factories.make_template(self.tenant)

        factories.make_step(self.automation, key="wait", step_type="wait",
                            action_type="", config={"duration": "30s"})
        factories.make_step(self.automation, key="mail", action_type="email",
                            channel=channel, template=template, on_failure="retry",
                            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"})
        factories.make_step(self.automation, key="hook", action_type="webhook",
                            on_failure="continue")

        actions = sync_actions_from_steps(self.automation)

        self.assertEqual([a.action_type for a in actions], ["email", "webhook"])
        self.assertEqual([a.order for a in actions], [1, 2])
        self.assertEqual([a.on_failure for a in actions], ["retry", "continue"])
        self.assertEqual(actions[0].channel, channel)
        self.assertEqual(actions[0].template, template)
        self.assertIsNone(actions[1].channel)

    def test_it_replaces_the_previous_action_rows(self):
        # Derived state, like the other direction: a hand-written legacy row must not
        # survive a rebuild, or the two representations would disagree forever.
        factories.make_action(self.automation, order=9)
        factories.make_step(self.automation, key="hook", action_type="webhook")

        actions = sync_actions_from_steps(self.automation)

        self.assertEqual(len(actions), 1)
        self.assertEqual(self.automation.actions.count(), 1)
        self.assertEqual(self.automation.actions.get().order, 1)


class StepConfigSerializerTests(TestCase):
    """validate_step_config, through the serializer that actually guards the API."""

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.email_channel = factories.make_channel(self.tenant, name="Mail")
        self.sms_channel = factories.make_channel(
            self.tenant, name="Sms", channel_type="sms",
            credentials=dict(factories.SMS_CREDENTIALS),
        )
        self.email_template = factories.make_template(self.tenant)
        self.sms_template = factories.make_template(
            self.tenant, name="SmsTemplate", channel_type="sms",
        )

    def serializer(self, **overrides):
        data = webhook_step("1")
        data.update(overrides)
        return AutomationStepWriteSerializer(data=data)

    def errors_of(self, **overrides):
        serializer = self.serializer(**overrides)
        self.assertFalse(serializer.is_valid(), "expected the step to be rejected")
        return serializer.errors

    # -- action ----------------------------------------------------------------

    def test_a_webhook_action_step_is_valid(self):
        serializer = self.serializer()

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["key"], "1")
        self.assertEqual(serializer.validated_data["config"], WEBHOOK_CONFIG)

    def test_an_action_step_without_action_type_is_rejected(self):
        errors = self.errors_of(action_type="")

        self.assertIn("non_field_errors", errors)

    def test_an_unknown_action_type_is_rejected(self):
        self.errors_of(action_type="carrier_pigeon")

    def test_an_email_step_needs_a_channel(self):
        errors = self.errors_of(
            action_type="email",
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

        self.assertIn("non_field_errors", errors)

    def test_an_email_step_with_a_matching_channel_is_valid(self):
        serializer = self.serializer(
            action_type="email",
            channel=self.email_channel.id,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_channel_of_the_wrong_type_is_rejected(self):
        self.errors_of(
            action_type="email",
            channel=self.sms_channel.id,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

    def test_a_template_of_the_wrong_channel_type_is_rejected(self):
        # An e-mail body sent through the SMS provider would silently lose its
        # formatting; the only place that can see both halves is here.
        self.errors_of(
            action_type="email",
            channel=self.email_channel.id,
            template=self.sms_template.id,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

    def test_a_template_of_the_matching_type_is_accepted(self):
        serializer = self.serializer(
            action_type="email",
            channel=self.email_channel.id,
            template=self.email_template.id,
            config={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_an_invalid_action_config_is_rejected(self):
        # url is required by the webhook schema.
        self.errors_of(config={"method": "POST", "headers": {}})

    # -- filter ----------------------------------------------------------------

    def test_a_filter_step_needs_conditions(self):
        self.errors_of(step_type="filter", action_type="", config={})
        self.errors_of(step_type="filter", action_type="", config={"conditions": []})

    def test_a_filter_step_with_a_condition_is_valid(self):
        config = {"conditions": [{"field": "amount", "operator": "gt", "value": 100}]}
        serializer = self.serializer(step_type="filter", action_type="", config=config)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["config"], config)

    def test_a_filter_condition_is_validated_like_an_automation_condition(self):
        # Missing `operator`: the condition serializer, not a hand-rolled check, is
        # what decides.
        self.errors_of(step_type="filter", action_type="",
                       config={"conditions": [{"field": "amount"}]})

    # -- branch ----------------------------------------------------------------

    def branch_config(self, **overrides):
        config = {
            "branches": [
                {"key": "true",
                 "conditions": [{"field": "amount", "operator": "gt", "value": 100}]},
                {"key": "false",
                 "conditions": [{"field": "amount", "operator": "lte", "value": 100}]},
            ],
        }
        config.update(overrides)
        return config

    def test_a_branch_step_needs_branches(self):
        self.errors_of(step_type="branch", action_type="", config={})
        self.errors_of(step_type="branch", action_type="", config={"branches": []})

    def test_a_branch_step_with_two_declared_branches_is_valid(self):
        serializer = self.serializer(step_type="branch", action_type="",
                                     config=self.branch_config())

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_duplicate_branch_keys_are_rejected(self):
        config = self.branch_config(branches=[
            {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
            {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 2}]},
        ])

        self.errors_of(step_type="branch", action_type="", config=config)

    def test_a_branch_without_conditions_is_rejected(self):
        config = self.branch_config(branches=[{"key": "true"}])

        self.errors_of(step_type="branch", action_type="", config=config)

    def test_a_blank_branch_key_is_rejected(self):
        config = self.branch_config(branches=[
            {"key": "  ", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
        ])

        self.errors_of(step_type="branch", action_type="", config=config)

    def test_a_non_string_default_branch_is_rejected(self):
        self.errors_of(step_type="branch", action_type="",
                       config=self.branch_config(default=7))

    # -- wait ------------------------------------------------------------------

    def test_a_wait_step_accepts_seconds_and_suffixed_durations(self):
        for duration in (3600, "30s", "15m", "24h", "7d"):
            serializer = self.serializer(step_type="wait", action_type="",
                                         config={"duration": duration})
            self.assertTrue(serializer.is_valid(), (duration, serializer.errors))

    def test_a_wait_step_accepts_an_iso_datetime_and_an_expression(self):
        for until in ("2030-01-01T10:00:00Z", "{{trigger.due_at}}"):
            serializer = self.serializer(step_type="wait", action_type="",
                                         config={"until": until})
            self.assertTrue(serializer.is_valid(), (until, serializer.errors))

    def test_a_wait_step_rejects_a_malformed_duration(self):
        for duration in ("24x", "1.5h", "", 0, -5, True):
            self.errors_of(step_type="wait", action_type="", config={"duration": duration})

    def test_a_wait_step_needs_duration_or_until(self):
        self.errors_of(step_type="wait", action_type="", config={})

    def test_a_wait_step_rejects_a_malformed_until(self):
        for until in ("not-a-date", "", 7):
            self.errors_of(step_type="wait", action_type="", config={"until": until})

    # -- set_variable ----------------------------------------------------------

    def test_a_set_variable_step_accepts_named_variables(self):
        serializer = self.serializer(step_type="set_variable", action_type="",
                                     config={"variables": {"tier": "gold"}})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_set_variable_step_needs_a_non_empty_dict(self):
        self.errors_of(step_type="set_variable", action_type="", config={})
        self.errors_of(step_type="set_variable", action_type="",
                       config={"variables": {}})
        self.errors_of(step_type="set_variable", action_type="",
                       config={"variables": ["tier"]})

    def test_a_set_variable_step_rejects_blank_names(self):
        self.errors_of(step_type="set_variable", action_type="",
                       config={"variables": {"  ": "gold"}})

    # -- step shape ------------------------------------------------------------

    def test_the_key_is_stripped(self):
        serializer = self.serializer(key="  send_sms  ")

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["key"], "send_sms")

    def test_a_blank_key_is_rejected(self):
        self.errors_of(key="   ")

    def test_the_config_must_be_a_dict(self):
        self.errors_of(config="nope")

    def test_an_unknown_step_type_only_needs_a_dict_config(self):
        # loop/ai_* have no schema until Faz 1.D; they must still be storable, or the
        # canvas could not persist a graph it is about to be able to run.
        serializer = self.serializer(step_type="loop", action_type="",
                                     config={"over": "items"})

        self.assertTrue(serializer.is_valid(), serializer.errors)


class StepGraphValidationTests(TestCase):
    """Properties of the whole graph - the things no single step can be blamed for."""

    def test_a_linear_graph_without_edges_gets_a_generated_chain(self):
        edges = validate_step_graph([webhook_step("a"), webhook_step("b")], None)

        self.assertEqual(
            [(e["from_step"], e["to_step"], e["order"]) for e in edges],
            [(None, "a", 0), ("a", "b", 1)],
        )

    def test_a_branching_graph_must_declare_its_edges(self):
        branch = {"key": "check", "step_type": "branch", "action_type": "",
                  "config": {"branches": [
                      {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
                  ]}}

        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([branch], None)

        self.assertIn("edges", ctx.exception.detail)

    def test_duplicate_step_keys_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a"), webhook_step("a")], None)

        self.assertIn("steps", ctx.exception.detail)

    def test_an_edge_pointing_at_an_unknown_source_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a")],
                                [{"from_step": "ghost", "to_step": "a"}])

        self.assertIn("edges", ctx.exception.detail)

    def test_an_edge_pointing_at_an_unknown_target_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a")],
                                [{"from_step": None, "to_step": "ghost"}])

        self.assertIn("edges", ctx.exception.detail)

    def test_a_self_loop_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a")],
                                [{"from_step": None, "to_step": "a"},
                                 {"from_step": "a", "to_step": "a"}])

        self.assertIn("edges", ctx.exception.detail)

    def test_a_two_node_cycle_is_rejected(self):
        edges = [
            {"from_step": None, "to_step": "a"},
            {"from_step": "a", "to_step": "b"},
            {"from_step": "b", "to_step": "a"},
        ]

        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a"), webhook_step("b")], edges)

        self.assertIn("edges", ctx.exception.detail)

    def test_a_three_node_cycle_is_rejected(self):
        edges = [
            {"from_step": None, "to_step": "a"},
            {"from_step": "a", "to_step": "b"},
            {"from_step": "b", "to_step": "c"},
            {"from_step": "c", "to_step": "a"},
        ]

        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph(
                [webhook_step("a"), webhook_step("b"), webhook_step("c")], edges
            )

        self.assertIn("edges", ctx.exception.detail)

    def test_a_merged_diamond_is_not_a_cycle(self):
        # Two branches converging on one step is the shape the graph exists for.
        edges = [
            {"from_step": None, "to_step": "check"},
            {"from_step": "check", "to_step": "a", "branch_key": "true"},
            {"from_step": "check", "to_step": "b", "branch_key": "false"},
            {"from_step": "a", "to_step": "merge"},
            {"from_step": "b", "to_step": "merge"},
        ]
        steps = [
            {"key": "check", "step_type": "branch", "action_type": "",
             "config": {"branches": [
                 {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
                 {"key": "false", "conditions": [{"field": "a", "operator": "eq", "value": 2}]},
             ]}},
            webhook_step("a"),
            webhook_step("b"),
            webhook_step("merge"),
        ]

        normalized = validate_step_graph(steps, edges)

        self.assertEqual([e["to_step"] for e in normalized], ["check", "a", "b", "merge", "merge"])

    def test_an_unreachable_step_is_rejected(self):
        edges = [
            {"from_step": None, "to_step": "a"},
            {"from_step": "a", "to_step": "b"},
        ]

        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph(
                [webhook_step("a"), webhook_step("b"), webhook_step("orphan")], edges
            )

        self.assertIn("edges", ctx.exception.detail)

    def test_a_missing_entry_edge_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([webhook_step("a"), webhook_step("b")],
                                [{"from_step": "a", "to_step": "b"}])

        self.assertIn("edges", ctx.exception.detail)

    def test_an_undeclared_branch_key_is_rejected(self):
        branch = {"key": "check", "step_type": "branch", "action_type": "",
                  "config": {"branches": [
                      {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
                  ]}}
        edges = [
            {"from_step": None, "to_step": "check"},
            {"from_step": "check", "to_step": "a", "branch_key": "maybe"},
        ]

        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([branch, webhook_step("a")], edges)

        self.assertIn("edges", ctx.exception.detail)

    def test_declared_and_default_branch_keys_are_accepted(self):
        branch = {"key": "check", "step_type": "branch", "action_type": "",
                  "config": {"default": "fallback", "branches": [
                      {"key": "true", "conditions": [{"field": "a", "operator": "eq", "value": 1}]},
                  ]}}
        edges = [
            {"from_step": None, "to_step": "check"},
            {"from_step": "check", "to_step": "a", "branch_key": "true"},
            {"from_step": "check", "to_step": "b", "branch_key": "fallback"},
            {"from_step": "check", "to_step": "c", "branch_key": "default"},
        ]
        steps = [branch, webhook_step("a"), webhook_step("b"), webhook_step("c")]

        normalized = validate_step_graph(steps, edges)

        self.assertEqual([e["branch_key"] for e in normalized], ["", "true", "fallback", "default"])

    def test_edges_without_steps_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_step_graph([], [{"from_step": None, "to_step": "a"}])

        self.assertIn("edges", ctx.exception.detail)

    def test_an_empty_graph_is_empty(self):
        self.assertEqual(validate_step_graph([], None), [])


class EdgeWriteSerializerTests(TestCase):

    def test_a_blank_from_step_means_the_entry_edge(self):
        # "" is how a canvas says "the start of the flow"; the column spells it None.
        serializer = AutomationEdgeWriteSerializer(
            data={"from_step": "", "to_step": "a"}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data["from_step"])
        self.assertEqual(serializer.validated_data["branch_key"], "")
        self.assertEqual(serializer.validated_data["order"], 0)

    def test_to_step_is_required(self):
        serializer = AutomationEdgeWriteSerializer(data={"from_step": None})

        self.assertFalse(serializer.is_valid())
        self.assertIn("to_step", serializer.errors)

    def test_a_negative_order_is_rejected(self):
        serializer = AutomationEdgeWriteSerializer(data={"to_step": "a", "order": -1})

        self.assertFalse(serializer.is_valid())


class AutomationGraphAPITests(CacheTestMixin, TestCase):
    """
    The HTTP surface: `steps`/`edges` next to the legacy `actions` list.

    The compatibility contract (ROADMAP §5.1) is bidirectional - an old client's
    `actions` payload must produce a graph, and a graph payload must produce action
    rows - so every test here asserts the representation the client did NOT send.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant, "A")
        self.owner = factories.make_owner(self.tenant)

        self.email_channel = factories.make_channel(self.tenant, self.branch, name="Mail")
        self.sms_channel = factories.make_channel(
            self.tenant, self.branch, name="Sms", channel_type="sms",
            credentials=dict(factories.SMS_CREDENTIALS),
        )
        self.whatsapp_channel = factories.make_channel(
            self.tenant, self.branch, name="Wa", channel_type="whatsapp",
            credentials=dict(WHATSAPP_CREDENTIALS),
        )
        self.email_template = factories.make_template(self.tenant, branch=self.branch)

        self.client.force_authenticate(user=self.owner)

    def fields_of(self, response):
        # core/exceptions.py wraps DRF's detail dict as {"success": False, "error":
        # {"fields": {...}}}; the field names are what these tests are about.
        return response.data["error"]["fields"]

    def payload(self, **overrides):
        payload = {
            "name": "Graph",
            "allowed_roles": ["super_admin", "owner"],
            "metadata": {},
            "tenant": str(self.tenant.id),
            "branch": str(self.branch.id),
            "triggers": [{"trigger_type": "manual", "config": {}, "initial_data": {}}],
            "conditions": [
                {"field": "amount", "operator": "gt", "value": "100",
                 "logical_operator": "and"}
            ],
        }
        payload.update(overrides)
        return payload

    def branch_steps(self):
        return [
            {
                "key": "check",
                "step_type": "branch",
                "config": {
                    "branches": [
                        {"key": "true", "conditions": [
                            {"field": "amount", "operator": "gt", "value": 100}]},
                        {"key": "false", "conditions": [
                            {"field": "amount", "operator": "lte", "value": 100}]},
                    ],
                    "default": "false",
                },
                "position": {"x": 40, "y": 10},
            },
            {
                "key": "wa",
                "step_type": "action",
                "action_type": "whatsapp",
                "channel": str(self.whatsapp_channel.id),
                "config": {"to": "+905551112233", "message": "hi"},
            },
            {
                "key": "sms",
                "step_type": "action",
                "action_type": "sms",
                "channel": str(self.sms_channel.id),
                "config": {"to": "+905551112233", "message": "hi"},
            },
            {"key": "pause", "step_type": "wait", "config": {"duration": "24h"}},
        ]

    def branch_edges(self):
        return [
            {"from_step": None, "to_step": "check"},
            {"from_step": "check", "to_step": "wa", "branch_key": "true", "order": 0},
            {"from_step": "check", "to_step": "sms", "branch_key": "false", "order": 1},
            {"from_step": "wa", "to_step": "pause", "order": 0},
            {"from_step": "sms", "to_step": "pause", "order": 1},
        ]

    # -- legacy actions -> graph ----------------------------------------------

    def test_a_legacy_actions_payload_also_populates_the_graph(self):
        response = self.client.post('/api/v1/automations/', self.payload(actions=[
            {"action_type": "webhook", "order": 1, "on_failure": "stop",
             "config": {"url": "https://example.com/hook", "method": "POST",
                        "headers": {}}},
        ]), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.assertEqual([s["key"] for s in response.data["steps"]], ["1"])
        self.assertEqual(response.data["steps"][0]["action_type"], "webhook")
        self.assertEqual(
            [(e["from_step"], e["to_step"]) for e in response.data["edges"]],
            [(None, "1")],
        )

        # ... and the same graph comes back out of the cached detail read.
        automation = Automation.objects.get(name="Graph")
        detail = self.client.get(f'/api/v1/automations/{automation.id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        self.assertEqual([s["key"] for s in detail.data["steps"]], ["1"])
        self.assertEqual([e["to_step"] for e in detail.data["edges"]], ["1"])

    def test_a_template_can_be_attached_to_an_action(self):
        response = self.client.post('/api/v1/automations/', self.payload(actions=[
            {"action_type": "email", "channel": str(self.email_channel.id),
             "template": str(self.email_template.id), "order": 1, "on_failure": "stop",
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        # A PrimaryKeyRelatedField renders the raw pk here (the response is not JSON
        # round-tripped yet, unlike the cached detail read).
        self.assertEqual(response.data["actions"][0]["template"], self.email_template.id)
        # The step derived from it carries the template too, or the graph would run a
        # different message than the list.
        self.assertEqual(response.data["steps"][0]["template"], self.email_template.id)

    def test_a_template_of_the_wrong_channel_type_is_rejected(self):
        sms_template = factories.make_template(
            self.tenant, name="SmsTemplate", channel_type="sms",
        )
        response = self.client.post('/api/v1/automations/', self.payload(actions=[
            {"action_type": "email", "channel": str(self.email_channel.id),
             "template": str(sms_template.id), "order": 1, "on_failure": "stop",
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- graph payloads --------------------------------------------------------

    def test_a_branching_graph_can_be_created_and_read_back(self):
        response = self.client.post('/api/v1/automations/', self.payload(
            steps=self.branch_steps(), edges=self.branch_edges(),
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        automation = Automation.objects.get(name="Graph")
        self.assertEqual(automation.steps.count(), 4)
        self.assertEqual(automation.edges.count(), 5)

        self.assertEqual([s["key"] for s in response.data["steps"]],
                         ["check", "wa", "sms", "pause"])
        self.assertEqual(response.data["steps"][0]["position"], {"x": 40, "y": 10})
        # Nested channel payload, not a bare uuid: the canvas needs the name too.
        self.assertEqual(response.data["steps"][1]["channel"]["id"],
                         str(self.whatsapp_channel.id))
        self.assertEqual(
            sorted((e["from_step"] or "", e["to_step"], e["branch_key"])
                   for e in response.data["edges"]),
            sorted([("", "check", ""), ("check", "wa", "true"),
                    ("check", "sms", "false"), ("wa", "pause", ""),
                    ("sms", "pause", "")]),
        )

        # The legacy executor reads AutomationAction, so the action steps must have
        # become rows - in creation order, since a list cannot express the fan-out.
        self.assertEqual(
            list(automation.actions.values_list("action_type", "order")),
            [("whatsapp", 1), ("sms", 2)],
        )

        detail = self.client.get(f'/api/v1/automations/{automation.id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        self.assertEqual(len(detail.data["edges"]), 5)

    def test_a_graph_without_edges_becomes_a_linear_chain(self):
        response = self.client.post('/api/v1/automations/', self.payload(steps=[
            {"key": "first", "step_type": "action", "action_type": "webhook",
             "config": {"url": "https://example.com/1", "method": "POST", "headers": {}}},
            {"key": "second", "step_type": "action", "action_type": "webhook",
             "config": {"url": "https://example.com/2", "method": "POST", "headers": {}}},
        ]), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.assertEqual(
            [(e["from_step"], e["to_step"]) for e in response.data["edges"]],
            [(None, "first"), ("first", "second")],
        )
        automation = Automation.objects.get(name="Graph")
        self.assertEqual(automation.actions.count(), 2)

    def test_an_automation_needs_actions_or_steps(self):
        response = self.client.post('/api/v1/automations/', self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("actions", self.fields_of(response))

    def test_edges_without_steps_are_rejected(self):
        response = self.client.post('/api/v1/automations/', self.payload(
            actions=[{"action_type": "webhook", "order": 1, "on_failure": "stop",
                      "config": dict(WEBHOOK_CONFIG)}],
            edges=[{"from_step": None, "to_step": "1"}],
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("edges", self.fields_of(response))

    def test_a_cyclic_graph_is_rejected(self):
        response = self.client.post('/api/v1/automations/', self.payload(
            steps=[webhook_step("a"), webhook_step("b")],
            edges=[
                {"from_step": None, "to_step": "a"},
                {"from_step": "a", "to_step": "b"},
                {"from_step": "b", "to_step": "a"},
            ],
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("edges", self.fields_of(response))

    def test_duplicate_step_keys_are_rejected(self):
        response = self.client.post('/api/v1/automations/', self.payload(
            steps=[webhook_step("a"), webhook_step("a")],
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("steps", self.fields_of(response))

    def test_a_step_with_a_mismatched_channel_is_rejected(self):
        response = self.client.post('/api/v1/automations/', self.payload(steps=[
            {"key": "mail", "step_type": "action", "action_type": "email",
             "channel": str(self.sms_channel.id),
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- tenant boundary -------------------------------------------------------

    def test_a_channel_from_another_tenant_is_rejected(self):
        # A Channel pk is globally unique and tenant-blind, so the FK alone would let
        # this automation send with someone else's credentials.
        other_tenant = factories.make_tenant(name="Globex")
        foreign_channel = factories.make_channel(other_tenant, name="Theirs")

        response = self.client.post('/api/v1/automations/', self.payload(actions=[
            {"action_type": "email", "channel": str(foreign_channel.id),
             "order": 1, "on_failure": "stop",
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("actions", self.fields_of(response))

    def test_a_step_channel_from_another_tenant_is_rejected(self):
        other_tenant = factories.make_tenant(name="Globex")
        foreign_channel = factories.make_channel(other_tenant, name="Theirs")

        response = self.client.post('/api/v1/automations/', self.payload(steps=[
            {"key": "mail", "step_type": "action", "action_type": "email",
             "channel": str(foreign_channel.id),
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("steps", self.fields_of(response))

    def test_a_template_from_another_tenant_is_rejected(self):
        other_tenant = factories.make_tenant(name="Globex")
        foreign_template = factories.make_template(other_tenant, name="Theirs")

        response = self.client.post('/api/v1/automations/', self.payload(actions=[
            {"action_type": "email", "channel": str(self.email_channel.id),
             "template": str(foreign_template.id), "order": 1, "on_failure": "stop",
             "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"}},
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("actions", self.fields_of(response))

    def test_a_chained_trigger_naming_another_tenants_automation_is_rejected(self):
        # source_automation_id lives in a JSON config, so nothing but this check
        # stands between a tenant and a fan-out from someone else's run.
        other_tenant = factories.make_tenant(name="Globex")
        outsider = factories.make_automation(other_tenant, name="Outsider")

        response = self.client.post('/api/v1/automations/', self.payload(
            triggers=[{"trigger_type": "automation_completed", "initial_data": {},
                       "config": {"source_automation_id": str(outsider.id)}}],
            actions=[{"action_type": "webhook", "order": 1, "on_failure": "stop",
                      "config": dict(WEBHOOK_CONFIG)}],
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("triggers", self.fields_of(response))

    # -- switching representations --------------------------------------------

    def test_switching_from_actions_to_a_graph_replaces_the_flow_and_the_cache(self):
        automation = factories.make_automation(
            self.tenant, self.branch, allowed_roles=["super_admin", "owner"], name="Swap",
        )
        factories.make_action(automation, order=1)
        sync_steps_from_actions(automation)

        url = f'/api/v1/automations/{automation.id}/'
        cache_key = f"automations/{automation.id}"

        self.client.get(url)
        self.assertEqual(self.redis.exists(cache_key), 1)

        response = self.client.put(url, self.payload(
            name="Swap", steps=self.branch_steps(), edges=self.branch_edges(),
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        # The parent entry is dropped by the Automation write itself; the stale graph
        # must not survive it.
        self.assertEqual(self.redis.exists(cache_key), 0)

        automation.refresh_from_db()
        self.assertEqual([s.key for s in automation.steps.all()],
                         ["check", "wa", "sms", "pause"])
        self.assertEqual(automation.edges.count(), 5)
        self.assertEqual(
            list(automation.actions.values_list("action_type", "order")),
            [("whatsapp", 1), ("sms", 2)],
        )

        # ... and back again: a legacy payload rebuilds the chain from scratch.
        response = self.client.put(url, self.payload(name="Swap", actions=[
            {"action_type": "webhook", "order": 1, "on_failure": "stop",
             "config": dict(WEBHOOK_CONFIG)},
        ]), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        automation.refresh_from_db()
        self.assertEqual([s.key for s in automation.steps.all()], ["1"])
        self.assertEqual(
            [(e.from_step.key if e.from_step else None, e.to_step.key)
             for e in automation.edges.all()],
            [(None, "1")],
        )
        self.assertEqual(automation.actions.count(), 1)

        detail = self.client.get(url)
        self.assertEqual([s["key"] for s in detail.data["steps"]], ["1"])

    def test_updating_with_both_representations_keeps_the_graph_authoritative(self):
        automation = factories.make_automation(
            self.tenant, self.branch, allowed_roles=["super_admin", "owner"], name="Both",
        )
        url = f'/api/v1/automations/{automation.id}/'

        response = self.client.put(url, self.payload(
            name="Both",
            steps=[webhook_step("only_step")],
            actions=[{"action_type": "sms", "channel": str(self.sms_channel.id),
                      "order": 5, "on_failure": "continue",
                      "config": {"to": "+905551112233", "message": "hi"}}],
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        automation.refresh_from_db()
        self.assertEqual([s.key for s in automation.steps.all()], ["only_step"])
        # The client's own action rows are kept as sent - no derivation on top of an
        # explicit list, or it would overwrite what the client asked for.
        self.assertEqual(
            list(automation.actions.values_list("action_type", "order", "on_failure")),
            [("sms", 5, "continue")],
        )
