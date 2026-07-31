"""
core/ unit tests: roles, permissions, BaseModel, tokens, pagination, exception handler.

These are the primitives every app is built on, so a defect here is never local -
the dict.get() TypeError in core/roles.py took out every /api/v1/users/ endpoint.
"""
import json
import logging

from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory

from core.exceptions import extended_exception_handler
from core.pagination import StandartPagination
from core.permissions import (
    IsOwner,
    IsSameTenant,
    IsSuperAdmin,
    IsSuperAdminOrOwner,
    check_tenant_access,
)
from core.roles import ROLE_WEIGHTS, can_manage, get_visible_roles, get_weight
from core.tokens import ExtendedAccessToken, ExtendedRefreshToken
from tests import factories


class RoleWeightTests(TestCase):

    def test_known_roles_have_the_documented_order(self):
        self.assertEqual(
            [r for r, _w in sorted(ROLE_WEIGHTS.items(), key=lambda kv: -kv[1])],
            ["super_admin", "owner", "branch_manager", "staff"],
        )

    def test_get_weight_of_a_known_role(self):
        self.assertEqual(get_weight("owner"), 3)

    def test_get_weight_of_an_unknown_role_is_zero_not_an_error(self):
        # Regression: this was written as ROLE_WEIGHTS.get(role, default=0), and
        # dict.get takes no keyword arguments. Every call raised TypeError, which
        # took out every /api/v1/users/ endpoint.
        self.assertEqual(get_weight("nonsense"), 0)
        self.assertEqual(get_weight(None), 0)

    def test_can_manage_is_strictly_greater(self):
        self.assertTrue(can_manage("owner", "staff"))
        self.assertFalse(can_manage("owner", "owner"))
        self.assertFalse(can_manage("staff", "owner"))

    def test_visible_roles_are_everything_strictly_below(self):
        self.assertEqual(sorted(get_visible_roles("owner")), ["branch_manager", "staff"])
        self.assertEqual(get_visible_roles("staff"), [])
        self.assertEqual(
            sorted(get_visible_roles("super_admin")),
            ["branch_manager", "owner", "staff"],
        )

    def test_visible_roles_of_an_unknown_role_is_empty(self):
        self.assertEqual(get_visible_roles("nonsense"), [])


class CheckTenantAccessTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant, "A")
        self.other_branch = factories.make_branch(self.tenant, "B")

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)
        self.staff = factories.make_staff(self.tenant, self.branch)

    def test_super_admin_may_reach_anything(self):
        allowed, msg = check_tenant_access(self.super_admin, self.other_tenant.id, None)
        self.assertTrue(allowed)
        self.assertIsNone(msg)

    def test_owner_may_reach_their_own_tenant(self):
        allowed, _msg = check_tenant_access(self.owner, self.tenant.id, self.branch.id)
        self.assertTrue(allowed)

    def test_owner_may_not_reach_another_tenant(self):
        allowed, msg = check_tenant_access(self.owner, self.other_tenant.id, None)
        self.assertFalse(allowed)
        self.assertIn("tenant", str(msg).lower())

    def test_branch_manager_is_confined_to_their_branch(self):
        self.assertTrue(check_tenant_access(self.manager, self.tenant.id, self.branch.id)[0])
        self.assertFalse(
            check_tenant_access(self.manager, self.tenant.id, self.other_branch.id)[0]
        )

    def test_staff_role_is_branch_scoped_too(self):
        # `is_staff` is django's admin flag and is False for the staff ROLE; the
        # check used to read it and fell through to a blanket deny.
        self.assertFalse(self.staff.is_staff)
        self.assertTrue(self.staff.is_staff_role)
        self.assertTrue(check_tenant_access(self.staff, self.tenant.id, self.branch.id)[0])
        self.assertFalse(
            check_tenant_access(self.staff, self.tenant.id, self.other_branch.id)[0]
        )

    def test_uuid_and_string_ids_compare_equal(self):
        # The caller may pass a UUID (model instance) or a str (json payload).
        as_str = check_tenant_access(self.owner, str(self.tenant.id), None)
        as_uuid = check_tenant_access(self.owner, self.tenant.id, None)
        self.assertEqual(as_str[0], as_uuid[0])
        self.assertTrue(as_str[0])

    def test_a_model_instance_is_accepted_as_well_as_an_id(self):
        # Serializers pass the resolved objects, because PrimaryKeyRelatedField
        # hands back instances. str(Tenant) is its NAME, so comparing that against a
        # uuid always failed and owners could not create anything in their own tenant.
        self.assertTrue(check_tenant_access(self.owner, self.tenant)[0])
        self.assertTrue(check_tenant_access(self.manager, self.tenant, self.branch)[0])
        self.assertFalse(check_tenant_access(self.owner, self.other_tenant)[0])

    def test_a_user_with_no_tenant_is_denied_rather_than_crashing(self):
        orphan = factories.make_user("owner", tenant=None)
        allowed, _msg = check_tenant_access(orphan, self.tenant.id, None)
        self.assertFalse(allowed)

    def test_a_branch_scoped_user_with_no_branch_is_denied(self):
        orphan = factories.make_user("branch_manager", tenant=self.tenant, branch=None)
        allowed, _msg = check_tenant_access(orphan, self.tenant.id, self.branch.id)
        self.assertFalse(allowed)


class PermissionClassTests(TestCase):

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.staff = factories.make_staff(self.tenant, self.branch)

    def _request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_is_super_admin(self):
        self.assertTrue(IsSuperAdmin().has_permission(self._request(self.super_admin), None))
        self.assertFalse(IsSuperAdmin().has_permission(self._request(self.owner), None))

    def test_is_owner(self):
        self.assertTrue(IsOwner().has_permission(self._request(self.owner), None))
        self.assertFalse(IsOwner().has_permission(self._request(self.staff), None))

    def test_is_super_admin_or_owner(self):
        perm = IsSuperAdminOrOwner()
        self.assertTrue(perm.has_permission(self._request(self.super_admin), None))
        self.assertTrue(perm.has_permission(self._request(self.owner), None))
        self.assertFalse(perm.has_permission(self._request(self.staff), None))

    def test_is_same_tenant_object_permission(self):
        automation = factories.make_automation(self.tenant, self.branch)
        perm = IsSameTenant()
        self.assertTrue(
            perm.has_object_permission(self._request(self.owner), None, automation)
        )

        other_tenant = factories.make_tenant(name="Globex")
        foreign = factories.make_automation(other_tenant)
        self.assertFalse(
            perm.has_object_permission(self._request(self.owner), None, foreign)
        )


class BaseModelTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)

    def test_pk_is_populated_before_the_row_exists(self):
        # This is why `if not instance.pk` cannot be used to detect a create.
        from apps.tenants.models import Tenant

        unsaved = Tenant(name="Fresh", slug="fresh")
        self.assertIsNotNone(unsaved.pk)
        self.assertTrue(unsaved._state.adding)

    def test_get_tenant_climbs_through_branch(self):
        automation = factories.make_automation(self.tenant, self.branch)
        self.assertEqual(automation.get_tenant(), self.tenant)

        branch_only = factories.make_automation(None, self.branch)
        self.assertEqual(branch_only.get_tenant(), self.tenant)

    def test_get_tenant_is_none_when_unscoped(self):
        unscoped = factories.make_automation(None, None)
        self.assertIsNone(unscoped.get_tenant())

    def test_get_scope_ids_on_a_normal_model(self):
        automation = factories.make_automation(self.tenant, self.branch)
        self.assertEqual(
            automation.get_scope_ids(), (self.tenant.id, self.branch.id)
        )

    def test_tenant_is_its_own_tenant_scope(self):
        # Tenant has no tenant_id column; reading one crashed every Tenant update.
        self.assertEqual(self.tenant.get_scope_ids(), (self.tenant.id, None))

    def test_branch_is_its_own_branch_scope(self):
        self.assertEqual(self.branch.get_scope_ids(), (self.tenant.id, self.branch.id))

    def test_is_active_defaults_to_true(self):
        self.assertTrue(self.tenant.is_active)


class TokenTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.branch = factories.make_branch(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)

    def test_access_token_carries_the_scoping_claims(self):
        token = ExtendedAccessToken.for_user(self.manager)
        self.assertEqual(token["user_id"], str(self.manager.id))
        self.assertEqual(token["role"], "branch_manager")
        self.assertEqual(token["email"], self.manager.email)
        self.assertEqual(token["tenant_id"], str(self.tenant.id))
        self.assertEqual(token["branch_id"], str(self.branch.id))

    def test_claims_are_null_for_an_unscoped_user(self):
        sa = factories.make_super_admin()
        token = ExtendedAccessToken.for_user(sa)
        self.assertIsNone(token["tenant_id"])
        self.assertIsNone(token["branch_id"])

    def test_refresh_token_mints_extended_access_tokens(self):
        refresh = ExtendedRefreshToken.for_user(self.manager)
        access = refresh.access_token
        self.assertEqual(access["role"], "branch_manager")
        self.assertEqual(access["tenant_id"], str(self.tenant.id))


class PaginationTests(TestCase):

    def test_response_shape(self):
        from apps.tenants.models import Tenant

        for i in range(25):
            factories.make_tenant(name=f"T{i}")

        paginator = StandartPagination()
        request = APIRequestFactory().get("/", {"page": "1"})
        from rest_framework.request import Request

        page = paginator.paginate_queryset(Tenant.objects.all().order_by("name"), Request(request))
        response = paginator.get_paginated_response([{"stub": True}])

        self.assertEqual(len(page), 20)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertEqual(response.data["results"], [{"stub": True}])

    def test_page_size_is_capped(self):
        paginator = StandartPagination()
        self.assertEqual(paginator.max_page_size, 100)
        self.assertEqual(paginator.page_size_query_param, "page_size")


class ExceptionHandlerTests(TestCase):

    def setUp(self):
        self.context = {"view": None, "request": RequestFactory().get("/")}

    def test_api_exceptions_are_wrapped_in_the_standard_envelope(self):
        response = extended_exception_handler(PermissionDenied("nope"), self.context)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertEqual(response.data["error"]["message"], "nope")

    def test_not_found_maps_to_its_code(self):
        response = extended_exception_handler(NotFound(), self.context)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "not_found")

    def test_field_errors_land_in_the_fields_bucket(self):
        response = extended_exception_handler(
            ValidationError({"email": ["This field is required."]}), self.context
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["message"], "Validation Error")
        self.assertIn("email", response.data["error"]["fields"])

    def test_unhandled_exceptions_are_logged_before_becoming_a_500(self):
        # Without this log the traceback is lost and every defect looks identical -
        # which is how a TypeError in core/roles.py survived unnoticed for months.
        with self.assertLogs("core.exceptions", level="ERROR") as captured:
            response = extended_exception_handler(RuntimeError("boom"), self.context)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(response.data["success"])
        self.assertIn("boom", "\n".join(captured.output))
