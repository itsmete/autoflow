"""
Faz 2.B / 2.C: paginated run history, dry run (RunMode.TEST) and replay.

Three things are asserted here, and they are the three the roadmap asks for:

  * `logs` is no longer embedded in an automation read. History is paginated, one run
    at a time, behind the same two-layer access check (tenant/branch scope plus the
    automation's own `allowed_roles`).
  * a `mode='test'` run has NO effect: no provider call, no HTTP request, no wait
    parked in Celery, and no `automation_completed` fan-out into live automations.
  * a replay is a new run with the source's payload and a `replay_of` pointer, and it
    executes its steps fresh - memoizing the source's successes would make it a no-op.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.automations.engine.executor import RESULT_SUCCESS
from apps.automations.engine.orchestrator import AutomationOrchestrator
from apps.automations.models import (
    AutomationRun,
    RunMode,
    RunStatus,
    StepRunStatus,
    TriggerType,
)
from apps.automations.tasks import run_automation
from apps.channels.providers import SMSProvider
from tests import factories
from tests.utils import CacheTestMixin


HTTP_REQUEST = "apps.automations.engine.actions.http_request.requests.request"
WEBHOOK_REQUEST = "apps.automations.engine.actions.send_webhook.requests.request"
DISPATCH = "apps.automations.runtime.dispatch_automation"


class AccessTestCase(CacheTestMixin, TestCase):
    """The hierarchy every endpoint test below needs, built once."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant, "A")
        self.other_tenant = factories.make_tenant(name="Globex")
        self.other_branch = factories.make_branch(self.other_tenant, "Z")

        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)
        self.staff = factories.make_staff(self.tenant, self.branch)
        self.foreign_owner = factories.make_owner(self.other_tenant)

        self.automation = factories.make_automation(
            self.tenant, self.branch, allowed_roles=["owner"], name="Mine"
        )
        self.foreign = factories.make_automation(
            self.other_tenant, self.other_branch, allowed_roles=["owner"], name="Theirs"
        )


#
# T-202: the read payload and the history endpoints
#

class AutomationReadPayloadTests(AccessTestCase):

    def test_a_read_no_longer_embeds_the_run_history(self):
        # The old payload carried `logs` - every log row, on every read, forever.
        factories.make_log(self.automation)
        factories.make_run(self.automation, status=RunStatus.SUCCESS)

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/{self.automation.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('logs', response.data)
        # Everything else the client authored is still there.
        for key in ('name', 'allowed_roles', 'triggers', 'conditions', 'actions',
                    'steps', 'edges'):
            self.assertIn(key, response.data)

    def test_the_list_payload_does_not_embed_history_either(self):
        factories.make_run(self.automation, status=RunStatus.SUCCESS)

        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/automations/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for item in response.data:
            self.assertNotIn('logs', item)


class RunListAPITests(AccessTestCase):

    def setUp(self):
        super().setUp()
        self.live = factories.make_run(
            self.automation, status=RunStatus.SUCCESS, mode=RunMode.LIVE
        )
        self.test_run = factories.make_run(
            self.automation, status=RunStatus.FAILED, mode=RunMode.TEST
        )
        self.queued = factories.make_run(
            self.automation, status=RunStatus.QUEUED, mode=RunMode.LIVE
        )

    def _get(self, user, query=""):
        self.client.force_authenticate(user=user)
        return self.client.get(f'/api/v1/automations/{self.automation.id}/runs/{query}')

    def test_the_history_is_paginated(self):
        response = self._get(self.owner)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)
        self.assertEqual(response.data['total_pages'], 1)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)

        ids = {item['id'] for item in response.data['results']}
        self.assertEqual(ids, {str(self.live.id), str(self.test_run.id), str(self.queued.id)})

        # Newest first: the list ordering is the model's, not the insertion order.
        self.assertEqual(response.data['results'][0]['id'], str(self.queued.id))

    def test_a_status_filter_narrows_the_history(self):
        response = self._get(self.owner, "?status=failed")

        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(self.test_run.id))

    def test_a_mode_filter_narrows_the_history(self):
        response = self._get(self.owner, "?mode=test")

        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(self.test_run.id))

    def test_an_unknown_filter_is_refused_instead_of_ignored(self):
        # Silently ignoring a typo would return the whole history, and a caller
        # cannot tell that from a filter that matched everything.
        self.assertEqual(self._get(self.owner, "?status=nope").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._get(self.owner, "?mode=nope").status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_a_role_outside_allowed_roles_cannot_read_the_history(self):
        self.assertEqual(self._get(self.staff).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._get(self.manager).status_code, status.HTTP_403_FORBIDDEN)

    def test_another_tenant_cannot_read_the_history(self):
        self.assertEqual(self._get(self.foreign_owner).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_an_unknown_automation_is_a_404(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            '/api/v1/automations/00000000-0000-0000-0000-000000000000/runs/'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RunDetailAPITests(AccessTestCase):

    def setUp(self):
        super().setUp()
        self.run = factories.make_run(
            self.automation, status=RunStatus.SUCCESS, mode=RunMode.LIVE,
            trigger_payload={"customer": "Ada"},
        )
        self.step_run = factories.make_step_run(
            self.run, step_key="1", output={"sender": "would_send"},
            duration_ms=7,
        )

    def test_a_run_is_readable_with_its_payload(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/runs/{self.run.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(self.run.id))
        self.assertEqual(response.data['status'], RunStatus.SUCCESS)
        self.assertEqual(response.data['trigger_payload'], {"customer": "Ada"})
        self.assertIsNone(response.data['replay_of'])

    def test_the_steps_of_a_run_are_paginated(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/runs/{self.run.id}/steps/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['total_pages'], 1)
        step = response.data['results'][0]
        self.assertEqual(step['step_key'], "1")
        self.assertEqual(step['output'], {"sender": "would_send"})
        self.assertEqual(step['duration_ms'], 7)

    def test_another_tenant_cannot_read_the_run_or_its_steps(self):
        self.client.force_authenticate(user=self.foreign_owner)

        self.assertEqual(
            self.client.get(f'/api/v1/automations/runs/{self.run.id}/').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get(f'/api/v1/automations/runs/{self.run.id}/steps/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_role_outside_allowed_roles_cannot_read_the_run(self):
        self.client.force_authenticate(user=self.staff)

        self.assertEqual(
            self.client.get(f'/api/v1/automations/runs/{self.run.id}/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_an_unknown_run_is_a_404(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            '/api/v1/automations/runs/00000000-0000-0000-0000-000000000000/'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


#
# T-203: dry run (RunMode.TEST)
#

class DryRunTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.automation = factories.make_automation(self.tenant, self.branch)
        self.orchestrator = AutomationOrchestrator()

    def _sms_step(self, key="sms"):
        channel = factories.make_channel(
            self.tenant, self.branch, channel_type="sms",
            credentials=dict(factories.SMS_CREDENTIALS),
        )
        return factories.make_step(
            self.automation, key=key, action_type="sms", channel=channel,
            config={"message": "Merhaba {{trigger.customer}}"},
        )

    def _run(self, mode="test"):
        step = self._sms_step()
        factories.make_edge(self.automation, None, step)

        with patch.object(SMSProvider, "send", return_value=True) as send, \
                        patch.object(SMSProvider, "simulate",
                                    return_value={"would_send": {"message": "x"}}) as simulate:
            result = self.orchestrator.run(
                str(self.automation.id), {"customer": "Ada"}, mode=mode
            )

        return result, send, simulate

    def test_a_channel_action_is_simulated_not_sent(self):
        result, send, simulate = self._run()

        self.assertTrue(result)
        send.assert_not_called()
        simulate.assert_called_once()

        run = AutomationRun.objects.get(automation=self.automation)
        self.assertEqual(run.mode, RunMode.TEST)
        self.assertEqual(run.status, RunStatus.SUCCESS)

        step_run = run.step_runs.get(step_key="sms")
        self.assertEqual(step_run.status, StepRunStatus.SUCCESS)
        self.assertEqual(step_run.output["would_send"], {"message": "x"})
        # The run namespaces and the flag itself are never part of what a step
        # reports it sent.
        self.assertNotIn("_mode", step_run.output["prepared"])
        self.assertNotIn("trigger", step_run.output["prepared"])

    def test_the_rendered_message_is_what_would_have_been_sent(self):
        # No patch on simulate: the provider default answers with the payload.
        step = self._sms_step()
        factories.make_edge(self.automation, None, step)

        with patch.object(SMSProvider, "send", return_value=True) as send:
            self.orchestrator.run(
                str(self.automation.id), {"customer": "Ada"}, mode="test"
            )

        send.assert_not_called()

        step_run = AutomationRun.objects.get(automation=self.automation).step_runs.get()
        self.assertEqual(step_run.output["would_send"]["message"], "Merhaba Ada")

    def test_the_same_automation_still_really_sends_in_live_mode(self):
        # The counterpart of the test above: the assertion is only worth something if
        # the live path does call the provider.
        step = self._sms_step()
        factories.make_edge(self.automation, None, step)

        with patch.object(SMSProvider, "send", return_value=True) as send:
            self.orchestrator.run(str(self.automation.id), {"customer": "Ada"})

        send.assert_called_once()
        self.assertEqual(send.call_args[0][1]["message"], "Merhaba Ada")
        self.assertEqual(
            AutomationRun.objects.get(automation=self.automation).mode, RunMode.LIVE
        )

    def test_an_http_request_is_not_made_in_test_mode(self):
        step = factories.make_step(
            self.automation, key="check", action_type="http_request",
            config={"url": "https://api.example.com/stock", "method": "POST",
                    "body": {"sku": "{{trigger.sku}}"}},
        )
        factories.make_edge(self.automation, None, step)

        with patch(HTTP_REQUEST) as request:
            self.orchestrator.run(str(self.automation.id), {"sku": "A-1"}, mode="test")

        request.assert_not_called()

        step_run = AutomationRun.objects.get(automation=self.automation).step_runs.get()
        self.assertEqual(step_run.status, StepRunStatus.SUCCESS)
        self.assertEqual(step_run.output["would_send"]["url"],
                         "https://api.example.com/stock")
        self.assertEqual(step_run.output["would_send"]["body"], {"sku": "A-1"})

    def test_a_webhook_is_not_posted_in_test_mode(self):
        step = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, step)

        with patch(WEBHOOK_REQUEST) as request:
            self.orchestrator.run(str(self.automation.id), {}, mode="test")

        request.assert_not_called()

        step_run = AutomationRun.objects.get(automation=self.automation).step_runs.get()
        self.assertEqual(step_run.output["would_send"]["url"],
                         "https://example.com/hook")

    def test_a_wait_step_is_simulated_and_the_graph_continues(self):
        wait = factories.make_step(
            self.automation, key="wait", step_type="wait", action_type="",
            config={"duration": "24h"},
        )
        hook = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, wait)
        factories.make_edge(self.automation, wait, hook)

        with patch(WEBHOOK_REQUEST) as request, \
                        patch.object(run_automation, "apply_async") as apply_async:
            # Dispatched as a task, because that is what would schedule a
            # continuation if the dry run were not dry.
            self.assertTrue(run_automation(
                str(self.automation.id), {}, mode="test",
            ))

        apply_async.assert_not_called()
        request.assert_not_called()

        run = AutomationRun.objects.get(automation=self.automation)
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertIsNotNone(run.finished_at)

        wait_run = run.step_runs.get(step_key="wait")
        self.assertEqual(wait_run.status, StepRunStatus.SUCCESS)
        self.assertEqual(wait_run.output["would_wait_seconds"], 86400)
        self.assertTrue(wait_run.output["simulated"])

        # The step after the wait ran in the same pass.
        self.assertEqual(run.step_runs.get(step_key="hook").status,
                         StepRunStatus.SUCCESS)

    def test_a_dry_run_does_not_start_the_automation_that_follows_it(self):
        child = factories.make_automation(self.tenant, self.branch, name="Child")
        factories.make_trigger(
            child, trigger_type=TriggerType.AUTOMATION_COMPLETED,
            config={"source_automation_id": str(self.automation.id)},
        )
        step = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, step)

        with patch(WEBHOOK_REQUEST), patch(DISPATCH) as dispatch:
            self.orchestrator.run(str(self.automation.id), {}, mode="test")

        dispatch.assert_not_called()

    def test_a_live_run_still_fans_out_to_the_automation_that_follows_it(self):
        child = factories.make_automation(self.tenant, self.branch, name="Child")
        factories.make_trigger(
            child, trigger_type=TriggerType.AUTOMATION_COMPLETED,
            config={"source_automation_id": str(self.automation.id)},
        )
        step = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, step)

        with patch(WEBHOOK_REQUEST), patch(DISPATCH) as dispatch:
            self.orchestrator.run(str(self.automation.id), {})

        dispatch.assert_called_once()

    def test_a_test_run_of_a_condition_that_does_not_match_is_skipped(self):
        factories.make_condition(self.automation, field="amount", operator="gt", value=100)
        step = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, step)

        with patch(WEBHOOK_REQUEST) as request:
            self.assertFalse(self.orchestrator.run(
                str(self.automation.id), {"amount": 5}, mode="test"
            ))

        request.assert_not_called()
        run = AutomationRun.objects.get(automation=self.automation)
        self.assertEqual(run.mode, RunMode.TEST)
        self.assertEqual(run.status, RunStatus.SKIPPED)


class DryRunEndpointTests(AccessTestCase):

    def setUp(self):
        super().setUp()
        channel = factories.make_channel(
            self.tenant, self.branch, channel_type="sms",
            credentials=dict(factories.SMS_CREDENTIALS),
        )
        factories.make_trigger(
            self.automation, trigger_type=TriggerType.MANUAL,
            initial_data={"customer": "Grace"},
        )
        step = factories.make_step(
            self.automation, key="sms", action_type="sms", channel=channel,
            config={"message": "Merhaba {{trigger.customer}}"},
        )
        factories.make_edge(self.automation, None, step)

    def test_the_endpoint_runs_the_automation_in_test_mode_and_returns_its_steps(self):
        self.client.force_authenticate(user=self.owner)

        with patch.object(SMSProvider, "send", return_value=True) as send:
            response = self.client.post(
                f'/api/v1/automations/{self.automation.id}/test/',
                {"customer": "Ada"}, format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        send.assert_not_called()

        self.assertEqual(response.data['mode'], RunMode.TEST)
        self.assertEqual(response.data['status'], RunStatus.SUCCESS)
        self.assertEqual(len(response.data['step_runs']), 1)
        step_run = response.data['step_runs'][0]
        self.assertEqual(step_run['step_key'], "sms")
        self.assertEqual(step_run['output']['would_send']['message'], "Merhaba Ada")

    def test_an_empty_body_falls_back_to_the_triggers_initial_data(self):
        self.client.force_authenticate(user=self.owner)

        with patch.object(SMSProvider, "send", return_value=True):
            response = self.client.post(
                f'/api/v1/automations/{self.automation.id}/test/', {}, format='json'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['trigger_payload'], {"customer": "Grace"})
        self.assertEqual(
            response.data['step_runs'][0]['output']['would_send']['message'],
            "Merhaba Grace",
        )

    def test_a_role_outside_allowed_roles_cannot_dry_run(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(f'/api/v1/automations/{self.automation.id}/test/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(AutomationRun.objects.count(), 0)

    def test_another_tenant_cannot_dry_run(self):
        self.client.force_authenticate(user=self.foreign_owner)

        response = self.client.post(f'/api/v1/automations/{self.automation.id}/test/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(AutomationRun.objects.count(), 0)


#
# T-203: replay
#

class ReplayAPITests(AccessTestCase):

    def setUp(self):
        super().setUp()
        self.source = factories.make_run(
            self.automation, status=RunStatus.FAILED, mode=RunMode.LIVE,
            trigger_payload={"customer": "Ada"},
        )
        factories.make_step_run(
            self.source, step_key="1", status=StepRunStatus.SUCCESS,
            output={"sent": True},
        )

    def _replay(self, user, body=None):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f'/api/v1/automations/runs/{self.source.id}/replay/',
            body if body is not None else {}, format='json',
        )

    @patch("apps.automations.views.run_automation.delay")
    def test_a_replay_queues_a_new_run_that_points_at_its_source(self, delay):
        response = self._replay(self.owner)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        new_id = response.data['id']
        self.assertNotEqual(new_id, str(self.source.id))
        self.assertEqual(str(response.data['replay_of']), str(self.source.id))
        self.assertEqual(response.data['trigger_payload'], {"customer": "Ada"})
        self.assertEqual(response.data['status'], RunStatus.QUEUED)
        self.assertEqual(response.data['mode'], RunMode.LIVE)

        # The task is handed the new run's id and mode, so it resumes THAT row rather
        # than opening a third one.
        delay.assert_called_once()
        args, kwargs = delay.call_args
        self.assertEqual(args[0], str(self.automation.id))
        self.assertEqual(args[1], {"customer": "Ada"})
        self.assertEqual(kwargs['run_id'], new_id)
        self.assertEqual(kwargs['mode'], RunMode.LIVE)

    @patch("apps.automations.views.run_automation.delay")
    def test_the_replayed_mode_defaults_to_the_sources_and_can_be_overridden(self, delay):
        test_source = factories.make_run(
            self.automation, status=RunStatus.SUCCESS, mode=RunMode.TEST
        )

        self.client.force_authenticate(user=self.owner)
        default = self.client.post(
            f'/api/v1/automations/runs/{test_source.id}/replay/', {}, format='json'
        )
        self.assertEqual(default.data['mode'], RunMode.TEST)

        forced = self.client.post(
            f'/api/v1/automations/runs/{test_source.id}/replay/',
            {"mode": "live"}, format='json',
        )
        self.assertEqual(forced.data['mode'], RunMode.LIVE)

    @patch("apps.automations.views.run_automation.delay")
    def test_an_unknown_mode_is_refused(self, delay):
        self.assertEqual(self._replay(self.owner, {"mode": "turbo"}).status_code,
                         status.HTTP_400_BAD_REQUEST)
        delay.assert_not_called()

    @patch("apps.automations.views.run_automation.delay")
    def test_a_run_that_is_still_active_cannot_be_replayed(self, delay):
        # Replaying a run that is midway through its steps would execute them a
        # second time while the first pass still owns them - a double send, not a
        # re-run. 'waiting' is active too: a continuation still owns that run.
        for active in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING):
            source = factories.make_run(self.automation, status=active)

            self.client.force_authenticate(user=self.owner)
            response = self.client.post(
                f'/api/v1/automations/runs/{source.id}/replay/', {}, format='json'
            )

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertFalse(AutomationRun.objects.filter(replay_of=source).exists())

        delay.assert_not_called()

    @patch("apps.automations.views.run_automation.delay")
    def test_every_terminal_status_can_be_replayed(self, delay):
        terminal_statuses = (
            RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.FAILED_PERMANENTLY,
            RunStatus.SKIPPED, RunStatus.CANCELLED,
        )

        for terminal in terminal_statuses:
            source = factories.make_run(self.automation, status=terminal)

            self.client.force_authenticate(user=self.owner)
            response = self.client.post(
                f'/api/v1/automations/runs/{source.id}/replay/', {}, format='json'
            )

            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
            self.assertTrue(AutomationRun.objects.filter(replay_of=source).exists())

        self.assertEqual(delay.call_count, len(terminal_statuses))

    def test_the_replayed_run_executes_its_steps_fresh(self):
        # The source's step 1 succeeded. The replay must run it again - otherwise a
        # replay of a finished automation would do nothing at all.
        hook = factories.make_step(self.automation, key="1", action_type="webhook")
        factories.make_edge(self.automation, None, hook)

        new_run = AutomationRun.objects.create(
            automation=self.automation, status=RunStatus.QUEUED, mode=RunMode.LIVE,
            trigger_payload={"customer": "Ada"}, replay_of=self.source,
        )

        with patch(WEBHOOK_REQUEST) as request:
            self.assertTrue(run_automation(
                str(self.automation.id), {"customer": "Ada"},
                run_id=str(new_run.id), mode=RunMode.LIVE,
            ))

        request.assert_called_once()

        new_run.refresh_from_db()
        self.assertEqual(new_run.status, RunStatus.SUCCESS)
        self.assertEqual(new_run.replay_of_id, self.source.id)
        self.assertEqual(new_run.trigger_payload, {"customer": "Ada"})

        step_run = new_run.step_runs.get(step_key="1")
        self.assertEqual(step_run.status, StepRunStatus.SUCCESS)
        self.assertFalse(step_run.output.get("memoized", False))

        # The source is untouched: a replay is a new attempt, not a mutation.
        source_step = self.source.step_runs.get(step_key="1")
        self.assertEqual(source_step.output, {"sent": True})

    @patch("apps.automations.views.run_automation.delay")
    def test_another_tenant_cannot_replay_a_run(self, delay):
        response = self._replay(self.foreign_owner)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        delay.assert_not_called()
        self.assertEqual(AutomationRun.objects.filter(replay_of=self.source).count(), 0)

    @patch("apps.automations.views.run_automation.delay")
    def test_a_role_outside_allowed_roles_cannot_replay(self, delay):
        response = self._replay(self.staff)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        delay.assert_not_called()


class ReplayRuntimeTests(TestCase):
    """`create_replay_run` on its own, without an HTTP request in the way."""

    def test_a_replay_start_from_an_empty_step_history(self):
        tenant = factories.make_tenant(name="Runtime")
        automation = factories.make_automation(tenant)
        source = factories.make_run(
            automation, status=RunStatus.SUCCESS, mode=RunMode.TEST,
            trigger_payload={"a": 1},
        )
        factories.make_step_run(source, step_key="1", output={"sent": True})

        from apps.automations import runtime

        replay = runtime.create_replay_run(source)

        self.assertEqual(replay.mode, RunMode.TEST)
        self.assertEqual(replay.status, RunStatus.QUEUED)
        self.assertEqual(replay.replay_of_id, source.id)
        self.assertEqual(replay.trigger_payload, {"a": 1})
        self.assertEqual(replay.step_runs.count(), 0)
        self.assertIsNone(replay.started_at)
