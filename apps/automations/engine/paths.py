"""
Path resolution over a trigger payload.

The evaluator used to do `data.get(field)`, which can only see the top level of the
payload - and real payloads are nested. A condition could not say anything about
`customer.address.city` or `items[0].sku`; the whole shape below the first key was
invisible.

No Django here on purpose. This module, operators.py and decision.py are the first
pieces of the framework-free engine core (see tests/test_engine_purity.py).
"""
import re


class _Missing:
        """
        The absence of a value, kept distinct from None.

        None is a legitimate JSON value: `{"discount": null}` says something different
        from a payload with no `discount` key at all. Collapsing the two makes
        `exists` and `is_empty` impossible to define.
        """

        _instance = None

        def __new__(cls):
                if cls._instance is None:
                        cls._instance = super().__new__(cls)
                return cls._instance

        def __repr__(self):
                return "MISSING"

        def __bool__(self):
                return False


MISSING = _Missing()

WILDCARD = '*'

_BRACKET = re.compile(r'\[([^\]]*)\]')


def split_path(path):
        """
        'items[0].price' and 'items.0.price' are the same path.

        Bracket syntax is what people write, dotted segments are what is easy to walk,
        so normalise one into the other rather than supporting two walkers.
        """
        normalised = _BRACKET.sub(r'.\1', path or '')
        return [segment for segment in normalised.split('.') if segment != '']


def _step(current, segment):
        if isinstance(current, dict):
                return current.get(segment, MISSING)

        if isinstance(current, (list,tuple)):
                try:
                        return current[int(segment)]
                except (ValueError,IndexError):
                        return MISSING

        return MISSING


def resolve_path(data, path):
        """
        Walk `path` through `data`, returning MISSING if any segment does not exist.

        A '*' segment fans out over a list and collects the rest of the path from
        every element, so 'items[*].stock' yields a list of stock levels. That is what
        makes quantified conditions ("any item is out of stock") expressible.
        """
        segments = split_path(path)

        if not segments:
                return MISSING

        current = data

        for index,segment in enumerate(segments):

                if segment == WILDCARD:
                        if not isinstance(current,(list,tuple)):
                                return MISSING

                        remainder = '.'.join(segments[index + 1:])
                        collected = []

                        for item in current:
                                value = resolve_path(item,remainder) if remainder else item
                                # A missing key on one element skips that element rather
                                # than poisoning the whole set - "any item is cheap"
                                # should still work when one item has no price.
                                if value is not MISSING:
                                        collected.append(value)

                        return collected

                current = _step(current,segment)

                if current is MISSING:
                        return MISSING

        return current


def has_wildcard(path):
        return WILDCARD in split_path(path)
