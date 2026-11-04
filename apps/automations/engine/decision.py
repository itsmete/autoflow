"""
The record of WHY the engine decided what it decided.

evaluate_conditions() used to return a bare bool, which answers the question nobody
actually asks. The questions people ask are "why didn't this customer get the
message" and, once a model is writing the rules, "which part of my condition was
wrong" - and a bool answers neither.

A Decision is truthy exactly when it passed, so `if not decision:` at the call site
still reads the way it always did.

No Django here; see tests/test_engine_purity.py.
"""
from dataclasses import dataclass, field


SKIP_SHORT_CIRCUIT = 'short_circuit'
SKIP_GROUP_ALREADY_TRUE = 'group_already_true'


def _plain(value):
        """
        JSON-safe rendering for the log.

        MISSING is the interesting case: it has to survive into the trace as
        something distinguishable from null, because "the key was absent" and "the
        key was null" are different answers to "why did this fail".
        """
        from .paths import MISSING

        if value is MISSING:
                return '<missing>'

        if isinstance(value,(str,int,float,bool)) or value is None:
                return value

        if isinstance(value,(list,tuple)):
                return [_plain(item) for item in value]

        if isinstance(value,dict):
                return {str(k) : _plain(v) for k,v in value.items()}

        return str(value)


@dataclass
class ConditionOutcome:
        field : str
        operator : str
        expected : object
        actual : object = None
        result : bool = None
        skipped : str = ''
        error : str = ''

        def as_dict(self):
                data = {
                        'field' : self.field,
                        'operator' : self.operator,
                        'expected' : _plain(self.expected),
                        'result' : self.result,
                }

                if self.skipped:
                        data['skipped'] = self.skipped
                else:
                        data['actual'] = _plain(self.actual)

                if self.error:
                        data['error'] = self.error

                return data

        def explain(self):
                if self.skipped:
                        return f"{self.field} {self.operator} {self.expected!r} -> not evaluated ({self.skipped})"

                if self.error:
                        return f"{self.field} {self.operator} {self.expected!r} -> error: {self.error}"

                return (
                        f"{self.field} {self.operator} {self.expected!r} -> {self.result}"
                        f" (actual: {_plain(self.actual)!r})"
                )


@dataclass
class Decision:
        result : bool
        groups : list = field(default_factory = list)

        def __bool__(self):
                return bool(self.result)

        def as_dict(self):
                return {
                        'result' : self.result,
                        # Groups are OR-ed, the conditions inside one are AND-ed.
                        'groups' : [[outcome.as_dict() for outcome in group] for group in self.groups],
                }

        def explain(self):
                """One line per condition, grouped. Written to be read by a person in a
                log and by a model in a repair loop."""
                if not self.groups:
                        return "no conditions"

                lines = []

                for index,group in enumerate(self.groups):
                        if index:
                                lines.append("OR")
                        for outcome in group:
                                lines.append("  " + outcome.explain())

                return "\n".join(lines)
