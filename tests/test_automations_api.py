"""
apps/automations HTTP surface: automation CRUD, templates, and the two trigger entry
points (webhook and manual).

Access here is two-layered: the tenant/branch scope from RoleBasedAccessMixin, plus
the per-automation `allowed_roles` list. Both are asserted.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.automations.models import (
    Automation,
    AutomationAction,
    AutomationTrigger,
    Template,
    TriggerType,
)
from tests import factories
from tests.utils import CacheTestMixin


class AutomationAPITests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant, "A")
        self.other_branch = factories.make_branch(self.other_tenant, "Z")

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)
        self.staff = factories.make_staff(self.tenant, self.branch)

        self.automation = factories.make_automation(
            self.tenant, self.branch, allowed_roles=["super_admin", "owner"], name="Mine"
        )
        self.foreign = factories.make_automation(
            self.other_tenant, self.other_branch,
            allowed_roles=["super_admin", "owner"], name="Theirs",
        )

    def _payload(self, **overrides):
        payload = {
            "name": "Created",
            "allowed_roles": ["super_admin", "owner"],
            "metadata": {},
            "tenant": str(self.tenant.id),
            "branch": str(self.branch.id),
            "triggers": [{"trigger_type": "manual", "config": {}, "initial_data": {}}],
            "conditions": [
                {"field": "amount", "operator": "gt", "value": "100",
                 "logical_operator": "and"}
            ],
            "actions": [
                {
                    "action_type": "webhook",
                    "order": 1,
                    "on_failure": "stop",
                    "config": {"url": "https://example.com/hook", "method": "POST",
                               "headers": {}},
                }
            ],
        }
        payload.update(overrides)
        return payload

    # -- authentication --------------------------------------------------------

    def test_anonymous_access_is_refused(self):
        # REST_FRAMEWORK's default permission key was misspelled
        # (DEFAULT_PERMISSON_CLASSES), so DRF fell back to AllowAny and every view
        # without explicit permission_classes - all of these - was public.
        for url in ('/api/v1/automations/', '/api/v1/automations/templates/'):
            response = self.client.get(url)
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                url,
            )

    def test_anonymous_cannot_create_an_automation(self):
        response = self.client.post('/api/v1/automations/', self._payload(), format='json')
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        self.assertFalse(Automation.objects.filter(name="Created").exists())

    # -- list ------------------------------------------------------------------

    def test_super_admin_sees_every_automation_they_are_allowed_on(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/automations/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(sorted(a['name'] for a in response.data), ["Mine", "Theirs"])

    def test_owner_only_sees_their_own_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/automations/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([a['name'] for a in response.data], ["Mine"])

    def test_allowed_roles_filters_the_list(self):
        factories.make_automation(
            self.tenant, self.branch, allowed_roles=["super_admin"], name="AdminOnly"
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/automations/')
        self.assertNotIn("AdminOnly", [a['name'] for a in response.data])

    def test_a_branch_manager_sees_only_their_branch(self):
        other_branch_automation = factories.make_automation(
            self.tenant,
            factories.make_branch(self.tenant, "B"),
            allowed_roles=["branch_manager"],
            name="OtherBranch",
        )
        self.automation.allowed_roles = ["branch_manager"]
        self.automation.save()

        self.client.force_authenticate(user=self.manager)
        response = self.client.get('/api/v1/automations/')
        self.assertEqual([a['name'] for a in response.data], ["Mine"])

    # -- create ----------------------------------------------------------------

    def test_owner_can_create_a_full_automation_graph(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post('/api/v1/automations/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        created = Automation.objects.get(name="Created")
        self.assertEqual(created.triggers.count(), 1)
        self.assertEqual(created.conditions.count(), 1)
        self.assertEqual(created.actions.count(), 1)

    def test_creating_in_another_tenant_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/',
            self._payload(tenant=str(self.other_tenant.id), branch=str(self.other_branch.id)),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_author_must_include_their_own_role_in_allowed_roles(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/', self._payload(allowed_roles=["super_admin"]), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_channel_backed_action_requires_a_channel(self):
        self.client.force_authenticate(user=self.owner)
        payload = self._payload(actions=[{
            "action_type": "email",
            "order": 1,
            "on_failure": "stop",
            "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        }])
        response = self.client.post('/api/v1/automations/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_channel_of_the_wrong_type_is_rejected(self):
        sms_channel = factories.make_channel(
            self.tenant, self.branch, channel_type="sms",
            credentials=dict(factories.SMS_CREDENTIALS),
        )
        self.client.force_authenticate(user=self.owner)
        payload = self._payload(actions=[{
            "action_type": "email",
            "channel": str(sms_channel.id),
            "order": 1,
            "on_failure": "stop",
            "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        }])
        response = self.client.post('/api/v1/automations/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_matching_channel_is_accepted(self):
        email_channel = factories.make_channel(self.tenant, self.branch)
        self.client.force_authenticate(user=self.owner)
        payload = self._payload(actions=[{
            "action_type": "email",
            "channel": str(email_channel.id),
            "order": 1,
            "on_failure": "stop",
            "config": {"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        }])
        response = self.client.post('/api/v1/automations/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_an_invalid_trigger_config_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        payload = self._payload(triggers=[{
            "trigger_type": "scheduled",
            "config": {"schedule_type": "forever"},  # no cron_expression
            "initial_data": {},
        }])
        response = self.client.post('/api/v1/automations/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_scheduled_trigger_can_be_created_through_the_api(self):
        # Creating one writes a CrontabSchedule and a PeriodicTask via the
        # automations post_save receiver; both used to blow up (UUID -> json, and
        # the global pre_save cache receiver).
        self.client.force_authenticate(user=self.owner)
        payload = self._payload(triggers=[{
            "trigger_type": "scheduled",
            "config": {"schedule_type": "forever", "cron_expression": "0 1 * * *"},
            "initial_data": {},
        }])
        response = self.client.post('/api/v1/automations/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        from django_celery_beat.models import PeriodicTask

        created = Automation.objects.get(name="Created")
        self.assertTrue(PeriodicTask.objects.filter(name=f"automation_{created.id}").exists())

    # -- detail ----------------------------------------------------------------

    def test_owner_can_read_their_own_automation(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/{self.automation.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Mine")

    def test_owner_cannot_read_another_tenants_automation(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/{self.foreign.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_role_outside_allowed_roles_is_refused(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.get(f'/api/v1/automations/{self.automation.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_replaces_the_child_rows(self):
        factories.make_trigger(self.automation)
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            f'/api/v1/automations/{self.automation.id}/',
            self._payload(name="Renamed"),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.automation.refresh_from_db()
        self.assertEqual(self.automation.name, "Renamed")
        self.assertEqual(self.automation.triggers.count(), 1)

    def test_delete_removes_the_automation_and_its_children(self):
        factories.make_trigger(self.automation)
        factories.make_action(self.automation)
        self.client.force_authenticate(user=self.owner)

        response = self.client.delete(f'/api/v1/automations/{self.automation.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Automation.objects.filter(pk=self.automation.pk).exists())
        self.assertEqual(AutomationTrigger.objects.filter(automation_id=self.automation.pk).count(), 0)


class ManualTriggerTests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.owner = factories.make_owner(self.tenant)
        self.staff = factories.make_staff(self.tenant, self.branch)

        self.automation = factories.make_automation(
            self.tenant, self.branch, allowed_roles=["owner"]
        )
        self.trigger = factories.make_trigger(
            self.automation, trigger_type="manual", initial_data={"default": True}
        )

    @patch("apps.automations.views.run_automation.delay")
    def test_an_allowed_role_can_fire_the_automation(self, mock_delay):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f'/api/v1/automations/{self.automation.id}/run/', {"amount": 5}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()

    @patch("apps.automations.views.run_automation.delay")
    def test_the_task_gets_a_string_pk(self, mock_delay):
        # CELERY_TASK_SERIALIZER is 'json'; a UUID raises at dispatch time.
        self.client.force_authenticate(user=self.owner)
        self.client.post(f'/api/v1/automations/{self.automation.id}/run/', {}, format='json')
        automation_id, _data = mock_delay.call_args[0]
        self.assertIsInstance(automation_id, str)

    @patch("apps.automations.views.run_automation.delay")
    def test_an_empty_body_falls_back_to_the_triggers_initial_data(self, mock_delay):
        self.client.force_authenticate(user=self.owner)
        self.client.post(f'/api/v1/automations/{self.automation.id}/run/', {}, format='json')
        _automation_id, data = mock_delay.call_args[0]
        self.assertEqual(data, {"default": True})

    def test_a_role_outside_allowed_roles_is_refused(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f'/api/v1/automations/{self.automation.id}/run/', {}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_automation_without_a_manual_trigger_is_a_404(self):
        other = factories.make_automation(self.tenant, self.branch, allowed_roles=["owner"])
        factories.make_trigger(other, trigger_type="webhook", config={})

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(f'/api/v1/automations/{other.id}/run/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class WebhookTriggerTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.trigger = factories.make_trigger(
            self.automation, trigger_type="webhook", config={}
        )
        self.secret = self.trigger.config["secret_key"]

    @patch("apps.automations.views.run_automation.delay")
    def test_a_valid_secret_fires_the_automation(self, mock_delay):
        response = self.client.post(
            '/api/v1/automations/webhook/',
            {"order_id": 1},
            format='json',
            headers={"X-Webhook-Secret": self.secret},
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()

    @patch("apps.automations.views.run_automation.delay")
    def test_the_payload_is_forwarded(self, mock_delay):
        self.client.post(
            '/api/v1/automations/webhook/',
            {"order_id": 42},
            format='json',
            headers={"X-Webhook-Secret": self.secret},
        )
        _automation_id, payload = mock_delay.call_args[0]
        self.assertEqual(payload, {"order_id": 42})

    def test_a_missing_secret_is_rejected(self):
        response = self.client.post('/api/v1/automations/webhook/', {}, format='json')
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_a_wrong_secret_is_rejected(self):
        response = self.client.post(
            '/api/v1/automations/webhook/',
            {},
            format='json',
            headers={"X-Webhook-Secret": "not-the-secret"},
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_a_non_webhook_triggers_secret_does_not_work(self):
        manual = factories.make_trigger(
            self.automation, trigger_type="manual", config={"secret_key": "manual-secret"}
        )
        response = self.client.post(
            '/api/v1/automations/webhook/',
            {},
            format='json',
            headers={"X-Webhook-Secret": "manual-secret"},
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class TemplateAPITests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant, "A")
        self.other_branch = factories.make_branch(self.other_tenant, "Z")

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.staff = factories.make_staff(self.tenant, self.branch)

        self.template = factories.make_template(self.tenant, self.branch)

    def _payload(self, **overrides):
        payload = {
            "name": "Welcome",
            "raw_text": "Merhaba {{customer}}",
            "fields": {
                "customer": {
                    "type": "string",
                    "required": True,
                    "description": "Customer name",
                    "source": "payload",
                }
            },
            "tenant": str(self.tenant.id),
            "branch": str(self.branch.id),
            "channel_type": "email",
        }
        payload.update(overrides)
        return payload

    def test_owner_can_create_a_template(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/', self._payload(), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(Template.objects.filter(name="Welcome").exists())

    def test_a_placeholder_with_no_matching_field_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/',
            self._payload(raw_text="Merhaba {{customer}} - {{undefined_field}}"),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_required_field_inside_an_optional_block_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/',
            self._payload(
                raw_text="Merhaba{%if customer%} {{customer}}{%endif%}",
            ),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_optional_field_inside_a_block_is_fine(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/',
            self._payload(
                raw_text="Merhaba{%if bonus%} {{bonus}}{%endif%}",
                fields={
                    "bonus": {
                        "type": "string", "required": False,
                        "description": "Bonus", "source": "payload",
                    }
                },
            ),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_a_malformed_field_definition_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/',
            self._payload(fields={"customer": {"type": "string"}}),  # no required/source
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_creating_in_another_tenant_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/automations/templates/',
            self._payload(tenant=str(self.other_tenant.id), branch=str(self.other_branch.id)),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_may_not_list_templates(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/v1/automations/templates/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_lists_only_their_tenants_templates(self):
        factories.make_template(self.other_tenant, self.other_branch, name="Foreign")
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/automations/templates/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([t['name'] for t in response.data], ["Greeting"])

    def test_reading_a_template(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/automations/templates/{self.template.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Greeting")

    def test_updating_a_template_bumps_its_version(self):
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.template.version, 1)

        response = self.client.put(
            f'/api/v1/automations/templates/{self.template.id}/',
            self._payload(name="Greeting v2"),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.template.refresh_from_db()
        self.assertEqual(self.template.version, 2)

    def test_deleting_a_template(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(f'/api/v1/automations/templates/{self.template.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Template.objects.filter(pk=self.template.pk).exists())
