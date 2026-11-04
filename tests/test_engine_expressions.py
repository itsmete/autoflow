"""
The expression layer: execution context, path resolution, filters, conditional
blocks and templating - and the fact that none of it can execute a line of code.

Phase 1.C (ROADMAP sections 4.5 and 5.4). Template bodies and action configs are
tenant-authored text, so `eval`, `exec` and Jinja2 are all off the table; this file
is largely about proving that what replaced them is enough, and that it is not more
than enough.

The old `TemplateManager` behaviour keeps its regression baseline in
test_automations_engine.py - everything there still has to pass, because declared
`Template.fields` were not allowed to stop working.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase, TestCase

from apps.automations.engine.exceptions import TemplateRenderError
from apps.automations.engine.expressions import (
    RESERVED_NAMESPACES,
    apply_filter,
    build_execution_context,
    evaluate_placeholder,
    parse_placeholder,
    render_string,
    resolve_context_path,
    resolve_value,
)
from apps.automations.engine.paths import MISSING
from apps.automations.engine.template_manager import TemplateManager
from tests import factories


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

STEPS = {
    "1": {"status": "success", "output": {"id": "ord_1", "count": 5,
                                          "items": [{"sku": "a"}, {"sku": "b"}]}},
    "2": {"status": "failed", "output": {}},
}


class ExecutionContextTests(SimpleTestCase):

    def test_every_namespace_the_spec_names_is_present(self):
        context = build_execution_context({}, now=NOW)
        for namespace in ("trigger", "steps", "automation", "tenant", "branch",
                          "now", "vars"):
            self.assertIn(namespace, context)
        self.assertLessEqual(RESERVED_NAMESPACES, set(context))

    def test_the_trigger_payload_is_both_namespaced_and_at_the_top_level(self):
        context = build_execution_context({"customer": {"email": "a@b.com"}}, now=NOW)

        self.assertEqual(context["trigger"]["customer"]["email"], "a@b.com")
        # The legacy spelling has to keep working: every template written before
        # the context existed addresses the payload directly.
        self.assertEqual(context["customer"]["email"], "a@b.com")

    def test_a_payload_that_is_not_a_mapping_becomes_an_empty_trigger(self):
        context = build_execution_context(["not", "a", "mapping"], now=NOW)
        self.assertEqual(context["trigger"], {})

    def test_automation_metadata_is_serialisable(self):
        automation = SimpleNamespace(id=uuid4(), name="Welcome",
                                     tenant=SimpleNamespace(id=uuid4(), name="Acme"),
                                     branch=None)
        context = build_execution_context({}, automation=automation, now=NOW)

        self.assertEqual(context["automation"]["id"], str(automation.id))
        self.assertEqual(context["automation"]["name"], "Welcome")
        self.assertEqual(context["tenant"], {"id": str(automation.tenant.id), "name": "Acme"})
        # Branch is None here, and an empty dict is a better answer than a crash.
        self.assertEqual(context["branch"], {})

    def test_a_mapping_automation_is_accepted_too(self):
        context = build_execution_context(
            {}, automation={"id": "abc", "name": "Welcome",
                            "tenant": {"id": "t1", "name": "Acme"}},
            now=NOW,
        )
        self.assertEqual(context["automation"], {"id": "abc", "name": "Welcome"})
        self.assertEqual(context["tenant"], {"id": "t1", "name": "Acme"})

    def test_steps_and_vars_default_to_empty_dicts(self):
        context = build_execution_context(now=NOW)
        self.assertEqual(context["steps"], {})
        self.assertEqual(context["vars"], {})

    def test_steps_and_vars_are_carried_through(self):
        context = build_execution_context({}, steps=STEPS, vars={"tier": "gold"}, now=NOW)
        self.assertEqual(context["steps"]["1"]["output"]["count"], 5)
        self.assertEqual(context["vars"]["tier"], "gold")

    def test_an_injected_clock_is_used_as_given(self):
        self.assertEqual(build_execution_context({}, now=NOW)["now"], NOW.isoformat())
        self.assertEqual(build_execution_context({}, now="pinned")["now"], "pinned")

    def test_the_clock_defaults_to_utc_now(self):
        before = datetime.now(timezone.utc)
        context = build_execution_context({})
        after = datetime.now(timezone.utc)

        rendered = datetime.fromisoformat(context["now"])
        self.assertTrue(before - timedelta(seconds=1) <= rendered <= after + timedelta(seconds=1))

    def test_a_reserved_namespace_is_not_shadowed_by_the_payload(self):
        # A trigger payload may legitimately contain a key called `steps`; the run's
        # own steps must win, otherwise a payload could rewrite the execution context.
        context = build_execution_context({"steps": {"1": "evil"}}, now=NOW)
        self.assertEqual(context["steps"], {})


class ResolveContextPathTests(SimpleTestCase):

    def setUp(self):
        self.context = build_execution_context(
            {"customer": {"name": "Ada", "address": {"city": "izmir"}}},
            automation={"id": "auto-1", "name": "Welcome", "tenant": {"id": "t1", "name": "Acme"}},
            steps=STEPS,
            vars={"tier": "gold"},
            now=NOW,
        )

    def test_a_namespaced_trigger_path(self):
        self.assertEqual(resolve_context_path(self.context, "trigger.customer.name"), "Ada")

    def test_the_legacy_un_namespaced_path_still_resolves(self):
        self.assertEqual(resolve_context_path(self.context, "customer.address.city"), "izmir")

    def test_a_numeric_step_key(self):
        self.assertEqual(resolve_context_path(self.context, "steps.1.output.id"), "ord_1")

    def test_an_index_into_a_step_output(self):
        self.assertEqual(resolve_context_path(self.context, "steps.1.output.items[1].sku"), "b")

    def test_vars_and_now(self):
        self.assertEqual(resolve_context_path(self.context, "vars.tier"), "gold")
        self.assertEqual(resolve_context_path(self.context, "now"), NOW.isoformat())

    def test_a_reserved_root_does_not_fall_back_to_the_trigger(self):
        # `automation` is empty here, but the trigger payload has one; the namespace
        # must still answer, or `{{automation.name}}` would silently read user data.
        context = {"trigger": {"automation": {"name": "legacy"}}, "automation": {}}
        self.assertIs(resolve_context_path(context, "automation.name"), MISSING)

    def test_an_unknown_path_is_missing(self):
        self.assertIs(resolve_context_path(self.context, "customer.phone"), MISSING)

    def test_a_non_mapping_context_is_missing_rather_than_an_error(self):
        self.assertIs(resolve_context_path(None, "customer.name"), MISSING)

    def test_a_one_segment_path_resolves_both_ways(self):
        self.assertEqual(resolve_context_path({"x": 1, "trigger": {"x": 2}}, "x"), 1)
        self.assertEqual(resolve_context_path({"trigger": {"x": 2}}, "x"), 2)


class FilterTests(SimpleTestCase):

    def test_default_fills_none_empty_and_missing(self):
        self.assertEqual(apply_filter(None, "default", "guest"), "guest")
        self.assertEqual(apply_filter("", "default", "guest"), "guest")
        self.assertEqual(apply_filter(MISSING, "default", "guest"), "guest")
        self.assertEqual(apply_filter(MISSING, "default"), "")

    def test_default_keeps_a_present_value_and_its_type(self):
        self.assertEqual(apply_filter(5, "default", "0"), 5)
        self.assertEqual(apply_filter("Ada", "default", "guest"), "Ada")

    def test_the_text_filters(self):
        self.assertEqual(apply_filter("ada", "upper"), "ADA")
        self.assertEqual(apply_filter("ADA", "lower"), "ada")
        self.assertEqual(apply_filter("  ada  ", "strip"), "ada")
        self.assertEqual(apply_filter("ada lovelace", "title"), "Ada Lovelace")

    def test_the_text_filters_render_an_empty_string_for_a_missing_value(self):
        for name in ("upper", "lower", "strip", "title"):
            self.assertEqual(apply_filter(MISSING, name), "", name)
            self.assertEqual(apply_filter(None, name), "", name)

    def test_length_of_a_string_a_list_a_dict_and_a_number(self):
        self.assertEqual(apply_filter("abc", "length"), 3)
        self.assertEqual(apply_filter([1, 2], "length"), 2)
        self.assertEqual(apply_filter({"a": 1}, "length"), 1)
        self.assertEqual(apply_filter(123, "length"), 3)
        self.assertEqual(apply_filter(MISSING, "length"), 0)

    def test_currency_formats_two_decimals_and_the_default_code(self):
        self.assertEqual(apply_filter(10, "currency", "TRY"), "10.00 TRY")
        self.assertEqual(apply_filter("10.5", "currency", "TRY"), "10.50 TRY")
        self.assertEqual(apply_filter(3.14159, "currency"), "3.14 TRY")

    def test_currency_refuses_to_invent_a_number(self):
        # Rendering "0.00 TRY" for a malformed total would hide a data problem behind
        # a plausible looking message.
        with self.assertRaises(TemplateRenderError):
            apply_filter("on request", "currency", "TRY")

    def test_currency_of_a_missing_value_is_empty(self):
        self.assertEqual(apply_filter(MISSING, "currency", "TRY"), "")
        self.assertEqual(apply_filter(None, "currency", "TRY"), "")

    def test_date_accepts_strftime_directives(self):
        self.assertEqual(apply_filter(NOW, "date", "%Y-%m-%d"), "2026-08-03")

    def test_date_accepts_the_tokens_people_actually_type(self):
        self.assertEqual(apply_filter(NOW, "date", "YYYY-MM-DD"), "2026-08-03")
        self.assertEqual(apply_filter(NOW, "date", "DD.MM.YYYY"), "03.08.2026")
        self.assertEqual(apply_filter(NOW, "date", "HH:mm"), "12:00")

    def test_date_parses_an_iso_string_including_the_z_suffix(self):
        self.assertEqual(apply_filter("2026-08-03T12:00:00Z", "date", "%Y-%m-%d"), "2026-08-03")
        self.assertEqual(apply_filter("2026-08-03", "date", "%d/%m/%Y"), "03/08/2026")

    def test_date_parses_a_plain_date(self):
        self.assertEqual(apply_filter(date(2026, 8, 3), "date", "%Y"), "2026")

    def test_a_date_that_cannot_be_parsed_raises(self):
        with self.assertRaises(TemplateRenderError):
            apply_filter("last tuesday", "date", "%Y-%m-%d")

    def test_date_of_a_missing_value_is_empty(self):
        self.assertEqual(apply_filter(MISSING, "date"), "")
        self.assertEqual(apply_filter(None, "date"), "")

    def test_a_default_date_format_is_used_when_none_is_given(self):
        self.assertEqual(apply_filter(NOW, "date"), "2026-08-03")

    def test_json_keeps_unicode_and_turns_missing_into_null(self):
        self.assertEqual(apply_filter({"a": "İzmir"}, "json"), '{"a": "İzmir"}')
        self.assertEqual(apply_filter(MISSING, "json"), "null")

    def test_an_unknown_filter_raises(self):
        with self.assertRaises(TemplateRenderError):
            apply_filter("x", "escape_html")


class PlaceholderParsingTests(SimpleTestCase):

    def test_a_bare_path(self):
        self.assertEqual(parse_placeholder("customer.name"), ("customer.name", []))

    def test_a_filter_chain(self):
        self.assertEqual(
            parse_placeholder("name | default:'guest' | upper"),
            ("name", [("default", "guest"), ("upper", None)]),
        )

    def test_quotes_are_stripped_from_an_argument(self):
        self.assertEqual(parse_placeholder('x | default:"N/A"')[1], [("default", "N/A")])

    def test_a_pipe_inside_a_quoted_argument_is_not_a_separator(self):
        path, filters = parse_placeholder("now | date:'d MMMM | yyyy'")
        self.assertEqual(path, "now")
        self.assertEqual(filters, [("date", "d MMMM | yyyy")])

    def test_an_argument_may_contain_a_colon(self):
        self.assertEqual(parse_placeholder("now | date:'%H:%M'")[1], [("date", "%H:%M")])


class RenderStringTests(SimpleTestCase):

    def setUp(self):
        self.context = build_execution_context(
            {"customer": {"name": "Ada", "vip": True},
             "items": [{"name": "kalem"}, {"name": "defter"}]},
            steps=STEPS,
            vars={"tier": "gold"},
            now=NOW,
        )

    def test_a_plain_placeholder(self):
        self.assertEqual(render_string("Merhaba {{customer.name}}", self.context), "Merhaba Ada")

    def test_whitespace_inside_the_braces_is_optional(self):
        self.assertEqual(render_string("{{customer.name}}", self.context), "Ada")
        self.assertEqual(render_string("{{  customer.name  }}", self.context), "Ada")

    def test_an_indexed_path(self):
        self.assertEqual(render_string("{{items[0].name}}", self.context), "kalem")

    def test_a_new_namespaced_path(self):
        self.assertEqual(render_string("Sipariş {{steps.1.output.id}}", self.context), "Sipariş ord_1")

    def test_an_unknown_placeholder_renders_empty_rather_than_the_raw_text(self):
        self.assertEqual(render_string("Merhaba {{customer.phone}}", self.context), "Merhaba ")

    def test_a_none_value_renders_empty(self):
        context = build_execution_context({"discount": None}, now=NOW)
        self.assertEqual(render_string("[{{discount}}]", context), "[]")

    def test_a_falsy_zero_still_renders(self):
        # `if not value` would have erased this: 0 is a legitimate answer.
        context = build_execution_context({"count": 0}, now=NOW)
        self.assertEqual(render_string("{{count}}", context), "0")

    def test_a_single_filter(self):
        self.assertEqual(render_string("{{customer.name | upper}}", self.context), "ADA")

    def test_chained_filters_run_left_to_right(self):
        context = build_execution_context({}, now=NOW)
        self.assertEqual(render_string("{{name | default:'guest' | upper}}", context), "GUEST")

    def test_a_filtered_number(self):
        self.assertEqual(
            render_string("{{steps.1.output.count | currency:TRY}}", self.context), "5.00 TRY"
        )

    def test_a_filtered_date(self):
        self.assertEqual(render_string("{{now | date:DD.MM.YYYY}}", self.context), "03.08.2026")

    def test_two_adjacent_placeholders(self):
        # The lazy single-placeholder pattern would happily swallow both at once.
        self.assertEqual(render_string("{{customer.name}}{{vars.tier}}", self.context), "Adagold")

    def test_a_conditional_block_whose_path_is_truthy_is_kept(self):
        text = "Merhaba {{customer.name}}{%if customer.vip%} - VIP{%endif%}"
        self.assertEqual(render_string(text, self.context), "Merhaba Ada - VIP")

    def test_a_conditional_block_whose_path_is_falsy_is_dropped(self):
        context = build_execution_context({"customer": {"name": "Ada", "vip": False}}, now=NOW)
        text = "Merhaba {{customer.name}}{%if customer.vip%} - VIP{%endif%}"
        self.assertEqual(render_string(text, context), "Merhaba Ada")

    def test_a_conditional_block_whose_path_is_absent_is_dropped(self):
        text = "Hi{%if bonus%} - you earned {{bonus}}{%endif%}"
        self.assertEqual(render_string(text, self.context), "Hi")

    def test_the_spaced_and_unspaced_block_spellings_both_work(self):
        context = build_execution_context({"bonus": 10}, now=NOW)
        self.assertEqual(render_string("{%if bonus%}A{%endif%}", context), "A")
        self.assertEqual(render_string("{% if bonus %}A{% endif %}", context), "A")

    def test_a_block_can_span_lines(self):
        context = build_execution_context({"bonus": 10}, now=NOW)
        self.assertEqual(render_string("Hi{%if bonus%}\n 10\n{%endif%}", context), "Hi\n 10\n")

    def test_a_block_on_a_step_output(self):
        context = build_execution_context({}, steps=STEPS, now=NOW)
        text = "{%if steps.1.output.count%}had {{steps.1.output.count}}{%endif%}"
        self.assertEqual(render_string(text, context), "had 5")

    def test_evaluate_placeholder_resolves_the_path_and_the_filters(self):
        self.assertEqual(evaluate_placeholder("customer.name | upper", self.context), "ADA")
        self.assertIs(evaluate_placeholder("customer.vip", self.context), True)

    def test_text_without_any_expression_is_returned_unchanged(self):
        self.assertEqual(render_string("Merhaba Ada", self.context), "Merhaba Ada")

    def test_a_non_string_body_is_returned_unchanged(self):
        self.assertEqual(render_string(None, self.context), None)


class ResolveValueTests(SimpleTestCase):
    """Native types: a lone placeholder is a value, text around it is text."""

    def setUp(self):
        self.context = build_execution_context(
            {"flag": True, "nothing": None, "price": 12.5},
            steps=STEPS,
            vars={"tier": "gold"},
            now=NOW,
        )

    def test_a_lone_placeholder_keeps_its_type(self):
        self.assertEqual(resolve_value("{{steps.1.output.count}}", self.context), 5)
        self.assertIsInstance(resolve_value("{{steps.1.output.count}}", self.context), int)

        self.assertEqual(
            resolve_value("{{steps.1.output.items}}", self.context),
            [{"sku": "a"}, {"sku": "b"}],
        )
        self.assertIs(resolve_value("{{flag}}", self.context), True)
        self.assertEqual(resolve_value("{{price}}", self.context), 12.5)
        self.assertIsNone(resolve_value("{{nothing}}", self.context))

    def test_a_lone_placeholder_with_surrounding_whitespace_is_still_a_value(self):
        self.assertEqual(resolve_value("  {{steps.1.output.count}}  ", self.context), 5)

    def test_interpolation_produces_a_string(self):
        self.assertEqual(
            resolve_value("Count: {{steps.1.output.count}}", self.context), "Count: 5"
        )

    def test_interpolation_of_a_non_string_value_uses_str(self):
        self.assertEqual(resolve_value("{{flag}}!", self.context), "True!")

    def test_a_missing_lone_placeholder_becomes_an_empty_string(self):
        self.assertEqual(resolve_value("{{customer.phone}}", self.context), "")
        self.assertEqual(resolve_value("{{customer.phone}}!", self.context), "!")

    def test_a_lone_placeholder_with_filters_returns_the_filtered_value(self):
        self.assertEqual(resolve_value("{{price | currency:TRY}}", self.context), "12.50 TRY")
        self.assertEqual(resolve_value("{{vars.tier | upper}}", self.context), "GOLD")
        self.assertEqual(resolve_value("{{missing | default:guest}}", self.context), "guest")

    def test_a_string_without_expressions_is_untouched(self):
        self.assertEqual(resolve_value("Merhaba Ada", self.context), "Merhaba Ada")

    def test_a_dict_is_walked(self):
        config = {
            "to": "{{customer.email}}",
            "count": "{{steps.1.output.count}}",
            "nested": {"ids": ["{{steps.1.output.id}}", "literal"]},
            "static": 7,
        }
        resolved = resolve_value(config, self.context)

        self.assertEqual(resolved["to"], "")
        self.assertEqual(resolved["count"], 5)
        self.assertEqual(resolved["nested"]["ids"], ["ord_1", "literal"])
        self.assertEqual(resolved["static"], 7)

    def test_a_list_is_walked(self):
        self.assertEqual(
            resolve_value(["{{vars.tier}}", 1, None], self.context), ["gold", 1, None]
        )

    def test_a_non_string_scalar_is_returned_unchanged(self):
        self.assertEqual(resolve_value(5, self.context), 5)
        self.assertIsNone(resolve_value(None, self.context))


class SSTISafetyTests(SimpleTestCase):
    """
    Nothing in a template is code, so nothing in a template can be made to run.

    These are not theoretical: template bodies are tenant-authored text, and the
    engine resolves them server side with the operator's own privileges behind them.
    """

    def setUp(self):
        self.context = build_execution_context(
            {"customer": {"name": "Ada"}, "secret": "s3cr3t"}, now=NOW
        )

    def test_dunder_attribute_access_does_not_escape_the_payload(self):
        # Paths walk dict keys, never attributes, and `__class__` is not a key.
        self.assertEqual(render_string("{{ __class__.__mro__ }}", self.context), "")

    def test_attribute_access_inside_a_string_value_stays_a_dict_lookup(self):
        self.assertEqual(render_string("{{ customer.__class__ }}", self.context), "")

    def test_a_call_expression_is_not_a_call(self):
        self.assertEqual(render_string("{%if __import__('os')%}pwned{%endif%}", self.context), "")
        self.assertEqual(render_string("{{ __import__('os').system('ls') }}", self.context), "")

    def test_an_operator_is_not_evaluated(self):
        # No expression grammar exists, so this is a path named "1 == 1" that does not
        # exist. The block disappears; it does not become True.
        self.assertEqual(render_string("{%if 1 == 1%}yes{%endif%}", self.context), "")

    def test_the_whole_context_is_not_dumpable_from_a_template(self):
        self.assertEqual(render_string("{{ trigger }}", self.context), "{'customer': {'name': 'Ada'}, 'secret': 's3cr3t'}")
        # ...but a wildcard path cannot be used to fan out over everything, either.
        self.assertEqual(render_string("{{ vars | json }}", self.context), "{}")

    def test_filters_are_a_closed_set(self):
        with self.assertRaises(TemplateRenderError):
            render_string("{{ customer.name | pwn }}", self.context)


class TemplateManagerExpressionTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.manager = TemplateManager()

    def test_an_undeclared_dotted_path_resolves_from_the_payload(self):
        template = factories.make_template(
            self.tenant,
            raw_text="Merhaba {{customer.address.city}}",
            fields={},
        )
        rendered = self.manager.render(
            template, {}, {"customer": {"address": {"city": "izmir"}}}
        )
        self.assertEqual(rendered, "Merhaba izmir")

    def test_a_declared_field_may_point_at_a_dotted_path(self):
        # `location.city` is declared as one field, so the lookup walks into the
        # payload instead of handing a whole dict to the template.
        template = factories.make_template(
            self.tenant,
            raw_text="Merhaba {{location.city}}",
            fields={"location.city": {"type": "string", "required": True,
                                      "source": "payload", "description": "city"}},
        )
        rendered = self.manager.render(
            template, {}, {"location": {"city": "izmir"}}
        )
        self.assertEqual(rendered, "Merhaba izmir")

    def test_a_declared_field_uses_a_simple_key(self):
        template = factories.make_template(
            self.tenant,
            raw_text="{{customer}}",
            fields={"customer": {"type": "string", "required": True,
                                 "source": "payload", "description": "c"}},
        )
        self.assertEqual(self.manager.render(template, {}, {"customer": "Ada"}), "Ada")

    def test_a_fetched_field_wins_over_the_raw_payload(self):
        # The declaration says where the value comes from; that promise has to be
        # stronger than whatever else happens to sit in the payload.
        template = factories.make_template(
            self.tenant,
            raw_text="{{customer}}",
            fields={"customer": {"type": "string", "required": False,
                                 "source": "config", "description": "c"}},
        )
        rendered = self.manager.render(template, {"customer": "Config"}, {"customer": "Payload"})
        self.assertEqual(rendered, "Config")

    def test_templates_can_read_steps_and_filters(self):
        template = factories.make_template(
            self.tenant,
            raw_text="Sipariş {{steps.1.output.id}} - {{total | currency:TRY}}",
            fields={},
        )
        rendered = self.manager.render(
            template, {}, {"steps": {"1": {"output": {"id": "ord_1"}}}, "total": 99}
        )
        self.assertEqual(rendered, "Sipariş ord_1 - 99.00 TRY")

    def test_a_conditional_block_on_a_dotted_path(self):
        template = factories.make_template(
            self.tenant,
            raw_text="{{customer.name}} {%if customer.vip%}VIP{%endif%}",
            fields={"customer.name": {"type": "string", "required": True,
                                      "source": "payload", "description": "c"}},
        )
        self.assertEqual(
            self.manager.render(template, {}, {"customer": {"name": "Ada", "vip": True}}),
            "Ada VIP",
        )

    def test_an_empty_field_map_still_raises_for_an_unknown_source_type_only(self):
        # `{}` is a legitimate template now: nothing is declared, so nothing to fetch.
        template = factories.make_template(self.tenant, raw_text="x", fields={})
        self.assertEqual(self.manager.fetch(template, {}, {}), {})

    def test_fill_merges_context_and_fetched_values(self):
        template = factories.make_template(self.tenant, raw_text="{{a}}-{{b}}", fields={})
        self.assertEqual(
            self.manager.fill(template, {"b": "2"}, context={"a": "1"}), "1-2"
        )

    def test_fill_still_accepts_a_single_resolved_mapping(self):
        # The old signature was fill(template, resolved_fields) and it is still used
        # by callers that have already done the fetching.
        template = factories.make_template(self.tenant, raw_text="Hi {{customer}}!", fields={})
        self.assertEqual(self.manager.fill(template, {"customer": "Ada"}), "Hi Ada!")
