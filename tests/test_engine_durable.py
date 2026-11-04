"""
Faz 2.A: AutomationRun, AutomationStepRun, and step memoization.

ROADMAP section 5.2 asks for one durable row per run and one per step, and section 7
gives the exit criterion of the phase: when a worker dies in the middle of a run, the
retry finishes it **without executing the steps that already succeeded**. Step
memoization is what makes that true - a `StepRun` whose status is `success` is never
run again, its stored output is injected into the context and the walk continues past
it.

`AutomationLog` is deliberately not replaced: it is the summary the API reads, and
every test that predates this module still calls it. The tests below therefore assert
both - the new rows and the old log - in the same run.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.automations.engine.executor import AutomationExecutor
from apps.automations.engine.orchestrator import AutomationOrchestrator
from apps.automations.models import (
    AutomationLog,
    AutomationRun,
    AutomationStatus,
    RunMode,
    RunStatus,
    StepRunStatus,
    sync_steps_from_actions,
)
from apps.automations.tasks import run_automation
from tests import factories


WEBHOOK_REQUESTS = "apps.automations.engine.actions.send_webhook.requests.request"


def webhook_config(url, payload=None):
    return {"url": url, "method": "POST", "headers": {}, "payload": payload or {}}


class WebhookRecorder:
    """
    A stand-in for `requests.request` that remembers which url was called.

    A bare MagicMock cannot answer "was step 1 called again on the second pass", which
    is the whole point of the memoization test. `failures` lets one url crash a given
    number of times, which is how a worker dying mid-run is simulated without killing
    the test process.
    """

    def __init__(self, fail_url=None, failures=0):
        self.fail_url = fail_url
        self.failures_left = failures
        self.calls = []

    def __call__(self, **kwargs):
        url = kwargs.get("url")

        if url == self.fail_url and self.failures_left > 0:
            self.failures_left -= 1
            raise Exception("the worker died here")

        self.calls.append({"url": url, "json": kwargs.get("json")})
        return MagicMock()

    def urls(self):
        return [call["url"] for call in self.calls]

    def calls_to(self, url):
        return [call for call in self.calls if call["url"] == url]


class DurableTestCase(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.orchestrator = AutomationOrchestrator()

    def _runs(self):
        return AutomationRun.objects.filter(automation=self.automation)

    def _step_runs(self, run):
        return {step_run.step_key: step_run for step_run in run.step_runs.all()}


class RunLifecycleTests(DurableTestCase):
    """One successful graph run, described row by row."""

    def setUp(self):
        super().setUp()
        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {"greeting": "Merhaba {{trigger.customer}}"}},
        )
        send = factories.make_step(
                self.automation, key="send", action_type="webhook",
                config=webhook_config("https://example.com/hook", {"text": "{{vars.greeting}}"}),
        )
        factories.make_edge(self.automation, None, variables)
        factories.make_edge(self.automation, variables, send)

    def test_a_finished_run_records_its_times_status_and_context(self):
        with patch(WEBHOOK_REQUESTS) as request:
            self.assertTrue(self.orchestrator.run(str(self.automation.id), {"customer": "Ada"}))

        request.assert_called_once()

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.mode, RunMode.LIVE)
        self.assertEqual(run.trigger_payload, {"customer": "Ada"})
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        self.assertEqual(run.error_message, "")
        # The context the run ended with is kept, so a reader can answer "why did
        # this customer get this text".
        self.assertEqual(run.context["steps"]["send"]["output"]["prepared"]["payload"], {
                "text": "Merhaba Ada",
        })

        # The old summary is still written - nothing reads AutomationRun yet.
        log = AutomationLog.objects.get(automation=self.automation)
        self.assertEqual(log.status, AutomationStatus.SUCCESS)

    def test_every_executed_step_gets_a_row_with_input_output_and_duration(self):
        with patch(WEBHOOK_REQUESTS):
            self.orchestrator.run(str(self.automation.id), {"customer": "Ada"})

        step_runs = self._step_runs(self._runs().get())

        self.assertEqual(set(step_runs), {"vars", "send"})

        variables = step_runs["vars"]
        self.assertEqual(variables.step_type, "set_variable")
        self.assertEqual(variables.status, StepRunStatus.SUCCESS)
        self.assertEqual(variables.attempt, 1)
        self.assertEqual(variables.input, {"variables": {"greeting": "Merhaba {{trigger.customer}}"}})
        self.assertEqual(variables.output, {"greeting": "Merhaba Ada"})

        send = step_runs["send"]
        self.assertEqual(send.step_type, "action")
        self.assertEqual(send.status, StepRunStatus.SUCCESS)
        self.assertEqual(send.output["message"], None)
        self.assertEqual(send.output["prepared"]["payload"], {"text": "Merhaba Ada"})

        # Both rows carry a real duration; nothing here is left as a placeholder.
        for step_run in step_runs.values():
            self.assertIsNotNone(step_run.duration_ms)
            self.assertGreaterEqual(step_run.duration_ms, 0)
            self.assertEqual(step_run.error, "")
            # The row points back at the canvas node while it still exists.
            self.assertIsNotNone(step_run.step_id)

    def test_a_test_mode_run_is_recorded_as_such(self):
        with patch(WEBHOOK_REQUESTS):
            self.orchestrator.run(str(self.automation.id), {}, mode="test")

        self.assertEqual(self._runs().get().mode, RunMode.TEST)

    def test_an_unknown_run_id_starts_a_new_run_instead_of_crashing(self):
        # A continuation whose row was deleted (or a malformed id from a client) is a
        # cache miss, not an error: the automation still has to run.
        with patch(WEBHOOK_REQUESTS):
            self.assertTrue(self.orchestrator.run(
                    str(self.automation.id), {"customer": "Ada"}, run_id="not-a-uuid",
            ))

        self.assertEqual(self._runs().count(), 1)


class SkippedRunTests(DurableTestCase):

    def test_a_condition_that_does_not_match_closes_the_run_as_skipped(self):
        factories.make_condition(self.automation, field="amount", operator="gt", value=100)
        step = factories.make_step(self.automation, key="1", action_type="webhook")
        factories.make_edge(self.automation, None, step)

        with patch(WEBHOOK_REQUESTS) as request:
            self.assertFalse(self.orchestrator.run(str(self.automation.id), {"amount": 5}))

        request.assert_not_called()

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.SKIPPED)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.decision["result"], False)
        self.assertEqual(AutomationLog.objects.get(automation=self.automation).status,
                         AutomationStatus.SKIPPED)


class CrashRecoveryTests(DurableTestCase):
    """
    ROADMAP section 7, Faz 2 exit criterion, as one automation and two attempts.

    Three action steps in a row. The first attempt gets through step 1 and step 2 and
    dies on step 3. The recovery is handed the same `run_id`, so step 1 and step 2 must
    not be executed again - the provider must see zero calls for them - while step 3,
    which reads their output through `{{steps.2.output.message}}`, finishes the run.
    """

    def setUp(self):
        super().setUp()

        template = factories.make_template(self.tenant, raw_text="Merhaba {{customer}}")

        first = factories.make_step(
                self.automation, key="1", action_type="webhook", template=template,
                config=webhook_config("https://example.com/1"),
        )
        second = factories.make_step(
                self.automation, key="2", action_type="webhook", template=template,
                config=webhook_config("https://example.com/2"),
        )
        third = factories.make_step(
                self.automation, key="3", action_type="webhook",
                config=webhook_config("https://example.com/3", {
                        "echo": "{{steps.2.output.message}}",
                }),
        )
        factories.make_edge(self.automation, None, first)
        factories.make_edge(self.automation, first, second)
        factories.make_edge(self.automation, second, third)

    def _crash(self):
        recorder = WebhookRecorder(fail_url="https://example.com/3", failures=1)

        with patch(WEBHOOK_REQUESTS, side_effect=recorder):
            result = self.orchestrator.run(str(self.automation.id), {"customer": "Ada"})

        return result, recorder

    def test_the_first_attempt_dies_with_the_finished_steps_persisted(self):
        result, recorder = self._crash()

        self.assertFalse(result)
        self.assertEqual(recorder.urls(), [
                "https://example.com/1",
                "https://example.com/2",
        ])

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIsNotNone(run.finished_at)

        step_runs = self._step_runs(run)
        self.assertEqual(step_runs["1"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["2"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["3"].status, StepRunStatus.FAILED)
        self.assertIn("An error occured while sending a webhook", step_runs["3"].error)

    def test_recovery_skips_the_succeeded_steps_and_finishes_the_run(self):
        _result, crash_recorder = self._crash()
        run = self._runs().get()

        recorder = WebhookRecorder()

        with patch(WEBHOOK_REQUESTS, side_effect=recorder):
            recovered = self.orchestrator.run(
                    str(self.automation.id), {"customer": "Ada"}, run_id=str(run.id),
            )

        self.assertTrue(recovered)

        # The provider was called once on the recovery: step 3, and nothing else.
        self.assertEqual(recorder.urls(), ["https://example.com/3"])
        self.assertEqual(recorder.calls_to("https://example.com/1"), [])
        self.assertEqual(recorder.calls_to("https://example.com/2"), [])
        self.assertEqual(crash_recorder.calls_to("https://example.com/3"), [])

        # Step 3 read what step 2 produced on the FIRST attempt.
        self.assertEqual(
                recorder.calls_to("https://example.com/3")[0]["json"],
                {"echo": "Merhaba Ada"},
        )

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertIsNotNone(run.finished_at)

        # The recovery resumed the run; it did not open a second history.
        self.assertEqual(self._runs().count(), 1)
        self.assertEqual(run.step_runs.count(), 3)

        step_runs = self._step_runs(run)
        self.assertEqual(step_runs["3"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["3"].output["prepared"]["payload"], {"echo": "Merhaba Ada"})

        # The memoized outputs were injected into the context, not just skipped.
        self.assertEqual(run.context["steps"]["1"]["output"]["message"], "Merhaba Ada")
        self.assertEqual(run.context["steps"]["2"]["output"]["message"], "Merhaba Ada")
        self.assertEqual(run.context["steps"]["3"]["status"], "success")

    def test_a_memoized_step_is_reported_as_memoized(self):
        result = AutomationExecutor().execute_graph(
                self.automation.steps.all(),
                self.automation.edges.all(),
                {},
                completed_steps={
                        "1": {"status": "success", "output": {"message": "stored"}},
                        "2": {"status": "success", "output": {"message": "stored too"}},
                        "3": {"status": "success", "output": {"message": "stored as well"}},
                },
                # No provider is patched here at all: a live step would blow up.
        )

        by_key = {step_result["key"]: step_result for step_result in result.step_results}

        self.assertEqual(set(by_key), {"1", "2", "3"})

        for key in ("1", "2", "3"):
                self.assertTrue(by_key[key]["memoized"])
                self.assertEqual(by_key[key]["status"], "success")

        # The stored outputs are in the context, which is what the next step reads.
        self.assertEqual(result.context["steps"]["3"]["output"], {"message": "stored as well"})
        json.dumps(result.context)


class MemoizedControlFlowTests(DurableTestCase):
    """
    A `set_variable` and a `branch` are memoized like any other step.

    Both matter for a different reason than an action does: their *output* is state the
    rest of the graph reads. If the resumed pass re-evaluated the branch, a payload
    that changed in between would send the run down a different path; if it dropped the
    variables, every later expression would resolve to nothing.
    """

    def setUp(self):
        super().setUp()

        variables = factories.make_step(
                self.automation, key="vars", step_type="set_variable", action_type="",
                config={"variables": {"greeting": "Merhaba {{trigger.customer}}"}},
        )
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
        vip = factories.make_step(
                self.automation, key="vip_send", action_type="webhook",
                config=webhook_config("https://example.com/vip", {"text": "{{vars.greeting}}"}),
        )
        standard = factories.make_step(
                self.automation, key="standard_send", action_type="webhook",
                config=webhook_config("https://example.com/standard", {"text": "{{vars.greeting}}"}),
        )

        factories.make_edge(self.automation, None, variables)
        factories.make_edge(self.automation, variables, route)
        factories.make_edge(self.automation, route, vip, branch_key="vip")
        factories.make_edge(self.automation, route, standard, branch_key="standard")

    def test_recovery_keeps_the_original_branch_and_the_original_variables(self):
        crashing = WebhookRecorder(fail_url="https://example.com/vip", failures=1)

        with patch(WEBHOOK_REQUESTS, side_effect=crashing):
            self.assertFalse(self.orchestrator.run(
                    str(self.automation.id), {"customer": "Ada", "vip": True},
            ))

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.FAILED)

        step_runs = self._step_runs(run)
        self.assertEqual(step_runs["vars"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["route"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["route"].output, {"branch": "vip"})
        self.assertEqual(step_runs["vip_send"].status, StepRunStatus.FAILED)

        recorder = WebhookRecorder()

        with patch(WEBHOOK_REQUESTS, side_effect=recorder):
            # Different customer, different vip flag: re-evaluating either step would
            # take the standard path and greet Bob.
            self.assertTrue(self.orchestrator.run(
                    str(self.automation.id), {"customer": "Bob", "vip": False},
                    run_id=str(run.id),
            ))

        self.assertEqual(recorder.urls(), ["https://example.com/vip"])
        self.assertEqual(
                recorder.calls[0]["json"],
                {"text": "Merhaba Ada"},
        )

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.context["vars"]["greeting"], "Merhaba Ada")
        self.assertEqual(run.context["steps"]["route"]["output"], {"branch": "vip"})
        self.assertFalse(AutomationRun.objects.filter(
                automation=self.automation, step_runs__step_key="standard_send",
        ).exists())


class WaitContinuationTests(DurableTestCase):
    """A `wait` step parks the run; its continuation must come back as the same run."""

    def setUp(self):
        super().setUp()

        template = factories.make_template(self.tenant, raw_text="Merhaba {{customer}}")

        first = factories.make_step(
                self.automation, key="send1", action_type="webhook", template=template,
                config=webhook_config("https://example.com/1"),
        )
        wait = factories.make_step(
                self.automation, key="wait", step_type="wait", action_type="",
                config={"duration": "1h"},
        )
        second = factories.make_step(
                self.automation, key="send2", action_type="webhook",
                config=webhook_config("https://example.com/2", {
                        "text": "{{steps.send1.output.message}}",
                }),
        )
        factories.make_edge(self.automation, None, first)
        factories.make_edge(self.automation, first, wait)
        factories.make_edge(self.automation, wait, second)

    def test_a_paused_run_is_waiting_and_its_continuation_carries_the_run_id(self):
        recorder = WebhookRecorder()

        with patch(WEBHOOK_REQUESTS, side_effect=recorder), \
                        patch.object(run_automation, "apply_async") as apply_async:
            self.assertTrue(run_automation(str(self.automation.id), {"customer": "Ada"}))

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.WAITING)
        self.assertIsNone(run.finished_at)
        self.assertIsNotNone(run.started_at)
        self.assertEqual(AutomationLog.objects.get(automation=self.automation).status,
                         AutomationStatus.PENDING)

        step_runs = self._step_runs(run)
        self.assertEqual(step_runs["send1"].status, StepRunStatus.SUCCESS)
        self.assertEqual(step_runs["wait"].status, StepRunStatus.WAITING)
        self.assertNotIn("send2", step_runs)

        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs["kwargs"]

        self.assertEqual(kwargs["start_step_key"], "send2")
        # The continuation resumes THIS run, not a fresh one.
        self.assertEqual(kwargs["run_id"], str(run.id))

    def test_the_continuation_finishes_the_same_run_without_re_sending_step_one(self):
        recorder = WebhookRecorder()

        with patch(WEBHOOK_REQUESTS, side_effect=recorder), \
                        patch.object(run_automation, "apply_async") as apply_async:
            run_automation(str(self.automation.id), {"customer": "Ada"})

            paused_context = apply_async.call_args.kwargs["args"][1]
            kwargs = apply_async.call_args.kwargs["kwargs"]

            self.assertTrue(run_automation(
                    str(self.automation.id), paused_context,
                    start_step_key = kwargs["start_step_key"],
                    run_id = kwargs["run_id"],
            ))

        self.assertEqual(len(recorder.calls_to("https://example.com/1")), 1)
        self.assertEqual(
                recorder.calls_to("https://example.com/2")[0]["json"],
                {"text": "Merhaba Ada"},
        )

        self.assertEqual(self._runs().count(), 1)

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.context["steps"]["send2"]["status"], "success")


class AttemptTrackingTests(DurableTestCase):
    """`attempt` and `duration_ms` are measurements, not decoration."""

    def test_a_step_that_needed_three_tries_records_three(self):
        step = factories.make_step(
                self.automation, key="1", action_type="webhook", on_failure="retry",
                config=webhook_config("https://example.com/retry"),
        )
        factories.make_edge(self.automation, None, step)

        recorder = WebhookRecorder(fail_url="https://example.com/retry", failures=2)

        with patch(WEBHOOK_REQUESTS, side_effect=recorder):
            self.assertTrue(self.orchestrator.run(str(self.automation.id), {}))

        step_run = self._step_runs(self._runs().get())["1"]
        self.assertEqual(step_run.status, StepRunStatus.SUCCESS)
        self.assertEqual(step_run.attempt, 3)
        self.assertEqual(step_run.error, "")
        self.assertIsNotNone(step_run.duration_ms)
        self.assertIsNotNone(step_run.output)

    def test_a_step_that_exhausts_its_retries_is_persisted_before_the_run_fails(self):
        step = factories.make_step(
                self.automation, key="1", action_type="webhook", on_failure="retry",
                config=webhook_config("https://example.com/never"),
        )
        factories.make_edge(self.automation, None, step)

        recorder = WebhookRecorder(fail_url="https://example.com/never", failures=99)

        with patch(WEBHOOK_REQUESTS, side_effect=recorder):
            self.assertFalse(self.orchestrator.run(str(self.automation.id), {}))

        # All three attempts were made and the provider was asked three times.
        self.assertEqual(len(recorder.calls_to("https://example.com/never")), 0)
        self.assertEqual(recorder.failures_left, 96)

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("An error occured while sending a webhook", run.error_message)

        # The failed step run exists even though the walk raised: an operator reading
        # the history must see which step burned the retries.
        step_run = self._step_runs(run)["1"]
        self.assertEqual(step_run.status, StepRunStatus.FAILED)
        self.assertEqual(step_run.attempt, 3)
        self.assertIn("An error occured while sending a webhook", step_run.error)
        self.assertIsNotNone(step_run.duration_ms)


class LegacyActionRunTests(DurableTestCase):
    """The actions API keeps its executor, and now also gets run history."""

    def test_a_legacy_action_list_is_recorded_step_by_step(self):
        for order in (1, 2):
                factories.make_action(
                        self.automation, order=order, action_type="webhook",
                        config=webhook_config("https://example.com/%s" % order),
                )
        sync_steps_from_actions(self.automation)

        with patch(WEBHOOK_REQUESTS) as request:
            self.assertTrue(self.orchestrator.run(str(self.automation.id), {}))

        self.assertEqual(request.call_count, 2)

        run = self._runs().get()
        self.assertEqual(run.status, RunStatus.SUCCESS)

        step_runs = self._step_runs(run)
        self.assertEqual(sorted(step_runs), ["1", "2"])

        for step_run in step_runs.values():
                self.assertEqual(step_run.step_type, "action")
                self.assertEqual(step_run.status, StepRunStatus.SUCCESS)
                self.assertEqual(step_run.attempt, 1)
                self.assertIsNotNone(step_run.duration_ms)
                self.assertEqual(step_run.input["url"].startswith("https://example.com/"), True)
