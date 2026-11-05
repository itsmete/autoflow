"""
Expression resolution over an execution context.

Template bodies, action configs and condition fields are all tenant-authored text.
That makes every one of them untrusted input, so nothing here may be *executed*: no
`eval`, no `exec`, no Jinja2 (ROADMAP section 5.4). What is allowed is small and
enumerable - a dotted path with list indices, a truthiness test for optional blocks,
and a closed set of value filters. The whole surface is below, which is the point:
it can be read in one sitting.

A path walks dicts and lists only. It never does `getattr`, so `{{ __class__.__mro__ }}`
cannot escape the payload even when a value in the context is a live object: the
segment is just a dict key that does not exist, and the answer is MISSING.

Three consumers share this module (§4.5): the action config, the condition field and
the template body. One resolver, one set of rules, one place to test.

No Django here on purpose - see tests/test_engine_purity.py.
"""
import json
import re
from collections.abc import Mapping, Sized
from datetime import date, datetime, timezone
from decimal import Decimal

from .exceptions import TemplateRenderError
from .paths import MISSING, resolve_path, split_path


# The namespaces `build_execution_context` always provides. A path that starts with
# one of them is talking about the run, not about the trigger payload, so the legacy
# fallback in resolve_context_path() must not shadow it.
RESERVED_NAMESPACES = frozenset({
        'trigger','steps','automation','tenant','branch','now','vars',
})

# Names the executor owns for the duration of a pass. They are kept out of the
# context's top level for the same reason the namespaces above are, but they are
# deliberately NOT in RESERVED_NAMESPACES: build_execution_context never provides
# them, and `_mode` is injected by execute_graph() and removed again before the
# context is persisted. A `set_variable` step named `_mode` used to be able to
# overwrite the run's dry-run marker mid-walk; a tenant-authored variable must never
# decide whether a step has an effect.
EXECUTOR_NAMESPACES = frozenset({'_mode'})


def _meta(obj):
        """
        `{'id': ..., 'name': ...}` for a model instance, a plain mapping or None.

        getattr and str() rather than an ORM import: the engine is not allowed to
        know what an Automation or a Tenant is (tests/test_engine_purity.py). UUIDs
        become strings because the context is written to a JSON log.
        """
        if obj is None:
                return {}

        meta = {}

        for attribute in ('id','name'):
                if isinstance(obj,Mapping):
                        value = obj.get(attribute)
                else:
                        value = getattr(obj,attribute,None)

                if value is not None:
                        meta[attribute] = str(value)

        return meta


def _member(obj, name):
        """An attribute of a model instance or a key of a mapping, without caring which."""
        if isinstance(obj,Mapping):
                return obj.get(name)
        return getattr(obj,name,None)


def _now_iso(now):
        """The clock as a string; injected by the caller so tests and replays can pin it."""
        if now is None:
                return datetime.now(timezone.utc).isoformat()

        if isinstance(now,(datetime,date)):
                return now.isoformat()

        return str(now)


def build_execution_context(trigger_payload = None,automation = None,steps = None,
                            vars = None,now = None) -> dict:
        """
        The one context every expression is resolved against (§4.5).

        The trigger payload is both namespaced under `trigger` and copied to the top
        level. The duplication is deliberate: `{{trigger.customer.email}}` is the shape
        we want people to write, while every automation and template that predates the
        context speaks `{{customer.email}}`. Dropping the legacy spelling would break
        all of them at once, for no gain.
        """
        trigger_dict = dict(trigger_payload) if isinstance(trigger_payload,Mapping) else {}

        context = dict(trigger_dict)

        context['trigger'] = dict(trigger_dict)
        context['steps'] = dict(steps or {})
        context['automation'] = _meta(automation)
        context['tenant'] = _meta(_member(automation,'tenant'))
        context['branch'] = _meta(_member(automation,'branch'))
        context['now'] = _now_iso(now)
        context['vars'] = dict(vars or {})

        return context


def resolve_context_path(context,path):
        """
        Resolve `path` against the context, falling back to the bare trigger payload.

        Returns MISSING when nothing matches. An empty answer and a `None` answer are
        different questions again - `{{discount}}` on a payload with no discount at all
        must not be confused with `{"discount": null}`.
        """
        if not isinstance(context,Mapping):
                return MISSING

        value = resolve_path(context,path)

        if value is not MISSING:
                return value

        if 'trigger' in context:
                segments = split_path(path)

                if segments and segments[0] not in RESERVED_NAMESPACES:
                        return resolve_path(context['trigger'],path)

        return MISSING


#
# Filters
#

# `YYYY`, `DD` and friends are the tokens people actually type; `%Y-%m-%d` is what
# strftime wants. Longest first so `YYYY` is not half-eaten by a `YY` rule.
_DATE_TOKENS = (
        ('YYYY','%Y'),
        ('HH','%H'),
        ('MM','%m'),
        ('DD','%d'),
        ('mm','%M'),
        ('SS','%S'),
)


def _as_number(value):
        if isinstance(value,(int,float,Decimal)):
                return float(value)

        if isinstance(value,str):
                try:
                        return float(value.strip())
                except ValueError:
                        pass

        raise TemplateRenderError("Not a number: %r" % (value,))


def _as_datetime(value):
        if isinstance(value,datetime):
                return value

        if isinstance(value,date):
                return datetime(value.year,value.month,value.day)

        if isinstance(value,str):
                text = value.strip()

                # fromisoformat() before 3.11 refuses the military `Z` suffix, and
                # `2026-08-03T12:00:00Z` is the most common ISO spelling of all.
                if text.endswith('Z'):
                        text = text[:-1] + '+00:00'

                try:
                        return datetime.fromisoformat(text)
                except ValueError:
                        pass

        raise TemplateRenderError("Not an ISO-8601 date: %r" % (value,))


def _date_format(spec):
        if '%' in spec:
                return spec

        formatted = spec

        for token,directive in _DATE_TOKENS:
                formatted = formatted.replace(token,directive)

        return formatted


def _is_empty(value):
        return value is MISSING or value is None or value == ''


def apply_filter(value,filter_name,filter_arg = None):
        """
        One filter. Value in, value out, no side effects anywhere.

        Missing values are empty strings rather than errors for the text filters: a
        template that mentions `{{nickname | upper}}` should render with a hole, not
        fail the run. `currency` and `date` do raise - a malformed total is a data
        problem somebody has to see, and `0.00 TRY` would hide it.
        """
        name = (filter_name or '').strip()

        if name == 'default':
                if _is_empty(value):
                        return '' if filter_arg is None else filter_arg
                return value

        if name == 'upper':
                return '' if _is_empty(value) else str(value).upper()

        if name == 'lower':
                return '' if _is_empty(value) else str(value).lower()

        if name == 'strip':
                return '' if _is_empty(value) else str(value).strip()

        if name == 'title':
                return '' if _is_empty(value) else str(value).title()

        if name == 'length':
                if _is_empty(value):
                        return 0
                if isinstance(value,Sized):
                        return len(value)
                return len(str(value))

        if name == 'currency':
                if _is_empty(value):
                        return ''
                code = filter_arg or 'TRY'
                return '%.2f %s' % (_as_number(value),code)

        if name == 'date':
                if _is_empty(value):
                        return ''
                fmt = _date_format(filter_arg or '%Y-%m-%d')
                try:
                        return _as_datetime(value).strftime(fmt)
                except ValueError as error:
                        raise TemplateRenderError(
                                "Unusable date format %r: %s" % (filter_arg,error)
                        )

        if name == 'json':
                return json.dumps(None if value is MISSING else value,ensure_ascii = False)

        raise TemplateRenderError("Unknown filter: %s" % name)


#
# Parsing
#

# `{% if x %}` as well as `{%if x%}`: both spellings exist in the templates people
# have already written.
_BLOCK = re.compile(r'\{%\s*if\s+(.+?)\s*%\}(.*?)\{%\s*endif\s*%\}',re.DOTALL)
# Also used with fullmatch() in resolve_value(): a string that is *only* a placeholder
# is a value there, not text.
_PLACEHOLDER = re.compile(r'\{\{\s*(.+?)\s*\}\}',re.DOTALL)


def _split_pipes(expression):
        """
        Split a filter chain on '|', ignoring pipes inside quotes.

        `{{now | date:'d MMMM | yyyy'}}` is a legitimate format spec, so a plain
        str.split('|') would shred it.
        """
        parts = []
        current = []
        quote = None

        for character in expression:
                if quote is not None:
                        current.append(character)
                        if character == quote:
                                quote = None
                        continue

                if character in ('"',"'"):
                        quote = character
                        current.append(character)
                        continue

                if character == '|':
                        parts.append(''.join(current))
                        current = []
                        continue

                current.append(character)

        parts.append(''.join(current))

        return parts


def _unquote(text):
        text = text.strip()

        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"',"'"):
                return text[1:-1]

        return text


def parse_placeholder(expression):
        """
        `path | filter | filter:argument` -> (path, [(name, argument), ...]).

        An argument is a bare string by design: there is no literal grammar to get
        wrong, and `default:0` rendering as the text "0" is what a template wants.
        """
        parts = _split_pipes(expression)
        path = parts[0].strip()
        filters = []

        for part in parts[1:]:
                name,separator,argument = part.partition(':')

                if separator:
                        filters.append((name.strip(),_unquote(argument)))
                else:
                        filters.append((name.strip(),None))

        return path,filters


def evaluate_placeholder(expression,context):
        """Resolve one `{{...}}` body and run its filters left to right."""
        path,filters = parse_placeholder(expression)

        value = resolve_context_path(context,path)

        for name,argument in filters:
                value = apply_filter(value,name,argument)

        return value


def _render_block(match,context):
        value = resolve_context_path(context,match.group(1).strip())

        # MISSING is already falsy, but written out because "absent" and "empty" are
        # different answers that happen to lead to the same rendering here.
        if value is not MISSING and bool(value):
                return match.group(2)

        return ''


def _render_placeholder(match,context):
        value = evaluate_placeholder(match.group(1),context)

        if value is MISSING or value is None:
                return ''

        return str(value)


def render_string(template_str,context):
        """
        Substitute placeholders and optional blocks in a block of text.

        Blocks are handled first so that a placeholder inside a kept block is then
        resolved normally. Nesting is not supported and is not silently mangled: the
        inner block simply survives as text, which is visible.
        """
        if not isinstance(template_str,str):
                return template_str

        if not isinstance(context,Mapping):
                context = {}

        text = _BLOCK.sub(lambda match : _render_block(match,context),template_str)

        return _PLACEHOLDER.sub(lambda match : _render_placeholder(match,context),text)


def resolve_value(value,context):
        """
        Resolve every string inside a config/document, keeping native types.

        A string that is exactly one placeholder becomes the value it points at, so
        `{"count": "{{steps.1.output.count}}"}` sends the number 5 and not the string
        "5" to a provider. Strings with text around the placeholder are interpolated,
        and dicts/lists are walked so a whole action config can be resolved at once.
        """
        if isinstance(value,str):
                single = _PLACEHOLDER.fullmatch(value.strip())

                # Adjacent placeholders (`{{a}}{{b}}`) also match lazily from the first
                # `{{` to the last `}}`; those are text, not a single value.
                if single is not None and '{{' not in single.group(1) and '{%' not in single.group(1):
                        path,filters = parse_placeholder(single.group(1))
                        resolved = resolve_context_path(context,path)

                        if not filters:
                                return '' if resolved is MISSING else resolved

                        for name,argument in filters:
                                resolved = apply_filter(resolved,name,argument)

                        return resolved

                if '{{' in value or '{%' in value:
                        return render_string(value,context)

                return value

        if isinstance(value,dict):
                return {key : resolve_value(item,context) for key,item in value.items()}

        if isinstance(value,list):
                return [resolve_value(item,context) for item in value]

        return value
