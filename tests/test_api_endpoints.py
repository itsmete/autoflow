from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.users.models import User
from apps.tenants.models import Tenant, Branch
from tests.utils import CacheTestMixin

class TenantAPIEndpointTests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.branch = Branch.objects.create(name="Test Branch", tenant=self.tenant)
        self.other_branch = Branch.objects.create(name="Other Branch", tenant=self.other_tenant)
        
        self.super_admin = User.objects.create_user(
            email="admin@test.com", password="pass", role="super_admin"
        )
        self.owner = User.objects.create_user(
            email="owner@test.com", password="pass", role="owner", tenant=self.tenant
        )
        self.branch_manager = User.objects.create_user(
            email="manager@test.com", password="pass", role="branch_manager", 
            tenant=self.tenant, branch=self.branch
        )

        # The cache is NOT mocked out any more - CacheTestMixin installs a real
        # CachingService over fakeredis. These endpoints used to pass with the cache
        # stubbed to None, which is precisely how a broken CELERY_RESULT_BACKEND and
        # an index-set write that never happened both went unnoticed.

    def test_super_admin_can_list_tenants(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/tenants/tenants/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        
    def test_owner_cannot_list_tenants(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/tenants/tenants/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
    def test_owner_can_list_own_branches(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/tenants/branchs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Test Branch")

    def test_branch_manager_can_read_own_branch(self):
        self.client.force_authenticate(user=self.branch_manager)
        response = self.client.get(f'/api/v1/tenants/branchs/{self.branch.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Test Branch")

    def test_branch_manager_cannot_read_other_branch(self):
        self.client.force_authenticate(user=self.branch_manager)
        response = self.client.get(f'/api/v1/tenants/branchs/{self.other_branch.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

