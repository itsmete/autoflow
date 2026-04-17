from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from .permissions import check_tenant_access
from rest_framework.utils.encoders import JSONEncoder


import core.cache.service as cache_module
from core.cache import get_cache_key,get_idx_keys,check_it_cacheable,resolve_idx_keys
import json

from .tasks import single_cache,bulk_cache

class RoleBasedAccessMixin:

        allowed_roles = None
        allowed_roles_for_list = None

        model = None

        detail_serializer = None
        list_serializer = None
        

        def check_permissions(self,user):
                if self.allowed_roles_for_list and user.role not in self.allowed_roles_for_list:
                        raise PermissionDenied(
                                _("You are not permitted to perform this operation")
                        )

        def get_qs_db(self,user):
                if user.is_super_admin:
                        qs =  self.model.objects.all()
                
                elif user.is_owner:
                        qs =  self.model.objects.filter(
                                tenant = user.tenant
                        )
                elif user.is_branch_manager or user.is_staff:
                        qs =  self.model.objects.filter(
                                branch = user.branch
                        )

                data = self.list_serializer(qs,many=True).data
                json_data = json.dumps(list(data),cls = JSONEncoder)

                bulk_cache.delay(self.model.__name__,json_data)

                return json_data



        def get_obj_db(self,pk,key):
                obj =  get_object_or_404(self.model,id = pk)
                
                data = self.detail_serializer(obj).data
                json_data = json.dumps(data,cls = JSONEncoder)

                idxs = resolve_idx_keys(self.model.__name__,obj.tenant_id,obj.branch_id)
                
                single_cache.delay(self.model.__name__,key,json_data,idxs)
                
                return json_data




        def get_queryset(self,user):
                model_name = self.model.__name__
                self.check_permissions(user)

                if not check_it_cacheable(model_name):
                        return json.loads(self.get_qs_db(user))

                        


                cache_key = None

                if user.is_super_admin:
                        cache_key = f"{get_cache_key(model_name)}all"
                
                elif user.is_owner:
                        cache_key = f"{get_cache_key(model_name)}tenant:{user.tenant.id}"
                        
                elif user.is_branch_manager or user.is_staff:
                        cache_key = f"{get_cache_key(model_name)}branch:{user.branch.id}"
                        
                data = cache_module.cache_service.bulk_get(cache_key)

                if data is None:
                        return json.loads(self.get_qs_db(user))

                return [json.loads(item) for item in data]
                
        
        
        def get_object(self,user,pk):
                self.check_permissions(user)
                
                cache_key = f"{get_cache_key(self.model.__name__)}{pk}"

                obj = cache_module.cache_service.get(cache_key)
                
                if obj is None:
                        obj = self.get_obj_db(pk,cache_key)

                
                obj_dict = json.loads(obj)
                tenant_id = obj_dict.get('tenant')
                branch_id = obj_dict.get('branch')
                

                # !!!!!!!!!! -> JSON serialized obj is not have properties. Will be handled. WIP.
                allowed , msg = check_tenant_access(user ,tenant_id,branch_id)
                
                
                if not allowed:
                        raise PermissionDenied(
                                _(msg)
                
                
                        )

                return obj_dict
                        
                

