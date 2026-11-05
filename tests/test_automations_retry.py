"""
Faz 2.D.2: queue-level retry of a failed LIVE run.

A failed attempt is re-dispatched on the SAME `AutomationRun` with exponential
backoff and jitter. The identity is what makes the retry safe: a run that already
sent message 1 must not send it again just because message 2 failed, so the retry
resumes the row and the executor memoizes the successful steps.

These tests are service-free on purpose: `apply_async` is mocked and the provider
call is a recorder, so a retry is observed as a dispatch and then replayed by hand.
Nothing here needs Redis, a worker or a real HTTP endpoint.
"""
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.automations.engine.executor import AutomationExecutor, GraphExecutionResult
from apps.automations.engine.exceptions import ExecutionError
from apps.automations.models import (
        AutomationRun,
        AutomationTrigger,
        RunMode,
        RunStatus,
        StepRunStatus,
)
from apps.automations.tasks import retry_countdown, run_automation
from tests import factories


WEBHOOK_REQUESTS = "apps.automations.engine.actions.send_webhook.requests.request"
NOTIFY = "apps.automations.runtime.notify_automation_completed"


def webhook_config(url,payload=None):
        return {"url": url,"method": "POST","headers": {},"payload": payload or {}}


class WebhookRecorder:
        """
        A stand-in for `requests.request` that can fail one url a fixed number of times.

        `failures` is how "the provider was down for the first attempt" is simulated
        without a real network; `calls` is what proves a memoized step was not sent a
        second time.
        """

        def __init__(self,fail_url = None,failures = 0):
                self.fail_url = fail_url
                self.failures_left = failures
                self.calls = []

        def __call__(self,**kwargs):
                url = kwargs.get('url')

                if url == self.fail_url and self.failures_left > 0:
                        self.failures_left -= 1
                        raise OSError("provider is down")

                self.calls.append({"url": url,"json": kwargs.get('json')})
                return MagicMock()

        def calls_to(self,url):
                return [call for call in self.calls if call["url"] == url]


def retry_dispatch(apply_async,index = -1):
        """
        The `(args, kwargs)` a mocked `apply_async` was asked to queue.
        """
        call = apply_async.call_args_list[index]
        return call.kwargs['args'],call.kwargs['kwargs']


class BackoffTests(TestCase):
        """The countdown helper is pure, so its shape can be pinned exactly."""

        def test_the_delay_grows_exponentially_from_the_base(self):
                self.assertEqual(
                        retry_countdown(0,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        30.0,
                )
                self.assertEqual(
                        retry_countdown(1,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        60.0,
                )
                self.assertEqual(
                        retry_countdown(2,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        120.0,
                )

        def test_the_delay_is_capped_at_the_configured_maximum(self):
                self.assertEqual(
                        retry_countdown(7,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        3600.0,
                )
                self.assertEqual(
                        retry_countdown(50,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        3600.0,
                )

        def test_a_negative_or_garbage_attempt_does_not_rewind_the_delay(self):
                self.assertEqual(
                        retry_countdown(-5,base_seconds = 30,max_seconds = 3600,jitter = 0),
                        30.0,
                )
                self.assertEqual(
                        retry_countdown("nonsense",base_seconds = 30,max_seconds = 3600,jitter = 0),
                        30.0,
                )

        def test_jitter_stays_inside_the_fraction_and_never_exceeds_the_cap(self):
                with patch("apps.automations.tasks.random.uniform",return_value = 0.25):
                        self.assertEqual(
                                retry_countdown(1,base_seconds = 30,max_seconds = 3600,
                                                jitter = 0.25),
                                75.0,
                        )

                with patch("apps.automations.tasks.random.uniform",return_value = -0.25):
                        self.assertEqual(
                                retry_countdown(1,base_seconds = 30,max_seconds = 3600,
                                                jitter = 0.25),
                                45.0,
                        )

                # A positive jitter on an already-capped delay must be clamped back to
                # the cap, not multiplied past it.
                with patch("apps.automations.tasks.random.uniform",return_value = 0.25):
                        self.assertEqual(
                                retry_countdown(10,base_seconds = 30,max_seconds = 3600,
                                                jitter = 0.25),
                                3600.0,
                        )

                # The lower clamp matters for an extreme jitter: a negative countdown
                # is not a schedule, it is a bug.
                with patch("apps.automations.tasks.random.uniform",return_value = -1.0):
                        self.assertEqual(
                                retry_countdown(0,base_seconds = 30,max_seconds = 3600,
                                                jitter = 2.0),
                                0.0,
                        )


class RunIdentityTests(TestCase):
        """One AutomationRun, named before it exists, reused by every retry."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                step = factories.make_step(self.automation,key = "hook",action_type = "webhook")
                factories.make_edge(self.automation,None,step)

        def test_the_first_attempt_creates_the_generated_id_and_the_retry_reuses_it(self):
                recorder = WebhookRecorder(
                        fail_url = "https://example.com/hook",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertFalse(run_automation(str(self.automation.id),{}))
                        args,kwargs = retry_dispatch(apply_async)

                run = AutomationRun.objects.get(automation = self.automation)
                self.assertEqual(kwargs['run_id'],str(run.id))
                self.assertEqual(run.status,RunStatus.QUEUED)
                self.assertIsNone(run.finished_at)
                self.assertEqual(AutomationRun.objects.count(),1)

                with patch(WEBHOOK_REQUESTS,side_effect = WebhookRecorder()), \
                                patch.object(run_automation,"apply_async") as retry_async:
                        self.assertTrue(run_automation(*args,**kwargs))

                retry_async.assert_not_called()
                self.assertEqual(AutomationRun.objects.count(),1)

                run.refresh_from_db()
                self.assertEqual(run.status,RunStatus.SUCCESS)
                self.assertIsNotNone(run.finished_at)

        def test_an_unknown_explicit_run_id_still_opens_a_fresh_run(self):
                unknown = str(uuid.uuid4())

                with patch(WEBHOOK_REQUESTS):
                        self.assertTrue(run_automation(
                                str(self.automation.id),{},run_id = unknown,
                        ))

                run = AutomationRun.objects.get()
                self.assertNotEqual(str(run.id),unknown)
                self.assertEqual(run.status,RunStatus.SUCCESS)

        def test_a_malformed_explicit_run_id_is_not_a_crash(self):
                with patch(WEBHOOK_REQUESTS):
                        self.assertTrue(run_automation(
                                str(self.automation.id),{},run_id = "not-a-uuid",
                        ))

                self.assertEqual(AutomationRun.objects.count(),1)


class CrossAutomationScopeTests(TestCase):
        """A run id is not a capability: every lookup and write is automation-scoped."""

        def setUp(self):
                self.tenant_a = factories.make_tenant(name = "Tenant A")
                self.automation_a = factories.make_automation(self.tenant_a,name = "A")
                self.run_a = factories.make_run(
                        self.automation_a,status = RunStatus.FAILED,mode = RunMode.LIVE,
                        trigger_payload = {"secret": "A"},
                        context = {"steps": {"a": {"status": "failed"}}},
                        decision = {"result": False},
                        error_message = "A failed",
                )

                self.tenant_b = factories.make_tenant(name = "Tenant B")
                self.automation_b = factories.make_automation(self.tenant_b,name = "B")
                step = factories.make_step(
                        self.automation_b,key = "hook",
                        config = webhook_config("https://example.com/b"),
                )
                factories.make_edge(self.automation_b,None,step)

        def test_another_automations_run_is_never_retried_or_mutated(self):
                recorder = WebhookRecorder(
                        fail_url = "https://example.com/b",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertFalse(run_automation(
                                str(self.automation_b.id),{},run_id = str(self.run_a.id),
                        ))
                        _args,kwargs = retry_dispatch(apply_async)

                # A's failed row is exactly where it was: no status change, no
                # context/decision rewrite, no error rewrite.
                self.run_a.refresh_from_db()
                self.assertEqual(self.run_a.status,RunStatus.FAILED)
                self.assertEqual(self.run_a.error_message,"A failed")
                self.assertEqual(self.run_a.context,
                                 {"steps": {"a": {"status": "failed"}}})
                self.assertEqual(self.run_a.decision,{"result": False})
                self.assertEqual(self.run_a.automation_id,self.automation_a.id)

                # B got its own fresh run and the retry names THAT row, not A's.
                self.assertEqual(AutomationRun.objects.filter(
                        automation = self.automation_a,
                ).count(),1)

                b_runs = AutomationRun.objects.filter(automation = self.automation_b)
                self.assertEqual(b_runs.count(),1)
                self.assertNotEqual(kwargs["run_id"],str(self.run_a.id))
                self.assertEqual(kwargs["run_id"],str(b_runs.get().id))

                # A transient B failure is suppressed; nothing was announced through
                # A's run or context.
                notify.assert_not_called()

        def test_a_missing_automation_cannot_touch_another_automations_run(self):
                with patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertIsNone(run_automation(
                                str(uuid.uuid4()),{},run_id = str(self.run_a.id),
                        ))

                apply_async.assert_not_called()
                notify.assert_not_called()

                self.run_a.refresh_from_db()
                self.assertEqual(self.run_a.status,RunStatus.FAILED)
                self.assertEqual(self.run_a.error_message,"A failed")
                self.assertEqual(AutomationRun.objects.count(),1)


class MemoizedRetryTests(TestCase):
        """The retry finishes the run; it does not repeat the work that succeeded."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                template = factories.make_template(
                        self.tenant,raw_text = "Merhaba {{customer}}",
                )

                first = factories.make_step(
                        self.automation,key = "1",action_type = "webhook",template = template,
                        config = webhook_config("https://example.com/1"),
                )
                second = factories.make_step(
                        self.automation,key = "2",action_type = "webhook",
                        config = webhook_config("https://example.com/2",{
                                "echo": "{{steps.1.output.message}}",
                        }),
                )
                factories.make_edge(self.automation,None,first)
                factories.make_edge(self.automation,first,second)

        def test_only_the_failed_step_runs_again_and_the_run_then_succeeds(self):
                crashing = WebhookRecorder(
                        fail_url = "https://example.com/2",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = crashing), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertFalse(run_automation(
                                str(self.automation.id),{"customer": "Ada"},
                        ))
                        args,kwargs = retry_dispatch(apply_async)

                run = AutomationRun.objects.get()
                self.assertEqual(run.status,RunStatus.QUEUED)
                self.assertEqual(run.step_runs.get(step_key = "1").status,
                                 StepRunStatus.SUCCESS)
                self.assertEqual(run.step_runs.get(step_key = "2").status,
                                 StepRunStatus.FAILED)

                recorder = WebhookRecorder()

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch.object(run_automation,"apply_async") as retry_async:
                        self.assertTrue(run_automation(*args,**kwargs))

                retry_async.assert_not_called()

                # Step 1 is memoized: the provider never sees it again. Step 2 runs
                # once and reads step 1's stored output, not a re-executed one.
                self.assertEqual(recorder.calls_to("https://example.com/1"),[])
                self.assertEqual(len(recorder.calls_to("https://example.com/2")),1)
                self.assertEqual(
                        recorder.calls_to("https://example.com/2")[0]["json"],
                        {"echo": "Merhaba Ada"},
                )

                self.assertEqual(AutomationRun.objects.count(),1)

                run.refresh_from_db()
                self.assertEqual(run.status,RunStatus.SUCCESS)
                self.assertIsNotNone(run.finished_at)
                self.assertEqual(run.step_runs.count(),2)
                self.assertEqual(run.step_runs.get(step_key = "2").status,
                                 StepRunStatus.SUCCESS)


class RetryBudgetTests(TestCase):
        """retry_attempt is internal, non-negative, and exhaustion is terminal."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                step = factories.make_step(
                        self.automation,key = "hook",
                        config = webhook_config("https://example.com/never"),
                )
                factories.make_edge(self.automation,None,step)

        def test_the_next_attempt_is_dispatched_with_an_incremented_counter_and_backoff(self):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch("apps.automations.tasks.retry_countdown",
                                      return_value = 42) as countdown, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        run_automation(str(self.automation.id),{})

                        self.assertEqual(apply_async.call_count,1)
                        args,kwargs = retry_dispatch(apply_async)
                        countdown.assert_called_once_with(0)

                self.assertEqual(kwargs['retry_attempt'],1)
                self.assertEqual(kwargs['run_id'],
                                 str(AutomationRun.objects.get().id))
                self.assertEqual(apply_async.call_args.kwargs['countdown'],42)

        def test_exhaustion_marks_failed_permanently_and_keeps_the_error_and_context(self):
                dispatched = 0

                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        args,kwargs = (str(self.automation.id),{}),{}

                        while True:
                                run_automation(*args,**kwargs)

                                if not apply_async.called:
                                        break

                                args,kwargs = retry_dispatch(apply_async)
                                apply_async.reset_mock()
                                dispatched += 1

                # 1 initial attempt + 3 re-dispatches (the default budget).
                self.assertEqual(dispatched,3)

                run = AutomationRun.objects.get()
                self.assertEqual(run.status,RunStatus.FAILED_PERMANENTLY)
                self.assertIsNotNone(run.finished_at)
                self.assertIn("webhook",run.error_message.lower())
                self.assertIn("hook",run.context.get("steps",{}))
                self.assertIsNotNone(run.decision)

        def test_a_negative_retry_attempt_is_treated_as_the_first_attempt(self):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        run_automation(str(self.automation.id),{},retry_attempt = -3)
                        _args,kwargs = retry_dispatch(apply_async)

                self.assertEqual(kwargs['retry_attempt'],1)

        def test_a_persisted_live_run_called_with_test_mode_still_retries_live(self):
                # The caller-supplied mode is not the authority once the row exists.
                # Dropping it from `may_retry` is what keeps this LIVE run on the LIVE
                # retry path instead of marking it permanent on the first failure.
                run = factories.make_run(
                        self.automation,status = RunStatus.FAILED,mode = RunMode.LIVE,
                        trigger_payload = {},
                )
                recorder = WebhookRecorder(
                        fail_url = "https://example.com/never",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertFalse(run_automation(
                                str(self.automation.id),{},run_id = str(run.id),
                                mode = "test",
                        ))
                        _args,kwargs = retry_dispatch(apply_async)

                # LIVE effects: the provider was actually called, not simulated.
                self.assertEqual(recorder.failures_left,0)
                self.assertEqual(kwargs["mode"],RunMode.LIVE)

                run.refresh_from_db()
                self.assertEqual(run.status,RunStatus.QUEUED)
                self.assertIsNone(run.finished_at)


class ExplicitIdFailureTests(TestCase):
        """An unknown/malformed explicit id must not orphan the fresh row."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                step = factories.make_step(
                        self.automation,key = "hook",
                        config = webhook_config("https://example.com/never"),
                )
                factories.make_edge(self.automation,None,step)

        def _drive(self,explicit_id):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        args,kwargs = (str(self.automation.id),{}),{
                                "run_id": explicit_id,
                        }
                        dispatched = 0

                        while True:
                                run_automation(*args,**kwargs)

                                if not apply_async.called:
                                        break

                                args,kwargs = retry_dispatch(apply_async)
                                apply_async.reset_mock()
                                dispatched += 1

                return dispatched,notify

        def test_an_unknown_explicit_id_still_retries_and_ends_permanently(self):
                explicit = str(uuid.uuid4())
                dispatched,notify = self._drive(explicit)

                self.assertEqual(dispatched,3)

                runs = AutomationRun.objects.filter(automation = self.automation)
                self.assertEqual(runs.count(),1)
                run = runs.get()
                self.assertNotEqual(str(run.id),explicit)
                self.assertEqual(run.status,RunStatus.FAILED_PERMANENTLY)
                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"failure")

        def test_a_malformed_explicit_id_still_retries_and_ends_permanently(self):
                dispatched,notify = self._drive("not-a-uuid")

                self.assertEqual(dispatched,3)

                runs = AutomationRun.objects.filter(automation = self.automation)
                self.assertEqual(runs.count(),1)
                self.assertEqual(runs.get().status,RunStatus.FAILED_PERMANENTLY)
                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"failure")


class FailureFanOutTests(TestCase):
        """A transient failure is silent; the one final failure is announced once."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                step = factories.make_step(self.automation,key = "hook",action_type = "webhook")
                factories.make_edge(self.automation,None,step)

        def test_a_transient_failure_does_not_fan_out(self):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async"):
                        run_automation(str(self.automation.id),{})

                notify.assert_not_called()

        def test_the_final_failure_fans_out_exactly_once(self):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        args,kwargs = (str(self.automation.id),{}),{}

                        while True:
                                run_automation(*args,**kwargs)

                                if not apply_async.called:
                                        break

                                args,kwargs = retry_dispatch(apply_async)
                                apply_async.reset_mock()

                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"failure")

        def test_a_successful_retry_fans_out_success_once_and_never_failure(self):
                recorder = WebhookRecorder(
                        fail_url = "https://example.com/hook",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        run_automation(str(self.automation.id),{})
                        args,kwargs = retry_dispatch(apply_async)
                        run_automation(*args,**kwargs)

                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"success")

        def test_a_retry_dispatch_failure_closes_the_run_and_fans_out_once(self):
                with patch(WEBHOOK_REQUESTS,side_effect = OSError("down")), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async",
                                             side_effect = RuntimeError("broker down")):
                        self.assertFalse(run_automation(str(self.automation.id),{}))

                run = AutomationRun.objects.get()
                self.assertEqual(run.status,RunStatus.FAILED_PERMANENTLY)
                self.assertIsNotNone(run.finished_at)
                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"failure")

        def test_a_failed_legacy_action_fans_out_once_on_the_final_attempt(self):
                # The action-list executor returns False instead of raising; that path
                # must fan out like every other failure, or the retry's suppression
                # would silence it forever.
                factories.make_action(
                        self.automation,order = 1,action_type = "webhook",
                        on_failure = "stop",
                )

                with patch.object(AutomationExecutor,"_execute_single",
                                  side_effect = ExecutionError("provider is down")), \
                                patch(NOTIFY) as notify, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        args,kwargs = (str(self.automation.id),{}),{}

                        while True:
                                run_automation(*args,**kwargs)

                                if not apply_async.called:
                                        break

                                args,kwargs = retry_dispatch(apply_async)
                                apply_async.reset_mock()

                self.assertEqual(notify.call_count,1)
                self.assertEqual(notify.call_args[0][1],"failure")


class NoRetryTests(TestCase):
        """Only a live, active, failed run with budget has a queue lifecycle."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)

        def test_a_skipped_run_is_not_retried(self):
                factories.make_condition(
                        self.automation,field = "amount",operator = "gt",value = 100,
                )
                step = factories.make_step(self.automation,key = "hook",action_type = "webhook")
                factories.make_edge(self.automation,None,step)

                with patch(WEBHOOK_REQUESTS) as request, \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertFalse(run_automation(
                                str(self.automation.id),{"amount": 5},
                        ))

                request.assert_not_called()
                apply_async.assert_not_called()
                self.assertEqual(AutomationRun.objects.get().status,RunStatus.SKIPPED)

        def test_an_inactive_automation_is_not_retried(self):
                step = factories.make_step(self.automation,key = "hook",action_type = "webhook")
                factories.make_edge(self.automation,None,step)
                self.automation.is_active = False
                self.automation.save()

                with patch.object(run_automation,"apply_async") as apply_async, \
                                patch(NOTIFY) as notify:
                        self.assertFalse(run_automation(str(self.automation.id),{}))

                apply_async.assert_not_called()
                notify.assert_not_called()
                self.assertEqual(AutomationRun.objects.get().status,
                                 RunStatus.FAILED_PERMANENTLY)

        def test_a_missing_automation_is_not_retried(self):
                with patch.object(run_automation,"apply_async") as apply_async:
                        self.assertIsNone(run_automation(str(uuid.uuid4()),{}))

                apply_async.assert_not_called()
                self.assertEqual(AutomationRun.objects.count(),0)

        def test_a_failed_dry_run_is_not_retried(self):
                step = factories.make_step(self.automation,key = "hook",action_type = "webhook")
                factories.make_edge(self.automation,None,step)

                failed = GraphExecutionResult(
                        status = "failed",context = {"steps": {},"vars": {}},
                        step_results = [],
                )

                with patch.object(AutomationExecutor,"execute_graph",
                                  return_value = failed), \
                                patch.object(run_automation,"apply_async") as apply_async, \
                                patch(NOTIFY) as notify:
                        self.assertFalse(run_automation(
                                str(self.automation.id),{},mode = "test",
                        ))

                apply_async.assert_not_called()
                notify.assert_not_called()

                run = AutomationRun.objects.get()
                self.assertEqual(run.mode,RunMode.TEST)
                self.assertEqual(run.status,RunStatus.FAILED)


class WaitContinuationRetryTests(TestCase):
        """A failed continuation retries as a continuation, not as a fresh run."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                template = factories.make_template(
                        self.tenant,raw_text = "Merhaba {{customer}}",
                )

                first = factories.make_step(
                        self.automation,key = "send1",action_type = "webhook",
                        template = template,
                        config = webhook_config("https://example.com/1"),
                )
                wait = factories.make_step(
                        self.automation,key = "wait",step_type = "wait",action_type = "",
                        config = {"duration": "1h"},
                )
                second = factories.make_step(
                        self.automation,key = "send2",action_type = "webhook",
                        config = webhook_config("https://example.com/2",{
                                "text": "{{steps.send1.output.message}}",
                        }),
                )
                factories.make_edge(self.automation,None,first)
                factories.make_edge(self.automation,first,wait)
                factories.make_edge(self.automation,wait,second)

        def test_a_failed_continuation_keeps_its_step_key_and_full_context_on_retry(self):
                with patch(WEBHOOK_REQUESTS,side_effect = WebhookRecorder()), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        self.assertTrue(run_automation(
                                str(self.automation.id),{"customer": "Ada"},
                        ))
                        paused_args,paused_kwargs = retry_dispatch(apply_async)

                # A fresh continuation starts a new leg: no retry budget carried over.
                self.assertNotIn("retry_attempt",paused_kwargs)
                self.assertEqual(paused_kwargs["start_step_key"],"send2")

                paused_context = paused_args[1]
                failing = WebhookRecorder(
                        fail_url = "https://example.com/2",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = failing), \
                                patch.object(run_automation,"apply_async") as retry_async:
                        self.assertFalse(run_automation(*paused_args,**paused_kwargs))
                        retry_args,retry_kwargs = retry_dispatch(retry_async)

                self.assertEqual(retry_kwargs["start_step_key"],"send2")
                self.assertEqual(retry_kwargs["run_id"],paused_kwargs["run_id"])
                self.assertEqual(retry_kwargs["retry_attempt"],1)
                # The retry carries the paused context, not trigger_payload: replacing
                # it would rebuild the graph and lose send1's memoized output.
                self.assertEqual(retry_args[1],paused_context)

                recorder = WebhookRecorder()

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch.object(run_automation,"apply_async") as final_async:
                        self.assertTrue(run_automation(*retry_args,**retry_kwargs))

                final_async.assert_not_called()
                self.assertEqual(recorder.calls_to("https://example.com/1"),[])
                self.assertEqual(
                        recorder.calls_to("https://example.com/2")[0]["json"],
                        {"text": "Merhaba Ada"},
                )

                run = AutomationRun.objects.get()
                self.assertEqual(run.status,RunStatus.SUCCESS)
                self.assertEqual(run.step_runs.get(step_key = "send2").status,
                                 StepRunStatus.SUCCESS)


class ScheduledRetryBudgetTests(TestCase):
        """The beat budget is spent once, on the final return of the retry chain."""

        def setUp(self):
                self.tenant = factories.make_tenant()
                self.automation = factories.make_automation(self.tenant)
                self.trigger = factories.make_trigger(
                        self.automation,trigger_type = "scheduled",
                        config = {
                                "schedule_type": "n",
                                "count": 2,
                                "cron_expression": "0 9 * * 1",
                        },
                )
                step = factories.make_step(
                        self.automation,key = "hook",
                        config = webhook_config("https://example.com/never"),
                )
                factories.make_edge(self.automation,None,step)

        def test_a_scheduled_run_spends_its_budget_once_across_a_retry(self):
                recorder = WebhookRecorder(
                        fail_url = "https://example.com/never",failures = 1,
                )

                with patch(WEBHOOK_REQUESTS,side_effect = recorder), \
                                patch.object(run_automation,"apply_async") as apply_async:
                        run_automation(str(self.automation.id),{},source = "scheduled")

                        # The first attempt did not finish the run; it handed it to a
                        # retry, so the budget is deliberately still unspent.
                        self.assertEqual(AutomationTrigger.objects.get().run_count,0)
                        args,kwargs = retry_dispatch(apply_async)

                self.assertEqual(kwargs["source"],"scheduled")

                with patch(WEBHOOK_REQUESTS,side_effect = WebhookRecorder()), \
                                patch.object(run_automation,"apply_async") as retry_async:
                        self.assertTrue(run_automation(*args,**kwargs))

                retry_async.assert_not_called()
                self.assertEqual(AutomationTrigger.objects.get().run_count,1)
                self.assertEqual(AutomationRun.objects.get().status,RunStatus.SUCCESS)
