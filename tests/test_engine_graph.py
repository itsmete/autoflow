"""
Faz 1.D: graph execution and the control-flow steps.

`execute_actions()` runs a flat list and will keep doing so for one release
(ROADMAP section 5.1). What it cannot express is a branch, a filter that ends a
path, a variable computed on the way, or a pause - and self-service automation is
mostly made of those. This module covers `execute_graph()`: step output capture
into `context['steps'][key]`, expression resolution across step configs, and the
five step types Faz 1 promises.

The last test in this file is the exit criterion of the phase (ROADMAP section 7):
"VIP ise WhatsApp, degilse SMS; 24 saat sonra yanit yoksa yoneticiye bildir" as a
single automation, run twice - once up to the pause, once after it.
"""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from apps.automations.engine.exceptions import ExecutionError
from apps.automations.engine.executor import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_SUCCESS,
    RESULT_WAITING,
    AutomationExecutor,
    GraphExecutionResult,
    parse_wait_duration,
)
from apps.automations.engine.expressions import build_execution_context
from apps.automations.engine.orchestrator import AutomationOrchestrator
from apps.automations.models import (
    AutomationLog,
    AutomationStatus,
    sync_steps_from_actions,
)
from apps.automations.tasks import run_automation
from apps.channels.providers import EmailProvider, SMSProvider, WhatsAppProvider
from tests import factories


WEBHOOK_REQUESTS = "apps.automations.engine.actions.send_webhook.requests.request"


def webhook_config(url):
    return {"url": url, "method": "POST", "headers": {}, "payload": {}}


class WaitDurationTests(SimpleTestCase):
    """`parse_wait_duration` on its own - no database, no graph."""

    def test_shorthand_durations(self):
        for expression,expected in (("30s",30),("15m",900),("24h",86400),("7d",604800)):
            delay,_eta = parse_wait_duration({"duration": expression},{})
            self.assertEqual(delay,expected,expression)

    def test_a_bare_number_is_seconds(self):
        self.assertEqual(parse_wait_duration({"duration": 90},{})[0],90)
        self.assertEqual(parse_wait_duration({"duration": "90"},{})[0],90)

    def test_a_duration_produces_an_eta_from_the_injected_clock(self):
        now = datetime(2026,8,3,12,0,tzinfo = timezone.utc)

        delay,eta = parse_wait_duration({"duration": "24h"},{},now = now)

        self.assertEqual(delay,86400)
        self.assertEqual(eta,datetime(2026,8,4,12,0,tzinfo = timezone.utc))

    def test_until_is_an_absolute_time(self):
        now = datetime(2026,8,3,12,0,tzinfo = timezone.utc)

        delay,eta = parse_wait_duration(
                {"until": "2026-08-03T12:30:00Z"},{},now = now
        )

        self.assertEqual(delay,1800)
        self.assertEqual(eta,datetime(2026,8,3,12,30,tzinfo = timezone.utc))

    def test_until_resolves_an_expression(self):
        context = build_execution_context({"appointment_time": "2026-08-04T09:00:00+00:00"})

        delay,eta = parse_wait_duration(
                {"until": "{{trigger.appointment_time}}"},
                context,
                now = datetime(2026,8,3,9,0,tzinfo = timezone.utc),
        )

        self.assertEqual(delay,86400)
        self.assertEqual(eta,datetime(2026,8,4,9,0,tzinfo = timezone.utc))

    def test_an_until_in_the_past_does_not_produce_a_negative_delay(self):
        delay,_eta = parse_wait_duration(
                {"until": "2020-01-01T00:00:00+00:00"},
                {},
                now = datetime(2026,8,3,12,0,tzinfo = timezone.utc),
        )

        self.assertEqual(delay,0)

    def test_an_unusable_duration_raises(self):
        for value in ("soon","24x","","-5m",True):
            with self.assertRaises(ExecutionError):
                parse_wait_duration({"duration": value},{})

    def test_a_wait_with_neither_duration_nor_until_raises(self):
        with self.assertRaises(ExecutionError):
            parse_wait_duration({},{})


class LinearGraphTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.executor = AutomationExecutor()

    def _run(self, context, **kwargs):
        return self.executor.execute_graph(
                self.automation.steps.all(),
                self.automation.edges.all(),
                context,
                **kwargs
        )

    def test_step_output_flows_into_the_next_step(self):
        template = factories.make_template(self.tenant, raw_text="Merhaba {{customer}}")
        first = factories.make_step(
                self.automation, key="1", action_type="webhook",
                config=webhook_config("https://example.com/1"), template=template,
        )
        second = factories.make_step(
                self.automation, key="2", action_type="webhook",
                config={
                        **webhook_config("https://example.com/2"),
                        "payload": {"echo": "{{steps.1.output.message}}"},
                },
        )
        factories.make_edge(self.automation, None, first)
        factories.make_edge(self.automation, first, second)

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run(build_execution_context({"customer": "Ada"}))

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertEqual(result.context["steps"]["1"]["output"]["message"], "Merhaba Ada")
        self.assertEqual(result.context["steps"]["2"]["status"], "success")
        # The second step sent what the first one produced, not the literal text.
        self.assertEqual(request.call_args.kwargs["json"], {"echo": "Merhaba Ada"})
        self.assertEqual([r["key"] for r in result.step_results], ["1", "2"])

    def test_steps_without_edges_run_in_creation_order(self):
        # The shape an automation has before anybody opens the canvas: no edges, and
        # the order the steps were written in is the order they run in.
        for key in ("1","2","3"):
            factories.make_step(self.automation, key=key, config=webhook_config("https://x/%s" % key))

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run(build_execution_context({}))

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertEqual([r["key"] for r in result.step_results], ["1","2","3"])
        self.assertEqual(
                [call.kwargs["url"] for call in request.call_args_list],
                ["https://x/1","https://x/2","https://x/3"],
        )

    def test_an_unknown_action_type_fails_the_step_and_stops(self):
        broken = factories.make_step(self.automation, key="1", action_type="carrier_pigeon")
        after = factories.make_step(self.automation, key="2", action_type="webhook")
        factories.make_edge(self.automation, None, broken)
        factories.make_edge(self.automation, broken, after)

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run(build_execution_context({}))

        self.assertEqual(result.status, RESULT_FAILED)
        self.assertIn("Unknown action type", result.context["steps"]["1"]["error"])
        self.assertNotIn("2", result.context["steps"])
        request.assert_not_called()

    def test_on_failure_continue_keeps_the_path_going(self):
        broken = factories.make_step(
                self.automation, key="1", action_type="carrier_pigeon", on_failure="continue",
        )
        after = factories.make_step(self.automation, key="2", action_type="webhook")
        factories.make_edge(self.automation, None, broken)
        factories.make_edge(self.automation, broken, after)

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run(build_execution_context({}))

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertEqual(result.context["steps"]["1"]["status"], "failed")
        self.assertEqual(result.context["steps"]["2"]["status"], "success")
        request.assert_called_once()

    def test_on_failure_retry_gives_up_with_an_error(self):
        factories.make_step(
                self.automation, key="1", action_type="carrier_pigeon", on_failure="retry",
        )
        factories.make_edge(self.automation, None, self.automation.steps.get(key="1"))

        with self.assertRaises(ExecutionError):
            self._run(build_execution_context({}))

    def test_an_unknown_start_step_is_an_error(self):
        factories.make_step(self.automation, key="1")

        with self.assertRaises(ExecutionError):
            self._run(build_execution_context({}), start_step_key="nope")


class SetVariableTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.executor = AutomationExecutor()

    def _run(self, context):
        return self.executor.execute_graph(
                self.automation.steps.all(), self.automation.edges.all(), context
        )

    def test_a_variable_is_computed_from_the_trigger(self):
        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {
                        "greeting": "Merhaba {{trigger.customer}}",
                        "total": "{{trigger.amount | currency:TRY}}",
                }},
        )
        action = factories.make_step(
                self.automation, key="send", action_type="webhook",
                config={
                        **webhook_config("https://example.com/hook"),
                        "payload": {"text": "{{vars.greeting}}"},
                },
        )
        factories.make_edge(self.automation, None, variables)
        factories.make_edge(self.automation, variables, action)

        context = build_execution_context({"customer": "Ada", "amount": 1250})

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run(context)

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertEqual(result.context["vars"]["greeting"], "Merhaba Ada")
        self.assertEqual(result.context["vars"]["total"], "1250.00 TRY")
        self.assertEqual(result.context["steps"]["vars"]["output"]["greeting"], "Merhaba Ada")
        self.assertEqual(request.call_args.kwargs["json"], {"text": "Merhaba Ada"})

    def test_a_later_variable_can_read_an_earlier_one(self):
        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {
                        "greeting": "Merhaba {{trigger.customer}}",
                        "line": "{{vars.greeting}}, randevunuz yarin.",
                }},
        )
        factories.make_edge(self.automation, None, variables)

        result = self._run(build_execution_context({"customer": "Ada"}))

        self.assertEqual(result.context["vars"]["line"], "Merhaba Ada, randevunuz yarin.")

    def test_a_variable_does_not_shadow_a_reserved_namespace(self):
        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {"trigger": "hijacked", "steps": "hijacked"}},
        )
        factories.make_edge(self.automation, None, variables)

        context = build_execution_context({"customer": "Ada"})
        result = self._run(context)

        # Reachable as a variable, but the namespace every expression relies on is
        # still the namespace.
        self.assertEqual(result.context["vars"]["trigger"], "hijacked")
        self.assertEqual(result.context["trigger"]["customer"], "Ada")


class FilterTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.executor = AutomationExecutor()

    def _graph(self, value):
        check = factories.make_step(
                self.automation, key="check", step_type="filter", action_type="",
                config={"conditions": [
                        {"field": "amount", "operator": "gt", "value": 100},
                ]},
        )
        action = factories.make_step(self.automation, key="send", action_type="webhook")
        factories.make_edge(self.automation, None, check)
        factories.make_edge(self.automation, check, action)
        return check, action

    def _run(self, payload):
        return self.executor.execute_graph(
                self.automation.steps.all(),
                self.automation.edges.all(),
                build_execution_context(payload),
        )

    def test_a_false_filter_skips_the_rest_of_its_path(self):
        self._graph(100)

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run({"amount": 5})

        self.assertEqual(result.status, RESULT_SKIPPED)
        self.assertFalse(result.context["steps"]["check"]["output"]["passed"])
        self.assertEqual(result.context["steps"]["check"]["status"], "skipped")
        self.assertNotIn("send", result.context["steps"])
        request.assert_not_called()

    def test_a_true_filter_lets_the_path_continue(self):
        self._graph(100)

        with patch(WEBHOOK_REQUESTS) as request:
            result = self._run({"amount": 500})

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertTrue(result.context["steps"]["check"]["output"]["passed"])
        self.assertEqual(result.context["steps"]["send"]["status"], "success")
        request.assert_called_once()

    def test_the_decision_trace_is_recorded(self):
        # The filter is where a run most often "silently does nothing", so it has to
        # carry the same explanation an automation-level condition carries.
        self._graph(100)

        result = self._run({"amount": 5})

        trace = result.context["steps"]["check"]["decision"]["groups"][0][0]
        self.assertEqual(trace["field"], "amount")
        self.assertEqual(trace["actual"], 5)
        self.assertFalse(trace["result"])


class BranchTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)
        self.executor = AutomationExecutor()

    def _step(self, key, action_type, channel, message):
        return factories.make_step(
                self.automation, key=key, action_type=action_type, channel=channel,
                config={"to": "+905550000000", "message": message},
        )

    def _route(self, key="route", **config):
        return factories.make_step(
                self.automation, key=key, step_type="branch", action_type="", config=config,
        )

    def _run(self, payload):
        return self.executor.execute_graph(
                self.automation.steps.all(),
                self.automation.edges.all(),
                build_execution_context(payload),
        )

    def _branches(self):
        return [
                {"key": "vip", "conditions": [
                        {"field": "vip", "operator": "eq", "value": True},
                ]},
                {"key": "standard", "conditions": [
                        {"field": "vip", "operator": "eq", "value": False},
                ]},
        ]

    def _two_paths(self):
        whatsapp = self._step(
                "wa", "whatsapp",
                factories.make_channel(self.tenant, self.branch, channel_type="whatsapp",
                                       credentials={"access_token": "t", "phone_number_id": "1"}),
                "VIP musteri {{trigger.customer}}",
        )
        sms = self._step(
                "sms", "sms",
                factories.make_channel(self.tenant, self.branch, channel_type="sms",
                                       credentials=dict(factories.SMS_CREDENTIALS)),
                "Standart musteri {{trigger.customer}}",
        )
        return whatsapp, sms

    def test_the_matching_branch_runs_and_the_other_does_not(self):
        route = self._route(branches=self._branches())
        whatsapp,sms = self._two_paths()
        factories.make_edge(self.automation, None, route)
        factories.make_edge(self.automation, route, whatsapp, branch_key="vip")
        factories.make_edge(self.automation, route, sms, branch_key="standard")

        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send:
            result = self._run({"vip": True, "customer": "Ada"})

        self.assertEqual(result.status, RESULT_SUCCESS)
        self.assertEqual(result.context["steps"]["route"]["output"]["branch"], "vip")
        wa_send.assert_called_once()
        self.assertEqual(wa_send.call_args[0][1]["message"], "VIP musteri Ada")
        sms_send.assert_not_called()
        self.assertNotIn("sms", result.context["steps"])

    def test_the_second_branch_runs_when_the_first_does_not_match(self):
        route = self._route(branches=self._branches())
        whatsapp,sms = self._two_paths()
        factories.make_edge(self.automation, None, route)
        factories.make_edge(self.automation, route, whatsapp, branch_key="vip")
        factories.make_edge(self.automation, route, sms, branch_key="standard")

        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send:
            result = self._run({"vip": False, "customer": "Ada"})

        self.assertEqual(result.context["steps"]["route"]["output"]["branch"], "standard")
        wa_send.assert_not_called()
        sms_send.assert_called_once()
        self.assertEqual(sms_send.call_args[0][1]["message"], "Standart musteri Ada")

    def test_the_default_branch_runs_when_nothing_matches(self):
        route = self._route(branches=[
                {"key": "vip", "conditions": [
                        {"field": "vip", "operator": "eq", "value": True},
                ]},
        ])
        whatsapp,sms = self._two_paths()
        factories.make_edge(self.automation, None, route)
        factories.make_edge(self.automation, route, whatsapp, branch_key="vip")
        factories.make_edge(self.automation, route, sms, branch_key="default")

        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send:
            result = self._run({"vip": False, "customer": "Ada"})

        self.assertEqual(result.context["steps"]["route"]["output"]["branch"], "default")
        wa_send.assert_not_called()
        sms_send.assert_called_once()

    def test_the_simple_true_false_form(self):
        route = self._route(conditions=[{"field": "vip", "operator": "eq", "value": True}])
        whatsapp,sms = self._two_paths()
        factories.make_edge(self.automation, None, route)
        factories.make_edge(self.automation, route, whatsapp, branch_key="true", order=0)
        factories.make_edge(self.automation, route, sms, branch_key="false", order=1)

        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send:
            result = self._run({"vip": True, "customer": "Ada"})

        self.assertEqual(result.context["steps"]["route"]["output"]["branch"], "true")
        wa_send.assert_called_once()
        sms_send.assert_not_called()


class WaitStepTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.executor = AutomationExecutor()

    def _wait(self, key="wait", **config):
        return factories.make_step(
                self.automation, key=key, step_type="wait", action_type="", config=config,
        )

    def test_a_duration_wait_pauses_and_schedules_a_continuation(self):
        wait = self._wait(duration="24h")
        after = factories.make_step(self.automation, key="after", action_type="webhook")
        factories.make_edge(self.automation, None, wait)
        factories.make_edge(self.automation, wait, after)

        schedule = MagicMock()

        with patch(WEBHOOK_REQUESTS) as request:
            result = self.executor.execute_graph(
                    self.automation.steps.all(),
                    self.automation.edges.all(),
                    build_execution_context({"customer": "Ada"}),
                    schedule_continuation = schedule,
            )

        self.assertEqual(result.status, RESULT_WAITING)
        self.assertEqual(result.resume_from_step_key, "after")
        self.assertEqual(result.context["steps"]["wait"]["output"]["delay_seconds"], 86400)
        self.assertIsNotNone(result.waiting_until)

        # The step after the wait did not run now, and it does not run twice.
        self.assertNotIn("after", result.context["steps"])
        request.assert_not_called()

        schedule.assert_called_once()
        next_step_key,continuation_context = schedule.call_args[0]
        self.assertEqual(next_step_key, "after")
        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 86400)
        # The continuation carries the run, not just a pointer to it.
        self.assertEqual(continuation_context["steps"]["wait"]["status"], "waiting")
        json.dumps(continuation_context)  # CELERY_TASK_SERIALIZER is 'json'

    def test_an_until_wait_records_the_absolute_time(self):
        wait = self._wait(until="{{trigger.appointment_time}}")
        factories.make_edge(self.automation, None, wait)

        result = self.executor.execute_graph(
                self.automation.steps.all(),
                self.automation.edges.all(),
                build_execution_context({"appointment_time": "2030-01-01T09:00:00+00:00"}),
        )

        self.assertEqual(result.status, RESULT_WAITING)
        self.assertEqual(
                result.context["steps"]["wait"]["output"]["until"],
                "2030-01-01T09:00:00+00:00",
        )
        self.assertGreater(result.context["steps"]["wait"]["output"]["delay_seconds"], 0)

    def test_a_resumed_run_starts_at_the_given_step_with_the_old_context(self):
        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {"greeting": "Merhaba {{trigger.customer}}"}},
        )
        wait = self._wait(duration="24h")
        after = factories.make_step(
                self.automation, key="after", action_type="webhook",
                config={
                        **webhook_config("https://example.com/hook"),
                        "payload": {"text": "{{vars.greeting}}"},
                },
        )
        factories.make_edge(self.automation, None, variables)
        factories.make_edge(self.automation, variables, wait)
        factories.make_edge(self.automation, wait, after)

        context = build_execution_context({"customer": "Ada"})

        paused = self.executor.execute_graph(
                self.automation.steps.all(), self.automation.edges.all(), context
        )
        self.assertEqual(paused.status, RESULT_WAITING)

        with patch(WEBHOOK_REQUESTS) as request:
            resumed = self.executor.execute_graph(
                    self.automation.steps.all(),
                    self.automation.edges.all(),
                    paused.context,
                    start_step_key = paused.resume_from_step_key,
            )

        self.assertEqual(resumed.status, RESULT_SUCCESS)
        self.assertEqual(request.call_args.kwargs["json"], {"text": "Merhaba Ada"})
        # Nothing re-ran: the pause and the variables are still there.
        self.assertEqual(resumed.context["steps"]["wait"]["status"], "waiting")
        self.assertEqual(resumed.context["vars"]["greeting"], "Merhaba Ada")


class GraphOrchestrationTests(TestCase):
    """The orchestrator's choice between the legacy list and the graph."""

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)
        self.orchestrator = AutomationOrchestrator()

    def test_a_plain_action_chain_still_uses_the_legacy_executor(self):
        # The graph the actions API derives from an `actions` payload is a linear
        # chain of action steps. Routing it through the graph would be a behaviour
        # change for every existing automation, for no gain (ROADMAP section 5.1).
        factories.make_action(self.automation, order=1)
        sync_steps_from_actions(self.automation)

        with patch.object(AutomationExecutor, "execute_actions", return_value=True) as legacy, \
                        patch.object(AutomationExecutor, "execute_graph") as graph:
            result = self.orchestrator.run(str(self.automation.id), {"any": "payload"})

        self.assertTrue(result)
        legacy.assert_called_once()
        graph.assert_not_called()

    def test_a_graph_with_a_control_flow_step_takes_the_graph_path(self):
        factories.make_step(
                self.automation, key="wait", step_type="wait", action_type="",
                config={"duration": "1h"},
        )

        executed = GraphExecutionResult(
                status = RESULT_SUCCESS, context = {"steps": {}, "vars": {}}, step_results = [],
        )

        with patch.object(AutomationExecutor, "execute_graph", return_value=executed) as graph:
            result = self.orchestrator.run(
                    str(self.automation.id), {}, schedule_continuation = MagicMock(),
            )

        self.assertTrue(result)
        graph.assert_called_once()

    def test_a_wait_step_logs_the_run_as_pending(self):
        wait = factories.make_step(
                self.automation, key="wait", step_type="wait", action_type="",
                config={"duration": "1h"},
        )
        factories.make_edge(self.automation, None, wait)

        result = self.orchestrator.run(
                str(self.automation.id), {}, schedule_continuation = MagicMock(),
        )

        self.assertTrue(result)
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.PENDING)
        self.assertEqual(log.execution_context["steps"]["wait"]["status"], "waiting")

    def test_a_failed_step_is_logged_as_a_failure(self):
        broken = factories.make_step(
                self.automation, key="1", action_type="carrier_pigeon",
        )
        factories.make_edge(self.automation, None, broken)

        result = self.orchestrator.run(str(self.automation.id), {})

        self.assertFalse(result)
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.FAILURE)
        self.assertIn("Unknown action type", log.error_message)


class RoadmapExitCriterionTests(TestCase):
    """
    ROADMAP section 7, Faz 1 exit criterion, as one automation and two runs.

    "VIP ise WhatsApp, degilse SMS; 24 saat bekle; yanit yoksa yoneticiye e-posta
    bildir." Before Faz 1.D this needed three automations and a scheduled trigger
    for the delay, and the reply check could not exist at all.
    """

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)

        self.whatsapp_channel = factories.make_channel(
                self.tenant, self.branch, channel_type="whatsapp",
                credentials={"access_token": "token", "phone_number_id": "10"},
        )
        self.sms_channel = factories.make_channel(
                self.tenant, self.branch, channel_type="sms",
                credentials=dict(factories.SMS_CREDENTIALS),
        )
        self.email_channel = factories.make_channel(self.tenant, self.branch)

        self.customer_template = factories.make_template(
                self.tenant, raw_text="Merhaba {{trigger.customer}}, randevunuz yarin.",
        )
        self.manager_template = factories.make_template(
                self.tenant, raw_text="{{trigger.customer}} yanit vermedi.",
        )

        self._build_graph()

    def _build_graph(self):
        route = factories.make_step(
                self.automation, key="route", step_type="branch", action_type="",
                config={"branches": [
                        {"key": "vip", "conditions": [
                                {"field": "vip", "operator": "eq", "value": True},
                        ]},
                        {"key": "standard", "conditions": [
                                {"field": "vip", "operator": "eq", "value": False},
                        ]},
                ]},
        )
        whatsapp = factories.make_step(
                self.automation, key="send_whatsapp", action_type="whatsapp",
                channel=self.whatsapp_channel, template=self.customer_template,
                config={"to": "{{trigger.phone}}"},
        )
        sms = factories.make_step(
                self.automation, key="send_sms", action_type="sms",
                channel=self.sms_channel, template=self.customer_template,
                config={"to": "{{trigger.phone}}"},
        )
        wait = factories.make_step(
                self.automation, key="wait_24h", step_type="wait", action_type="",
                config={"duration": "24h"},
        )
        check = factories.make_step(
                self.automation, key="check_replied", step_type="filter", action_type="",
                config={"conditions": [
                        {"field": "replied", "operator": "eq", "value": False},
                ]},
        )
        notify = factories.make_step(
                self.automation, key="notify_manager", action_type="email",
                channel=self.email_channel, template=self.manager_template,
                config={"to": "{{trigger.manager_email}}", "subject": "Yanit yok"},
        )

        factories.make_edge(self.automation, None, route)
        factories.make_edge(self.automation, route, whatsapp, branch_key="vip", order=0)
        factories.make_edge(self.automation, route, sms, branch_key="standard", order=1)
        factories.make_edge(self.automation, whatsapp, wait)
        factories.make_edge(self.automation, sms, wait)
        factories.make_edge(self.automation, wait, check)
        factories.make_edge(self.automation, check, notify)

    def _payload(self, vip):
        return {
                "customer": "Ada",
                "phone": "+905550000001",
                "manager_email": "manager@example.com",
                "vip": vip,
                "replied": False,
        }

    def test_the_vip_path_pauses_at_the_wait_and_the_continuation_notifies_the_manager(self):
        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send, \
                        patch.object(EmailProvider, "send", return_value=True) as email_send, \
                        patch.object(run_automation, "apply_async") as apply_async:

            first = run_automation(str(self.automation.id), self._payload(vip=True))

            self.assertTrue(first)
            wa_send.assert_called_once()
            sms_send.assert_not_called()
            email_send.assert_not_called()

            apply_async.assert_called_once()
            args = apply_async.call_args.kwargs["args"]
            kwargs = apply_async.call_args.kwargs["kwargs"]

            self.assertEqual(kwargs["start_step_key"], "check_replied")
            self.assertIn("eta", apply_async.call_args.kwargs)

            paused_context = args[1]
            json.dumps(paused_context)  # must survive CELERY_TASK_SERIALIZER='json'
            self.assertEqual(paused_context["steps"]["send_whatsapp"]["status"], "success")
            self.assertEqual(paused_context["steps"]["wait_24h"]["status"], "waiting")
            self.assertNotIn("check_replied", paused_context["steps"])

            paused_log = AutomationLog.objects.get(automation=self.automation)
            self.assertEqual(paused_log.status, AutomationStatus.PENDING)

            # 24 hours later: the continuation the wait step scheduled.
            second = run_automation(
                    str(self.automation.id), paused_context,
                    start_step_key = kwargs["start_step_key"],
            )

        self.assertTrue(second)
        email_send.assert_called_once()
        email_payload = email_send.call_args[0][1]
        self.assertEqual(email_payload["to"], "manager@example.com")
        self.assertEqual(email_payload["body"], "Ada yanit vermedi.")
        # The WhatsApp message was not sent twice.
        self.assertEqual(wa_send.call_count, 1)

        finished = AutomationLog.objects.get(
                automation = self.automation, status = AutomationStatus.SUCCESS,
        )
        # The filter asked "yanit yok mu?" (replied == False) and the answer was yes,
        # so the path continued to the manager notification.
        self.assertTrue(finished.execution_context["steps"]["check_replied"]["output"]["passed"])
        self.assertEqual(
                finished.execution_context["steps"]["notify_manager"]["status"], "success",
        )
        # The first run's records survived into the second.
        self.assertEqual(finished.execution_context["steps"]["wait_24h"]["status"], "waiting")

    def test_the_standard_path_sends_sms_instead(self):
        with patch.object(WhatsAppProvider, "send", return_value=True) as wa_send, \
                        patch.object(SMSProvider, "send", return_value=True) as sms_send, \
                        patch.object(EmailProvider, "send", return_value=True), \
                        patch.object(run_automation, "apply_async"):

            run_automation(str(self.automation.id), self._payload(vip=False))

        sms_send.assert_called_once()
        wa_send.assert_not_called()
