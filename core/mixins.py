from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from .permissions import check_tenant_access
from rest_framework.utils.encoders import JSONEncoder


import core.cache.service as cache_module
from core.cache import get_cache_key,get_scope_index_key,check_it_cacheable
import json

from .tasks import single_cache,bulk_cache


class RoleBasedAccessMixin:
        """
        Role-scoped access plus the read-through cache for list and detail endpoints.

        NEVER name a method here after one of DRF's APIView hooks. An earlier version
        called this `check_permissions`, which shadowed APIView.check_permissions and
        silently disabled permission_classes on every view using the mixin.

        Two entry points return different things on purpose:
          get_cached_object / get_cached_queryset -> plain dicts, for GET responses
          get_object                              -> a model instance, for PUT/DELETE
        Do not use the cached variants where you need to call .save() or .delete().
        """

        allowed_roles = None
        allowed_roles_for_list = None

        model = None

        detail_serializer = None
        list_serializer = None


        def check_role_access(self,user):
                if self.allowed_roles_for_list and user.role not in self.allowed_roles_for_list:
                        raise PermissionDenied(
                                _("You are not permitted to perform this operation")
                        )


        def get_user_scope(self,user):
                """
                Which slice of the model this user's list request covers.

                Returns (scope, tenant_id, branch_id) where scope is 'all' | 'tenant' |
                'branch', or (None, ...) for a role with no defined visibility. The
                scope is the single source of truth for both the queryset filter and
                the cache index, so the two can never disagree - that disagreement is
                exactly what let a branch manager's narrow result get published as the
                complete answer for super_admin.
                """
                if user.is_super_admin:
                        return ('all', None, None)
                if user.is_owner:
                        return ('tenant', user.tenant_id, None)
                if user.is_branch_manager or user.is_staff_role:
                        return ('branch', getattr(user,'tenant_id',None), user.branch_id)

                return (None, None, None)


        def get_qs_db(self,user,index_key=None):
                model_name = self.model.__name__
                scope, tenant_id, branch_id = self.get_user_scope(user)

                if scope == 'all':
                        qs = self.model.objects.all()
                elif scope == 'tenant':
                        if model_name == 'Tenant':
                                qs = self.model.objects.filter(id=tenant_id)
                        else:
                                qs = self.model.objects.filter(tenant_id=tenant_id)
                elif scope == 'branch':
                        if model_name == 'Branch':
                                qs = self.model.objects.filter(id=branch_id)
                        else:
                                qs = self.model.objects.filter(branch_id=branch_id)
                else:
                        # An unrecognised role used to leave `qs` unbound and raise
                        # UnboundLocalError, which surfaced as an opaque 500.
                        raise PermissionDenied(
                                _("You are not permitted to perform this operation")
                        )

                data = self.list_serializer(qs,many=True).data
                json_data = json.dumps(list(data),cls = JSONEncoder)

                # Cache under the index for THIS scope only, and only when the registry
                # declares one. Empty results are skipped: redis cannot hold an empty
                # set, so caching one would be indistinguishable from a cold miss.
                if index_key and data:
                        bulk_cache.delay(model_name,index_key,json_data)

                return json_data


        def get_obj_db(self,pk,key):
                model_name = self.model.__name__

                obj = get_object_or_404(self.model,id = pk)

                data = self.detail_serializer(obj).data
                json_data = json.dumps(data,cls = JSONEncoder)

                # Object-level cache only. A detail fetch must not add itself to a list
                # index - see the semantics note in core/cache/__init__.py.
                if check_it_cacheable(model_name):
                        single_cache.delay(model_name,key,json_data)

                return json_data


        def get_cached_queryset(self,user):
                model_name = self.model.__name__
                self.check_role_access(user)

                scope, tenant_id, branch_id = self.get_user_scope(user)

                index_key = None
                if check_it_cacheable(model_name):
                        index_key = get_scope_index_key(model_name,scope,tenant_id,branch_id)

                if index_key is None:
                        # Not cacheable, or the registry declares no index for this
                        # scope. Straight to the database - a missing index degrades
                        # into a plain miss instead of a wrong answer.
                        return json.loads(self.get_qs_db(user))

                data = cache_module.cache_service.bulk_get(index_key)

                if data is None:
                        return json.loads(self.get_qs_db(user,index_key=index_key))

                return [json.loads(item) for item in data]


        def get_cached_object(self,user,pk):
                model_name = self.model.__name__
                self.check_role_access(user)

                prefix = get_cache_key(model_name)
                obj = None

                if prefix and check_it_cacheable(model_name):
                        cache_key = f"{prefix}{pk}"
                        obj = cache_module.cache_service.get(cache_key)
                else:
                        cache_key = None

                if obj is None:
                        obj = self.get_obj_db(pk,cache_key)

                obj_dict = json.loads(obj)

                # The serialized payload has no model properties, so the scope has to
                # be read out of the dict. Mirrors BaseModel.get_scope_ids().
                tenant_id = obj_dict.get('tenant')
                branch_id = obj_dict.get('branch')

                if model_name == 'Tenant':
                        tenant_id = obj_dict.get('id')
                elif model_name == 'Branch':
                        branch_id = obj_dict.get('id')

                allowed , msg = check_tenant_access(user ,tenant_id,branch_id)

                if not allowed:
                        raise PermissionDenied(
                                _(msg)
                        )

                return obj_dict


        def get_object(self, user, pk):
                """A real model instance, for the write paths (PUT / DELETE)."""
                self.check_role_access(user)
                obj = get_object_or_404(self.model, id=pk)

                # Same scope resolution as the cache path. Reading tenant_id straight
                # off the instance returned None for a Tenant, so check_tenant_access
                # compared the owner's tenant against None and refused them access to
                # their own tenant on PUT and DELETE.
                tenant_id, branch_id = obj.get_scope_ids()

                allowed, msg = check_tenant_access(user, tenant_id, branch_id)
                if not allowed:
                        raise PermissionDenied(_(msg))

                return obj
