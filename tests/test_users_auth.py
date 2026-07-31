"""
apps/users: login, registration, admin-side edits and deletion.

Registration is where the role hierarchy is actually enforced on write, so most of
these assert who may create or promote whom.
"""
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.users.models import User
from tests import factories
from tests.utils import CacheTestMixin


class LoginTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.user = factories.make_branch_manager(
            self.tenant, self.branch, email="login@test.com"
        )

    def test_valid_credentials_return_both_tokens(self):
        response = self.client.post(
            '/api/v1/auth/login/',
            {"email": "login@test.com", "password": factories.PASSWORD},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_the_access_token_carries_the_scope_claims(self):
        # RefreshToken.access_token copies claims off the refresh payload rather than
        # calling ExtendedAccessToken.for_user, so setting access_token_class alone
        # produced tokens with no role/tenant/branch at all.
        response = self.client.post(
            '/api/v1/auth/login/',
            {"email": "login@test.com", "password": factories.PASSWORD},
            format='json',
        )
        token = AccessToken(response.data["access"])
        self.assertEqual(token["role"], "branch_manager")
        self.assertEqual(token["email"], "login@test.com")
        self.assertEqual(token["tenant_id"], str(self.tenant.id))
        self.assertEqual(token["branch_id"], str(self.branch.id))

    def test_a_wrong_password_is_rejected(self):
        response = self.client.post(
            '/api/v1/auth/login/',
            {"email": "login@test.com", "password": "wrongpassword"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_email_is_rejected(self):
        response = self.client.post(
            '/api/v1/auth/login/',
            {"email": "nobody@test.com", "password": factories.PASSWORD},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_bearer_token_authenticates_a_request(self):
        login = self.client.post(
            '/api/v1/auth/login/',
            {"email": "login@test.com", "password": factories.PASSWORD},
            format='json',
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {login.data["access"]}')
        response = client.get('/api/v1/users/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "login@test.com")


class RegistrationTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant)
        self.other_branch = factories.make_branch(self.other_tenant)

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)

    def _payload(self, **overrides):
        payload = {
            "email": "new@test.com",
            "password": factories.PASSWORD,
            "first_name": "New",
            "last_name": "User",
            "role": "staff",
            "branch": str(self.branch.id),
        }
        payload.update(overrides)
        return payload

    def test_anonymous_registration_is_refused(self):
        response = self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_owner_can_create_staff_in_their_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        created = User.objects.get(email="new@test.com")
        self.assertEqual(created.tenant, self.tenant)
        self.assertEqual(created.role, "staff")

    def test_the_password_is_stored_hashed(self):
        self.client.force_authenticate(user=self.owner)
        self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        created = User.objects.get(email="new@test.com")
        self.assertNotEqual(created.password, factories.PASSWORD)
        self.assertTrue(created.check_password(factories.PASSWORD))

    def test_owner_may_not_mint_another_owner(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/auth/register/', self._payload(role="owner"), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_may_not_mint_a_super_admin(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/auth/register/', self._payload(role="super_admin"), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_may_not_attach_a_branch_from_another_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/auth/register/',
            self._payload(branch=str(self.other_branch.id)),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_branch_manager_may_not_register_anyone(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_super_admin_may_assign_any_role(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.post(
            '/api/v1/auth/register/', self._payload(role="owner"), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_registering_a_super_admin_sets_the_django_admin_flag(self):
        self.client.force_authenticate(user=self.super_admin)
        self.client.post(
            '/api/v1/auth/register/', self._payload(role="super_admin"), format='json'
        )
        created = User.objects.get(email="new@test.com")
        self.assertTrue(created.is_staff)
        self.assertFalse(created.is_staff_role)

    def test_a_duplicate_email_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        response = self.client.post('/api/v1/auth/register/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_short_password_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/auth/register/', self._payload(password="short"), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AdminUpdateTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant)

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.staff = factories.make_staff(self.tenant, self.branch)
        self.foreign_staff = factories.make_staff(
            self.other_tenant, factories.make_branch(self.other_tenant)
        )

    def test_owner_can_deactivate_their_own_staff(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/users/{self.staff.id}/update/',
            {"email": self.staff.email, "first_name": "N", "last_name": "S",
             "role": "staff", "is_active": False},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)

    def test_owner_cannot_touch_another_tenants_user(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/users/{self.foreign_staff.id}/update/',
            {"email": self.foreign_staff.email, "first_name": "N", "last_name": "S",
             "role": "staff", "is_active": False},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_nobody_may_promote_to_their_own_level(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/users/{self.staff.id}/update/',
            {"email": self.staff.email, "first_name": "N", "last_name": "S",
             "role": "owner", "is_active": True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_may_not_edit_anyone(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            f'/api/v1/users/{self.staff.id}/update/',
            {"email": self.staff.email, "first_name": "N", "last_name": "S",
             "role": "staff", "is_active": True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class UserDeleteTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant)

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.staff = factories.make_staff(self.tenant, self.branch)
        self.foreign_staff = factories.make_staff(
            self.other_tenant, factories.make_branch(self.other_tenant)
        )

    def test_owner_can_delete_their_own_staff(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(f'/api/v1/users/{self.staff.id}/delete/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.staff.pk).exists())

    def test_owner_cannot_delete_another_tenants_user(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(f'/api/v1/users/{self.foreign_staff.id}/delete/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.foreign_staff.pk).exists())

    def test_staff_cannot_delete_anyone(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.delete(f'/api/v1/users/{self.staff.id}/delete/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_delete_across_tenants(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.delete(f'/api/v1/users/{self.foreign_staff.id}/delete/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class TenantBranchAPITests(CacheTestMixin, TestCase):
    """Coverage for the tenant/branch endpoints beyond the cache-focused tests."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant, "A")

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)

    def test_super_admin_can_create_a_tenant(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.post(
            '/api/v1/tenants/tenants/', {"name": "New Co", "slug": "new-co"}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_owner_cannot_create_a_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/tenants/tenants/', {"name": "New Co", "slug": "new-co"}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_rename_their_own_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/tenants/tenants/{self.tenant.id}/', {"name": "Renamed"}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Renamed")

    def test_owner_cannot_rename_another_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            f'/api/v1/tenants/tenants/{self.other_tenant.id}/',
            {"name": "Hijacked"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_create_a_branch_in_their_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/tenants/branchs/', {"name": "B"}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(self.tenant.branches.count(), 2)

    def test_a_branch_manager_cannot_create_a_branch(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            '/api/v1/tenants/branchs/', {"name": "B"}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_branch_manager_cannot_delete_their_branch(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.delete(f'/api/v1/tenants/branchs/{self.branch.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_delete_a_branch(self):
        self.client.force_authenticate(user=self.owner)
        extra = factories.make_branch(self.tenant, "Doomed")
        response = self.client.delete(f'/api/v1/tenants/branchs/{extra.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_anonymous_access_is_refused(self):
        for url in ('/api/v1/tenants/tenants/', '/api/v1/tenants/branchs/'):
            response = self.client.get(url)
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                url,
            )
