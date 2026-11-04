"""
Faz 1.B: the step graph (AutomationStep + AutomationEdge).

AutomationAction is a linear list, and every way of putting branching on top of an
`order` integer ends up re-implementing a graph badly (ROADMAP §5.1). These tests
cover the graph itself and the two bridges that keep the old list authoritative:

- `sync_steps_from_actions()` - the runtime bridge the actions API will use.
- `migrate_actions_to_steps()` - the data migration that copies existing rows.

Both are copies, not moves: AutomationAction stays readable for a release, so a test
that only checked the new tables would not notice the old ones being clobbered.
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
from apps.automations.models import (
    AutomationAction,
    AutomationEdge,
    AutomationStep,
    StepTypes,
    sync_steps_from_actions,
)
from tests import factories


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
