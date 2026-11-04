"""
The comparison vocabulary of the rule engine.

There used to be six operators, all of them string-shaped: gt/lt cast both sides
with int(), so 10.5 was a crash and a date was impossible. The condition VALUE was a
CharField too, which meant a list could only ever be a comma-separated string.

Contract for an operator: it takes (actual, expected, ctx) and returns a bool. It
signals "these two cannot be compared" by raising ValueError - the evaluator turns
that into an EvaluationError. Returning False for an impossible comparison would
silently answer a question that was never asked.

This module is the single source of the operator NAMES; models.ConditionOperators
builds its choices from them. The dependency points that way round on purpose: the
Django layer may know about the engine, the engine may not know about Django.
"""
from datetime import datetime, timedelta, timezone

import re


# --- names -------------------------------------------------------------------

EQUALS = 'eq'
NOT_EQUALS = 'neq'
GREATER = 'gt'
GREATER_OR_EQUAL = 'gte'
LESS = 'lt'
LESS_OR_EQUAL = 'lte'

CONTAINS = 'contains'
NOT_CONTAINS = 'not_contains'
STARTS_WITH = 'starts_with'
ENDS_WITH = 'ends_with'
MATCHES = 'regex'

IN = 'in'
NOT_IN = 'not_in'
BETWEEN = 'between'

EXISTS = 'exists'
NOT_EXISTS = 'not_exists'
IS_EMPTY = 'is_empty'
IS_NOT_EMPTY = 'is_not_empty'

WITHIN_LAST = 'within_last'
OLDER_THAN = 'older_than'


# Operators that are ABOUT presence. They must still be evaluated when the field is
# absent, so the missing-field policy does not apply to them - asking "does this
# exist" and being told "it does not exist, so I refuse to answer" is a bad joke.
PRESENCE_OPERATORS = frozenset({EXISTS,NOT_EXISTS,IS_EMPTY,IS_NOT_EMPTY})


class OperatorContext:
        """
        Everything an operator needs besides the two values.

        `now` is injected rather than read from the clock inside the operator so that
        temporal conditions are testable and so that a replayed run can be evaluated
        against the time it originally ran at.
        """

        def __init__(self,now = None):
                self.now = now or datetime.now(timezone.utc)


# --- coercion ----------------------------------------------------------------

_DURATION = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$',re.IGNORECASE)

_DURATION_SECONDS = {
        's' : 1,
        'm' : 60,
        'h' : 3600,
        'd' : 86400,
        'w' : 604800,
}

# Bounds a tenant-authored pattern. Python's re has no timeout, so the only cheap
# defence against a catastrophic backtrack is to keep both sides small.
MAX_REGEX_LENGTH = 512
MAX_SUBJECT_LENGTH = 10000


def as_number(value):
        """int/float if the value can honestly be read as one, else ValueError."""
        if isinstance(value,bool):
                # bool is an int subclass in Python, and treating True as 1 in a
                # numeric comparison is almost never what the author meant.
                raise ValueError("a boolean is not a number")

        if isinstance(value,(int,float)):
                return value

        if isinstance(value,str):
                text = value.strip()
                try:
                        return int(text)
                except ValueError:
                        return float(text)

        raise ValueError(f"cannot read {value!r} as a number")


def as_datetime(value):
        """A timezone-aware datetime, or ValueError."""
        if isinstance(value,datetime):
                parsed = value
        elif isinstance(value,str):
                text = value.strip()
                # fromisoformat learned 'Z' in 3.11; normalise anyway so the parser is
                # not the thing that decides which payloads are acceptable.
                if text.endswith(('Z','z')):
                        text = text[:-1] + '+00:00'
                parsed = datetime.fromisoformat(text)
        else:
                raise ValueError(f"cannot read {value!r} as a datetime")

        # A naive datetime is read as UTC. Comparing naive with aware raises TypeError
        # in Python, and a TypeError at this depth surfaces as an unexplained failure.
        if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo = timezone.utc)

        return parsed


def as_duration(value):
        """'7d', '36h', '90m' -> timedelta."""
        if isinstance(value,timedelta):
                return value

        match = _DURATION.match(str(value))
        if not match:
                raise ValueError(f"cannot read {value!r} as a duration, expected e.g. '7d'")

        amount,unit = match.groups()
        return timedelta(seconds = float(amount) * _DURATION_SECONDS[unit.lower()])


def as_text(value):
        return value if isinstance(value,str) else str(value)


def as_sequence(value):
        """
        The right-hand side of `in` / `not_in`.

        A list is used as it stands. A string is split on commas, which is how the
        value was necessarily expressed while it lived in a CharField, and existing
        conditions still read that way.
        """
        if isinstance(value,(list,tuple,set)):
                return list(value)

        if isinstance(value,str):
                return [item.strip() for item in value.split(',')]

        return [value]


def _ordered(actual,expected):
        """
        Both sides as a comparable pair: numbers if they read as numbers, datetimes if
        they read as datetimes, and otherwise a refusal.

        Falling back to string comparison here would be worse than refusing: '9' > '10'
        is true for strings and is never what the author wanted.
        """
        try:
                return as_number(actual),as_number(expected)
        except (ValueError,TypeError):
                pass

        try:
                return as_datetime(actual),as_datetime(expected)
        except (ValueError,TypeError):
                pass

        raise ValueError(f"cannot order {actual!r} against {expected!r}")


def _loosely_equal(actual,expected):
        if actual == expected:
                return True

        # 100 and "100" mean the same thing to whoever wrote the condition, and the
        # two sides arrive from different places: one from a JSON payload, one from
        # the stored config.
        try:
                return as_number(actual) == as_number(expected)
        except (ValueError,TypeError):
                return False


# --- operators ---------------------------------------------------------------

def _op_equals(actual,expected,ctx):
        return _loosely_equal(actual,expected)


def _op_not_equals(actual,expected,ctx):
        return not _loosely_equal(actual,expected)


def _op_greater(actual,expected,ctx):
        left,right = _ordered(actual,expected)
        return left > right


def _op_greater_or_equal(actual,expected,ctx):
        left,right = _ordered(actual,expected)
        return left >= right


def _op_less(actual,expected,ctx):
        left,right = _ordered(actual,expected)
        return left < right


def _op_less_or_equal(actual,expected,ctx):
        left,right = _ordered(actual,expected)
        return left <= right


def _op_contains(actual,expected,ctx):
        if isinstance(actual,(list,tuple,set,dict)):
                return expected in actual
        return as_text(expected) in as_text(actual)


def _op_not_contains(actual,expected,ctx):
        return not _op_contains(actual,expected,ctx)


def _op_starts_with(actual,expected,ctx):
        return as_text(actual).startswith(as_text(expected))


def _op_ends_with(actual,expected,ctx):
        return as_text(actual).endswith(as_text(expected))


def _op_matches(actual,expected,ctx):
        pattern = as_text(expected)
        subject = as_text(actual)

        if len(pattern) > MAX_REGEX_LENGTH:
                raise ValueError("regex pattern is too long")

        try:
                compiled = re.compile(pattern)
        except re.error as error:
                raise ValueError(f"invalid regex: {error}")

        return bool(compiled.search(subject[:MAX_SUBJECT_LENGTH]))


def _op_in(actual,expected,ctx):
        return any(_loosely_equal(actual,candidate) for candidate in as_sequence(expected))


def _op_not_in(actual,expected,ctx):
        return not _op_in(actual,expected,ctx)


def _op_between(actual,expected,ctx):
        bounds = as_sequence(expected)
        if len(bounds) != 2:
                raise ValueError("between expects exactly two bounds")

        low,high = bounds
        return _op_greater_or_equal(actual,low,ctx) and _op_less_or_equal(actual,high,ctx)


def _op_exists(actual,expected,ctx):
        from .paths import MISSING
        return actual is not MISSING


def _op_not_exists(actual,expected,ctx):
        return not _op_exists(actual,expected,ctx)


def _op_is_empty(actual,expected,ctx):
        from .paths import MISSING

        if actual is MISSING or actual is None:
                return True

        if isinstance(actual,(str,list,tuple,dict,set)):
                return len(actual) == 0

        return False


def _op_is_not_empty(actual,expected,ctx):
        return not _op_is_empty(actual,expected,ctx)


def _op_within_last(actual,expected,ctx):
        moment = as_datetime(actual)
        return ctx.now - as_duration(expected) <= moment <= ctx.now


def _op_older_than(actual,expected,ctx):
        moment = as_datetime(actual)
        return moment < ctx.now - as_duration(expected)


OPERATORS = {
        EQUALS : _op_equals,
        NOT_EQUALS : _op_not_equals,
        GREATER : _op_greater,
        GREATER_OR_EQUAL : _op_greater_or_equal,
        LESS : _op_less,
        LESS_OR_EQUAL : _op_less_or_equal,

        CONTAINS : _op_contains,
        NOT_CONTAINS : _op_not_contains,
        STARTS_WITH : _op_starts_with,
        ENDS_WITH : _op_ends_with,
        MATCHES : _op_matches,

        IN : _op_in,
        NOT_IN : _op_not_in,
        BETWEEN : _op_between,

        EXISTS : _op_exists,
        NOT_EXISTS : _op_not_exists,
        IS_EMPTY : _op_is_empty,
        IS_NOT_EMPTY : _op_is_not_empty,

        WITHIN_LAST : _op_within_last,
        OLDER_THAN : _op_older_than,
}


def get_operator(name):
        return OPERATORS.get(name)


# --- the rest of the condition vocabulary ------------------------------------
#
# Same reason as the operator names: models.py builds its TextChoices from these so
# there is one spelling of 'and', not two that can drift apart.

AND = 'and'
OR = 'or'

# A quantifier turns a wildcard path into a question about a collection:
# field='items[*].stock', quantifier='any', operator='lt', value=1 reads as
# "any item has stock below 1". One extra column, and it composes with every
# operator - which is why there is no any_eq/all_eq/none_gt explosion.
QUANTIFIER_NONE_SET = ''
QUANTIFIER_ANY = 'any'
QUANTIFIER_ALL = 'all'
QUANTIFIER_NO = 'none'

# What to do when the field simply is not in the payload. Raising was the only
# behaviour, which meant one optional field could fail an entire automation.
ON_MISSING_FAIL = 'fail'
ON_MISSING_FALSE = 'false'
ON_MISSING_TRUE = 'true'
