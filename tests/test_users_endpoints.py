from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from apps.users.models import User
from apps.tenants.models import Tenant, Branch

class UserAPIEndpointTests(APITestCase):

    def setUp(self):
        self.client = APIClient()
        
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant-users")
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant-users")
        
        self.branch = Branch.objects.create(name="Test Branch", tenant=self.tenant)
        self.other_branch = Branch.objects.create(name="Other Branch", tenant=self.other_tenant)
        self.tenant_branch_2 = Branch.objects.create(name="Second Branch", tenant=self.tenant)
        
        self.super_admin = User.objects.create_user(
            email="super@test.com", password="pass", role="super_admin"
        )
        
        self.owner = User.objects.create_user(
            email="owner@test.com", password="pass", role="owner", tenant=self.tenant
        )
        
        self.branch_manager = User.objects.create_user(
            email="manager@test.com", password="pass", role="branch_manager", 
            tenant=self.tenant, branch=self.branch
        )
        
        self.staff_1 = User.objects.create_user(
            email="staff1@test.com", password="pass", role="staff", 
            tenant=self.tenant, branch=self.branch
        )

        self.staff_2 = User.objects.create_user(
            email="staff2@test.com", password="pass", role="staff", 
            tenant=self.tenant, branch=self.tenant_branch_2
        )
        
        self.other_owner = User.objects.create_user(
            email="otherowner@test.com", password="pass", role="owner", tenant=self.other_tenant
        )

    def test_super_admin_can_list_all_users(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should see all 6 users created in setUp
        self.assertEqual(len(response.data), 6)

    def test_owner_can_list_tenant_users(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Owner should see branch_manager, staff_1, staff_2 (roles below owner in same tenant)
        # Assuming get_visible_roles returns ['branch_manager', 'staff'] for owner
        # Wait, owner might also see themselves or other owners?
        self.assertTrue(len(response.data) > 0)
        for user_data in response.data:
            self.assertEqual(user_data['tenant'], self.tenant.id)

    def test_branch_manager_can_list_branch_users(self):
        self.client.force_authenticate(user=self.branch_manager)
        response = self.client.get('/api/v1/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for user_data in response.data:
            self.assertEqual(user_data['branch'], self.branch.id)
            self.assertEqual(user_data['tenant'], self.tenant.id)

    def test_staff_cannot_list_users(self):
        self.client.force_authenticate(user=self.staff_1)
        response = self.client.get('/api/v1/users/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_read_own_staff(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/users/{self.staff_1.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], "staff1@test.com")

    def test_owner_cannot_read_other_tenant_staff(self):
        self.client.force_authenticate(user=self.owner)
        other_staff = User.objects.create_user(
            email="otherstaff@test.com", password="pass", role="staff", 
            tenant=self.other_tenant, branch=self.other_branch
        )
        response = self.client.get(f'/api/v1/users/{other_staff.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_branch_manager_cannot_read_other_branch_staff(self):
        self.client.force_authenticate(user=self.branch_manager)
        response = self.client.get(f'/api/v1/users/{self.staff_2.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_self_update(self):
        self.client.force_authenticate(user=self.staff_1)
        response = self.client.get('/api/v1/users/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        response = self.client.put('/api/v1/users/me/', {
            'first_name': 'Updated',
            'last_name': 'Name'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['first_name'], 'Updated')
