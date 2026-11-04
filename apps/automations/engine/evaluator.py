"""
Condition evaluation.

Called by the orchestrator with a collection of conditions and the trigger payload.

Three things changed from the first version and all three are behavioural, so they
are spelled out here rather than left to be discovered:

  1. PRECEDENCE. The old loop folded left to right, so `A AND B OR C AND D` meant
     `(((A and B) or C) and D)`. Now AND binds tighter than OR - the reading every
     other language uses - giving `(A and B) or (C and D)`. Conditions are grouped
     on their logical_operator: 'or' opens a new group, 'and' extends the current
     one. Pure-AND and pure-OR chains are unaffected.

  2. SHORT CIRCUIT. A group stops at its first false condition, and the whole set
     stops once a group comes out true. This was pure waste before; once a condition
     can call a model it is money.

  3. A DECISION, NOT A BOOL. evaluate_conditions returns a Decision that records
     every condition, its actual value and its result. Decision is truthy when it
     passed, so existing `if not ...` call sites are unaffected.

This module imports nothing from Django. `condition` is read by attribute, so it can
be an ORM row or any object with the same shape.
"""
from .decision import ConditionOutcome, Decision, SKIP_GROUP_ALREADY_TRUE, SKIP_SHORT_CIRCUIT
from .exceptions import EvaluationError
from .operators import (
        AND,
        ON_MISSING_FAIL,
        ON_MISSING_FALSE,
        ON_MISSING_TRUE,
        OR,
        PRESENCE_OPERATORS,
        QUANTIFIER_ALL,
        QUANTIFIER_ANY,
        QUANTIFIER_NO,
        OperatorContext,
        get_operator,
)
from .paths import MISSING, resolve_path


class ConditionEvaluator:

        def __init__(self,now = None):
                # Injected rather than read from the clock inside each operator, so a
                # temporal condition is testable and a replayed run can be judged
                # against the time it originally ran at.
                self.ctx = OperatorContext(now)


        def group_conditions(self,conditions):
                """
                Split an ordered list into OR-ed groups of AND-ed conditions.

                The first condition's logical_operator is meaningless - there is nothing
                to its left to join it to - and is ignored.
                """
                groups = []

                for index,condition in enumerate(conditions):
                        operator = getattr(condition,'logical_operator',AND)

                        if index == 0 or operator == OR:
                                groups.append([])
                        elif operator != AND:
                                raise EvaluationError(
                                        f"Invalid logical operator for condition {condition}"
                                )

                        groups[-1].append(condition)

                return groups


        def _apply(self,operator_name,operator_func,actual,expected):
                try:
                        return bool(operator_func(actual,expected,self.ctx))
                except EvaluationError:
                        raise
                except (ValueError,TypeError) as error:
                        raise EvaluationError(
                                f"Cannot apply '{operator_name}' to {actual!r} and {expected!r}: {error}"
                        )


        def _quantified(self,operator_name,operator_func,values,expected,quantifier):
                # A wildcard path yields a list; a plain path under a quantifier is read
                # as a collection of one, which keeps 'any' meaningful either way.
                if not isinstance(values,(list,tuple)):
                        values = [values]

                results = [
                        self._apply(operator_name,operator_func,item,expected)
                        for item in values
                ]

                if quantifier == QUANTIFIER_ANY:
                        return any(results)

                if quantifier == QUANTIFIER_ALL:
                        # Vacuously true on an empty collection, as in logic and in every
                        # other all() you have used.
                        return all(results)

                if quantifier == QUANTIFIER_NO:
                        return not any(results)

                raise EvaluationError(f"Unknown quantifier '{quantifier}'")


        def evaluate_single(self,condition,data):
                """
                One condition against the payload, as a ConditionOutcome.

                Failures come back ON the outcome rather than as an exception, so that
                evaluate_conditions can put the partial trace into the error it raises.
                A run that dies mid-evaluation is exactly the run whose reasoning you
                most want to read.
                """
                field_path = condition.field
                operator_name = condition.operator
                expected = condition.value
                quantifier = getattr(condition,'quantifier','') or ''

                outcome = ConditionOutcome(
                        field = field_path,
                        operator = operator_name,
                        expected = expected,
                )

                operator_func = get_operator(operator_name)
                if not operator_func:
                        outcome.error = "Evaluator function couldn't be called"
                        return outcome

                actual = resolve_path(data,field_path)
                outcome.actual = actual

                if actual is MISSING and operator_name not in PRESENCE_OPERATORS:
                        policy = getattr(condition,'on_missing',ON_MISSING_FAIL) or ON_MISSING_FAIL

                        if policy == ON_MISSING_FALSE:
                                outcome.result = False
                                return outcome

                        if policy == ON_MISSING_TRUE:
                                outcome.result = True
                                return outcome

                        outcome.error = f"Field value ({field_path}) is not found in data "
                        return outcome

                try:
                        if quantifier:
                                outcome.result = self._quantified(
                                        operator_name,operator_func,actual,expected,quantifier
                                )
                        else:
                                outcome.result = self._apply(operator_name,operator_func,actual,expected)
                except EvaluationError as error:
                        outcome.error = str(error)

                return outcome


        def evaluate_conditions(self,conditions,data) -> Decision:
                conditions = list(conditions or [])

                if not conditions:
                        return Decision(result = True)

                groups = self.group_conditions(conditions)

                overall = False
                trace = []

                for group in groups:
                        group_trace = []
                        group_result = True

                        for condition in group:
                                if overall:
                                        group_trace.append(self._skipped(condition,SKIP_GROUP_ALREADY_TRUE))
                                        continue

                                if not group_result:
                                        group_trace.append(self._skipped(condition,SKIP_SHORT_CIRCUIT))
                                        continue

                                outcome = self.evaluate_single(condition,data)
                                group_trace.append(outcome)

                                if outcome.error:
                                        trace.append(group_trace)
                                        raise EvaluationError(
                                                outcome.error,
                                                decision = Decision(result = False,groups = trace),
                                        )

                                group_result = outcome.result

                        trace.append(group_trace)

                        if not overall and group_result:
                                overall = True

                return Decision(result = overall,groups = trace)


        def _skipped(self,condition,reason):
                return ConditionOutcome(
                        field = condition.field,
                        operator = condition.operator,
                        expected = condition.value,
                        skipped = reason,
                )
