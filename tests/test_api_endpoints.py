from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.users.models import User
from apps.tenants.models import Tenant, Branch
from unittest.mock import patch
import json
import uuid

class TenantAPIEndpointTests(TestCase):
    
    def setUp(self):
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

        patcher1 = patch('core.cache.service.CachingService.bulk_get', return_value=None)
        patcher2 = patch('core.cache.service.CachingService.get', return_value=None)
        patcher3 = patch('core.tasks.bulk_cache.delay')
        patcher4 = patch('core.tasks.single_cache.delay')
        
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)
        self.addCleanup(patcher4.stop)
        
        self.mock_bulk_get = patcher1.start()
        self.mock_get = patcher2.start()
        self.mock_bulk_delay = patcher3.start()
        self.mock_single_delay = patcher4.start()
        
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

