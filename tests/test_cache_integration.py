"""
End-to-end tests for the cache: real views, real signals, real lua (over fakeredis).

Each test corresponds to a defect found on 2026-07-31. Together they are the proof
that the layer does something rather than silently doing nothing - the state it was
in before, when an unloaded script dict made every operation a swallowed KeyError.
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.tenants.models import Tenant, Branch
from apps.users.models import User
from apps.automations.models import Automation, AutomationTrigger, TriggerType
from tests.utils import CacheTestMixin


class CacheIntegrationTests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Globex", slug="globex")
        self.branch_a = Branch.objects.create(name="Branch A", tenant=self.tenant)
        self.branch_b = Branch.objects.create(name="Branch B", tenant=self.tenant)

        self.super_admin = User.objects.create_user(
            email="sa@test.com", password="password123", role="super_admin"
        )
        self.owner = User.objects.create_user(
            email="owner@test.com", password="password123", role="owner",
            tenant=self.tenant,
        )

    # -- the cache is actually wired up ---------------------------------------

    def test_a_list_request_populates_the_index(self):
        self.client.force_authenticate(user=self.super_admin)

        self.assertEqual(self.redis.exists("tenants/all"), 0)

        response = self.client.get('/api/v1/tenants/tenants/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # If this is empty the layer is a no-op, which is exactly how it shipped.
        self.assertEqual(self.redis.exists("tenants/all"), 1)
        self.assertEqual(len(self.redis.smembers("tenants/all")), 2)

    def test_a_second_list_request_is_served_from_the_cache(self):
        self.client.force_authenticate(user=self.super_admin)
        self.client.get('/api/v1/tenants/tenants/')

        # Delete the rows from the database. A cache hit still answers; a miss would
        # come back empty. This distinguishes "cached" from "coincidentally correct".
        Tenant.objects.all().delete()
        # ...but deleting fires post_delete invalidation, so re-seed the index by hand
        # to keep the assertion about reads, not writes.
        self.redis.sadd("tenants/all", "tenants/probe")
        self.redis.setex("tenants/probe", 300, '{"id": "probe", "name": "from-cache"}')

        response = self.client.get('/api/v1/tenants/tenants/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([t["name"] for t in response.data], ["from-cache"])

    def test_a_detail_request_caches_the_object_but_no_index(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(f'/api/v1/tenants/tenants/{self.tenant.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(self.redis.exists(f"tenants/{self.tenant.id}"), 1)
        # A single object says nothing about the completeness of any list.
        self.assertEqual(self.redis.exists("tenants/all"), 0)

    # -- DESIGN-01: a narrow role must not poison a wide index ------------------

    def test_owner_list_does_not_publish_itself_as_the_global_answer(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/tenants/branchs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # The owner's scoped index is warm...
        self.assertEqual(self.redis.exists(f"branches/tenant:{self.tenant.id}"), 1)
        # ...and super_admin's global index is untouched, so their next request goes
        # to the database instead of getting the owner's narrower result.
        self.assertEqual(self.redis.exists("branches/all"), 0)

    def test_super_admin_sees_every_row_after_an_owner_has_listed(self):
        Branch.objects.create(name="Other Branch", tenant=self.other_tenant)

        self.client.force_authenticate(user=self.owner)
        self.client.get('/api/v1/tenants/branchs/')

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/tenants/branchs/')

        # 2 branches for the owner's tenant + 1 for the other one.
        self.assertEqual(len(response.data), 3)

    # -- DESIGN-02: new rows must show up immediately ---------------------------

    def test_a_newly_created_row_appears_in_the_next_list(self):
        self.client.force_authenticate(user=self.super_admin)

        first = self.client.get('/api/v1/tenants/branchs/')
        self.assertEqual(len(first.data), 2)
        self.assertEqual(self.redis.exists("branches/all"), 1)

        Branch.objects.create(name="Branch C", tenant=self.tenant)

        # post_save invalidation must have dropped the index; otherwise the new row
        # would stay invisible for the whole TTL.
        self.assertEqual(self.redis.exists("branches/all"), 0)

        second = self.client.get('/api/v1/tenants/branchs/')
        self.assertEqual(len(second.data), 3)

    def test_an_updated_row_is_not_served_stale(self):
        self.client.force_authenticate(user=self.super_admin)
        self.client.get(f'/api/v1/tenants/tenants/{self.tenant.id}/')
        self.assertEqual(self.redis.exists(f"tenants/{self.tenant.id}"), 1)

        self.tenant.name = "Acme Renamed"
        self.tenant.save()

        self.assertEqual(self.redis.exists(f"tenants/{self.tenant.id}"), 0)

        response = self.client.get(f'/api/v1/tenants/tenants/{self.tenant.id}/')
        self.assertEqual(response.data['name'], "Acme Renamed")

    def test_a_deleted_row_disappears_from_the_list(self):
        self.client.force_authenticate(user=self.super_admin)
        self.client.get('/api/v1/tenants/branchs/')

        self.branch_b.delete()

        response = self.client.get('/api/v1/tenants/branchs/')
        self.assertEqual([b['name'] for b in response.data], ["Branch A"])

    # -- BUG-04/05: signals must survive every model ----------------------------

    def test_saving_a_model_outside_the_registry_does_not_explode(self):
        # pre_save/post_save are connected without a sender, so they fire for
        # django_celery_beat's models too. check_it_cacheable() used to raise
        # AttributeError on them, which made scheduled automations uncreatable.
        from django_celery_beat.models import CrontabSchedule

        schedule = CrontabSchedule.objects.create(
            minute="0", hour="1", day_of_week="*", day_of_month="*", month_of_year="*",
        )
        schedule.hour = "2"
        schedule.save()

        self.assertEqual(CrontabSchedule.objects.filter(pk=schedule.pk).count(), 1)

    def test_updating_a_tenant_does_not_explode(self):
        # Tenant has no tenant_id column; reading one off it raised AttributeError.
        self.tenant.name = "Renamed"
        self.tenant.save()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Renamed")

    def test_a_scheduled_trigger_can_be_created(self):
        # The end-to-end version of the above: creating this writes a CrontabSchedule
        # and a PeriodicTask through automations' own post_save receiver.
        automation = Automation.objects.create(
            name="Nightly", tenant=self.tenant, branch=self.branch_a,
            allowed_roles=["owner"], metadata={},
        )
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=TriggerType.SCHEDULED,
            config={"cron_expression": "0 1 * * *"},
            initial_data={},
        )
        self.assertIsNotNone(trigger.pk)

    def test_a_child_row_invalidates_its_parent_automation(self):
        automation = Automation.objects.create(
            name="Nightly", tenant=self.tenant, branch=self.branch_a,
            allowed_roles=["owner"], metadata={},
        )
        key = f"automations/{automation.id}"
        self.redis.setex(key, 300, '{"id": "stale"}')

        AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=TriggerType.MANUAL,
            config={},
            initial_data={},
        )

        # AutomationTrigger is not cached itself; it is serialized into its parent,
        # so a change to it has to drop the parent's entry.
        self.assertEqual(self.redis.exists(key), 0)

    # -- role scoping -----------------------------------------------------------

    def test_a_staff_role_user_gets_branch_scope(self):
        # `is_staff` is django's admin flag, not the staff ROLE. Scoping code read
        # `is_branch_manager or is_staff`, so a staff-role user matched neither
        # branch and left the queryset variable unbound -> UnboundLocalError -> 500.
        staff = User.objects.create_user(
            email="staff@test.com", password="password123", role="staff",
            tenant=self.tenant, branch=self.branch_a,
        )
        self.assertFalse(staff.is_staff)
        self.assertTrue(staff.is_staff_role)

        self.client.force_authenticate(user=staff)
        response = self.client.get('/api/v1/tenants/branchs/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([b['name'] for b in response.data], ["Branch A"])
        self.assertEqual(self.redis.exists(f"branches/branch:{self.branch_a.id}"), 1)

    def test_owner_can_update_their_own_tenant(self):
        # get_object() read tenant_id straight off the instance, which is None for a
        # Tenant, so check_tenant_access compared the owner's tenant against None and
        # refused them access to their own tenant on PUT/DELETE.
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/tenants/tenants/{self.tenant.id}/',
            {'name': 'Acme Corp', 'slug': 'acme'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Acme Corp')

    # -- resilience -------------------------------------------------------------

    def test_endpoints_still_work_when_redis_is_unreachable(self):
        import core.cache.service as cache_module
        from core.cache.service import CachingService

        class DeadRedis:
            def __getattr__(self, name):
                def boom(*args, **kwargs):
                    raise ConnectionError("redis is down")
                return boom

        cache_module.cache_service = CachingService(DeadRedis())

        self.client.force_authenticate(user=self.super_admin)
        with self.assertLogs('core.cache.service', level='ERROR'):
            response = self.client.get('/api/v1/tenants/tenants/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
