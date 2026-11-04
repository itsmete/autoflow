"""
The rule layer: path resolution, the operator vocabulary, grouping, short circuit
and the decision trace.

Phase 1.A. The evaluator was 67 lines and six operators that all compared strings;
this file covers what replaced it. The regression baseline for the OLD behaviour
stays in test_automations_engine.py - everything there still has to pass, because
none of this was allowed to change what a pure-AND or pure-OR chain means.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.automations.engine import operators as ops
from apps.automations.engine.decision import (
    SKIP_GROUP_ALREADY_TRUE,
    SKIP_SHORT_CIRCUIT,
    ConditionOutcome,
    Decision,
)
from apps.automations.engine.evaluator import ConditionEvaluator
from apps.automations.engine.exceptions import EvaluationError
from apps.automations.engine.executor import AutomationExecutor
from apps.automations.engine.paths import MISSING, has_wildcard, resolve_path, split_path
from apps.automations.models import AutomationLog, AutomationStatus
from apps.automations.engine.orchestrator import AutomationOrchestrator
from tests import factories


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class PathResolutionTests(SimpleTestCase):

    def test_a_top_level_key(self):
        self.assertEqual(resolve_path({"status": "paid"}, "status"), "paid")

    def test_a_nested_key(self):
        data = {"customer": {"address": {"city": "izmir"}}}
        self.assertEqual(resolve_path(data, "customer.address.city"), "izmir")

    def test_bracket_and_dot_index_are_the_same_path(self):
        data = {"items": [{"sku": "a"}, {"sku": "b"}]}
        self.assertEqual(resolve_path(data, "items[1].sku"), "b")
        self.assertEqual(resolve_path(data, "items.1.sku"), "b")

    def test_split_path_normalises_brackets(self):
        self.assertEqual(split_path("items[0].price"), ["items", "0", "price"])

    def test_a_missing_key_is_MISSING_not_None(self):
        # None is a legitimate JSON value; conflating the two makes `exists` and
        # `is_empty` undefinable.
        self.assertIs(resolve_path({"a": 1}, "b"), MISSING)

    def test_an_explicit_null_is_None_not_MISSING(self):
        self.assertIsNone(resolve_path({"discount": None}, "discount"))

    def test_walking_past_a_scalar_is_MISSING(self):
        self.assertIs(resolve_path({"a": 5}, "a.b.c"), MISSING)

    def test_an_out_of_range_index_is_MISSING(self):
        self.assertIs(resolve_path({"items": [1]}, "items[7]"), MISSING)

    def test_a_wildcard_collects_across_a_list(self):
        data = {"items": [{"stock": 3}, {"stock": 0}, {"stock": 9}]}
        self.assertEqual(resolve_path(data, "items[*].stock"), [3, 0, 9])

    def test_a_wildcard_skips_elements_that_lack_the_key(self):
        # "any item is cheap" should survive one item having no price.
        data = {"items": [{"price": 10}, {"sku": "no-price"}, {"price": 4}]}
        self.assertEqual(resolve_path(data, "items[*].price"), [10, 4])

    def test_a_bare_wildcard_returns_the_list_itself(self):
        self.assertEqual(resolve_path({"tags": ["a", "b"]}, "tags[*]"), ["a", "b"])

    def test_a_wildcard_over_a_non_list_is_MISSING(self):
        self.assertIs(resolve_path({"items": {"a": 1}}, "items[*].x"), MISSING)

    def test_has_wildcard(self):
        self.assertTrue(has_wildcard("items[*].price"))
        self.assertFalse(has_wildcard("items.0.price"))


class OperatorTests(SimpleTestCase):

    def setUp(self):
        self.ctx = ops.OperatorContext(now=NOW)

    def _apply(self, name, actual, expected):
        return ops.get_operator(name)(actual, expected, self.ctx)

    def test_equals_bridges_the_string_number_divide(self):
        # One side comes from a JSON payload, the other from stored config; 100 and
        # "100" mean the same thing to whoever wrote the condition.
        self.assertTrue(self._apply(ops.EQUALS, "100", 100))
        self.assertTrue(self._apply(ops.EQUALS, 100, "100"))
        self.assertFalse(self._apply(ops.EQUALS, "abc", 100))

    def test_ordering_handles_floats(self):
        # int() on both sides made 10.5 a crash.
        self.assertTrue(self._apply(ops.GREATER, 10.5, 10))
        self.assertTrue(self._apply(ops.LESS_OR_EQUAL, 10.5, 10.5))
        self.assertTrue(self._apply(ops.GREATER_OR_EQUAL, "10.5", 10))

    def test_ordering_handles_dates(self):
        self.assertTrue(self._apply(ops.GREATER, "2026-08-04", "2026-08-01"))
        self.assertTrue(self._apply(ops.LESS, "2026-08-01T09:00:00Z", "2026-08-01T10:00:00Z"))

    def test_ordering_refuses_rather_than_comparing_strings(self):
        # '9' > '10' is true for strings and is never what anyone meant.
        with self.assertRaises(ValueError):
            self._apply(ops.GREATER, "apple", "banana")

    def test_a_boolean_is_not_a_number(self):
        with self.assertRaises(ValueError):
            ops.as_number(True)

    def test_contains_works_on_strings_and_on_lists(self):
        self.assertTrue(self._apply(ops.CONTAINS, "this is urgent", "urgent"))
        self.assertTrue(self._apply(ops.CONTAINS, ["a", "b"], "b"))
        self.assertTrue(self._apply(ops.NOT_CONTAINS, ["a", "b"], "z"))

    def test_starts_and_ends_with(self):
        self.assertTrue(self._apply(ops.STARTS_WITH, "TR-1234", "TR-"))
        self.assertTrue(self._apply(ops.ENDS_WITH, "invoice.pdf", ".pdf"))

    def test_regex(self):
        self.assertTrue(self._apply(ops.MATCHES, "TR-1234", r"^TR-\d+$"))
        self.assertFalse(self._apply(ops.MATCHES, "XX-1234", r"^TR-\d+$"))

    def test_an_invalid_regex_is_a_refusal_not_a_crash(self):
        with self.assertRaises(ValueError):
            self._apply(ops.MATCHES, "x", "([")

    def test_an_overlong_pattern_is_refused(self):
        with self.assertRaises(ValueError):
            self._apply(ops.MATCHES, "x", "a" * (ops.MAX_REGEX_LENGTH + 1))

    def test_in_accepts_a_real_list_and_the_legacy_comma_string(self):
        self.assertTrue(self._apply(ops.IN, "izmir", ["ankara", "izmir"]))
        self.assertTrue(self._apply(ops.IN, "izmir", "ankara,izmir"))
        self.assertTrue(self._apply(ops.NOT_IN, "bursa", ["ankara", "izmir"]))

    def test_between_is_inclusive(self):
        self.assertTrue(self._apply(ops.BETWEEN, 5, [1, 10]))
        self.assertTrue(self._apply(ops.BETWEEN, 1, [1, 10]))
        self.assertFalse(self._apply(ops.BETWEEN, 11, [1, 10]))

    def test_between_needs_two_bounds(self):
        with self.assertRaises(ValueError):
            self._apply(ops.BETWEEN, 5, [1, 2, 3])

    def test_presence_operators(self):
        self.assertTrue(self._apply(ops.EXISTS, "anything", None))
        self.assertTrue(self._apply(ops.NOT_EXISTS, MISSING, None))
        self.assertTrue(self._apply(ops.IS_EMPTY, "", None))
        self.assertTrue(self._apply(ops.IS_EMPTY, [], None))
        self.assertTrue(self._apply(ops.IS_EMPTY, None, None))
        self.assertTrue(self._apply(ops.IS_NOT_EMPTY, "x", None))

    def test_within_last_and_older_than(self):
        recent = (NOW - timedelta(days=2)).isoformat()
        ancient = (NOW - timedelta(days=90)).isoformat()

        self.assertTrue(self._apply(ops.WITHIN_LAST, recent, "7d"))
        self.assertFalse(self._apply(ops.WITHIN_LAST, ancient, "7d"))
        self.assertTrue(self._apply(ops.OLDER_THAN, ancient, "60d"))

    def test_a_naive_timestamp_is_read_as_utc_rather_than_raising(self):
        # Comparing naive with aware is a TypeError in Python, and a TypeError this
        # deep surfaces as an unexplained failure.
        naive = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        self.assertTrue(self._apply(ops.WITHIN_LAST, naive, "2h"))

    def test_a_malformed_duration_is_refused(self):
        with self.assertRaises(ValueError):
            self._apply(ops.WITHIN_LAST, NOW.isoformat(), "soon")


class GroupingTests(TestCase):
    """AND binds tighter than OR. This CHANGES the old behaviour, which folded left
    to right, and is the one intentional semantic break in Phase 1.A."""

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator(now=NOW)

    def _cond(self, order, **kwargs):
        return factories.make_condition(self.automation, order=order, **kwargs)

    def test_and_binds_tighter_than_or(self):
        # A AND B OR C AND D  ==  (A and B) or (C and D)
        a = self._cond(0, field="a", operator="eq", value=1)
        b = self._cond(1, field="b", operator="eq", value=1, logical_operator="and")
        c = self._cond(2, field="c", operator="eq", value=1, logical_operator="or")
        d = self._cond(3, field="d", operator="eq", value=1, logical_operator="and")

        conditions = [a, b, c, d]

        # second group true, first false
        self.assertTrue(
            self.evaluator.evaluate_conditions(conditions, {"a": 0, "b": 0, "c": 1, "d": 1})
        )
        # both groups half-true -> false, where a left fold would have said true
        self.assertFalse(
            self.evaluator.evaluate_conditions(conditions, {"a": 1, "b": 0, "c": 1, "d": 0})
        )

    def test_groups_are_split_on_or(self):
        a = self._cond(0, field="a", operator="eq", value=1)
        b = self._cond(1, field="b", operator="eq", value=1, logical_operator="and")
        c = self._cond(2, field="c", operator="eq", value=1, logical_operator="or")

        groups = self.evaluator.group_conditions([a, b, c])

        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_the_first_logical_operator_opens_a_group_whatever_it_says(self):
        only = self._cond(0, field="a", operator="eq", value=1, logical_operator="or")
        self.assertEqual(len(self.evaluator.group_conditions([only])), 1)

    def test_an_unknown_logical_operator_is_an_evaluation_error(self):
        first = self._cond(0, field="a", operator="eq", value=1)
        second = self._cond(1, field="b", operator="eq", value=1)
        second.logical_operator = "xor"

        with self.assertRaises(EvaluationError):
            self.evaluator.group_conditions([first, second])

    def test_conditions_come_back_in_the_authored_order(self):
        # Grouping depends on order, so an unordered queryset made the MEANING of a
        # condition set depend on what the database felt like returning.
        self._cond(2, field="c", operator="eq", value=1)
        self._cond(0, field="a", operator="eq", value=1)
        self._cond(1, field="b", operator="eq", value=1)

        fields = [c.field for c in self.automation.conditions.all()]
        self.assertEqual(fields, ["a", "b", "c"])


class ShortCircuitTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator(now=NOW)

    def test_a_false_condition_stops_its_group(self):
        first = factories.make_condition(self.automation, order=0, field="a", operator="eq", value=1)
        second = factories.make_condition(self.automation, order=1, field="b", operator="eq", value=1)

        decision = self.evaluator.evaluate_conditions([first, second], {"a": 0, "b": 1})

        self.assertFalse(decision)
        self.assertEqual(decision.groups[0][1].skipped, SKIP_SHORT_CIRCUIT)

    def test_a_true_group_stops_the_remaining_groups(self):
        first = factories.make_condition(self.automation, order=0, field="a", operator="eq", value=1)
        second = factories.make_condition(
            self.automation, order=1, field="b", operator="eq", value=1, logical_operator="or"
        )

        decision = self.evaluator.evaluate_conditions([first, second], {"a": 1, "b": 1})

        self.assertTrue(decision)
        self.assertEqual(decision.groups[1][0].skipped, SKIP_GROUP_ALREADY_TRUE)

    def test_a_skipped_condition_is_never_evaluated(self):
        # The point of the exercise: once a condition can call a model, evaluating
        # one that cannot change the answer is money.
        first = factories.make_condition(self.automation, order=0, field="a", operator="eq", value=1)
        # would raise if it were evaluated - the field is absent and the policy is fail
        second = factories.make_condition(self.automation, order=1, field="absent", operator="eq", value=1)

        decision = self.evaluator.evaluate_conditions([first, second], {"a": 0})

        self.assertFalse(decision)


class MissingFieldPolicyTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator(now=NOW)

    def _cond(self, on_missing):
        return factories.make_condition(
            self.automation, field="absent", operator="eq", value=1, on_missing=on_missing
        )

    def test_fail_is_the_default_and_raises(self):
        with self.assertRaises(EvaluationError):
            self.evaluator.evaluate_conditions([self._cond("fail")], {})

    def test_false_treats_the_condition_as_unmet(self):
        self.assertFalse(self.evaluator.evaluate_conditions([self._cond("false")], {}))

    def test_true_treats_the_condition_as_met(self):
        self.assertTrue(self.evaluator.evaluate_conditions([self._cond("true")], {}))

    def test_presence_operators_ignore_the_policy(self):
        # "does this exist" answered with "it does not exist, so I refuse to answer"
        # would be a bad joke.
        condition = factories.make_condition(
            self.automation, field="absent", operator="not_exists", value=None, on_missing="fail"
        )
        self.assertTrue(self.evaluator.evaluate_conditions([condition], {}))


class QuantifierTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator(now=NOW)
        self.payload = {"items": [{"stock": 5}, {"stock": 0}, {"stock": 9}]}

    def _cond(self, quantifier, operator="lt", value=1, field="items[*].stock"):
        return factories.make_condition(
            self.automation, field=field, operator=operator, value=value, quantifier=quantifier
        )

    def test_any(self):
        self.assertTrue(self.evaluator.evaluate_conditions([self._cond("any")], self.payload))

    def test_all(self):
        self.assertFalse(self.evaluator.evaluate_conditions([self._cond("all")], self.payload))
        cheap = self._cond("all", operator="lt", value=100)
        self.assertTrue(self.evaluator.evaluate_conditions([cheap], self.payload))

    def test_none(self):
        self.assertFalse(self.evaluator.evaluate_conditions([self._cond("none")], self.payload))
        expensive = self._cond("none", operator="gt", value=1000)
        self.assertTrue(self.evaluator.evaluate_conditions([expensive], self.payload))

    def test_all_is_vacuously_true_on_an_empty_collection(self):
        condition = self._cond("all")
        self.assertTrue(self.evaluator.evaluate_conditions([condition], {"items": []}))

    def test_any_is_false_on_an_empty_collection(self):
        condition = self._cond("any")
        self.assertFalse(self.evaluator.evaluate_conditions([condition], {"items": []}))

    def test_a_quantifier_over_a_plain_path_reads_it_as_one_element(self):
        condition = self._cond("any", operator="eq", value="paid", field="status")
        self.assertTrue(self.evaluator.evaluate_conditions([condition], {"status": "paid"}))


class DecisionTraceTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.evaluator = ConditionEvaluator(now=NOW)

    def test_the_trace_records_the_actual_value(self):
        # "false" answers nothing; "320 is not more than 500" answers the question,
        # both for a person reading a log and for a model repairing its own rule.
        condition = factories.make_condition(
            self.automation, field="order.total", operator="gt", value=500
        )

        decision = self.evaluator.evaluate_conditions([condition], {"order": {"total": 320}})

        outcome = decision.groups[0][0]
        self.assertEqual(outcome.actual, 320)
        self.assertFalse(outcome.result)

    def test_a_decision_is_truthy_exactly_when_it_passed(self):
        # So `if not decision:` at the call site keeps reading the way it did.
        self.assertTrue(Decision(result=True))
        self.assertFalse(Decision(result=False))

    def test_as_dict_is_json_safe_including_missing(self):
        condition = factories.make_condition(
            self.automation, field="absent", operator="eq", value=1, on_missing="false"
        )

        payload = self.evaluator.evaluate_conditions([condition], {}).as_dict()

        self.assertEqual(payload["groups"][0][0]["actual"], "<missing>")
        self.assertIs(payload["result"], False)

    def test_explain_names_the_field_and_the_actual_value(self):
        condition = factories.make_condition(
            self.automation, field="amount", operator="gt", value=500
        )

        text = self.evaluator.evaluate_conditions([condition], {"amount": 320}).explain()

        self.assertIn("amount", text)
        self.assertIn("320", text)

    def test_a_skipped_condition_says_so_instead_of_reporting_a_value(self):
        first = factories.make_condition(self.automation, order=0, field="a", operator="eq", value=1)
        second = factories.make_condition(self.automation, order=1, field="b", operator="eq", value=1)

        payload = self.evaluator.evaluate_conditions([first, second], {"a": 0, "b": 1}).as_dict()

        self.assertEqual(payload["groups"][0][1]["skipped"], SKIP_SHORT_CIRCUIT)
        self.assertNotIn("actual", payload["groups"][0][1])

    def test_an_evaluation_error_carries_the_partial_trace(self):
        # The run that died mid-evaluation is the one whose reasoning matters most.
        first = factories.make_condition(self.automation, order=0, field="a", operator="eq", value=1)
        second = factories.make_condition(self.automation, order=1, field="amount", operator="gt", value=1)

        with self.assertRaises(EvaluationError) as caught:
            self.evaluator.evaluate_conditions([first, second], {"a": 1, "amount": "abc"})

        self.assertIsNotNone(caught.exception.decision)
        self.assertEqual(len(caught.exception.decision.groups[0]), 2)


class OrchestratorDecisionTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.orchestrator = AutomationOrchestrator()

    def test_a_skipped_run_persists_why(self):
        factories.make_condition(self.automation, field="amount", operator="gt", value=100)
        factories.make_action(self.automation, order=1)

        self.orchestrator.run(str(self.automation.id), {"amount": 5})

        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.SKIPPED)
        self.assertEqual(log.decision["groups"][0][0]["actual"], 5)

    def test_a_successful_run_persists_the_trace_too(self):
        # A trace on failed runs only would answer "why not" but not "what exactly
        # was compared when it did go out" - and the same record is what a future
        # AI repair loop reads back.
        factories.make_condition(self.automation, field="amount", operator="gt", value=100)
        factories.make_action(self.automation, order=1)

        with patch.object(AutomationExecutor, "_execute_single", return_value=True):
            self.assertTrue(
                self.orchestrator.run(str(self.automation.id), {"amount": 500})
            )

        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.SUCCESS)
        self.assertTrue(log.decision["result"])
        self.assertEqual(log.decision["groups"][0][0]["actual"], 500)

    def test_a_failed_evaluation_persists_the_partial_trace(self):
        factories.make_condition(self.automation, field="absent", operator="eq", value=1)
        factories.make_action(self.automation, order=1)

        self.orchestrator.run(str(self.automation.id), {"other": 1})

        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.FAILURE)
        self.assertIn("error", log.decision["groups"][0][0])
