from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from .permissions import check_tenant_access

class RoleBasedAccessMixin:

        allowed_roles = None
        model = None

        def check_permissions(self,user):
                if self.allowed_roles_for_list and user.role not in self.allowed_roles_for_list:
                        raise PermissionDenied(
                                _("You are not permitted to perform this operation")
                        )


        def get_queryset(self,user):

                self.check_permissions(user)

                if user.is_super_admin:
                        return self.model.objects.all()
                
                elif user.is_owner:
                        return self.model.objects.filter(
                                tenant = user.tenant
                        )
                elif user.is_branch_manager or user.is_staff:
                        return self.model.objects.filter(
                                branch = user.branch
                        )
                

        
        def get_object(self,user,pk):
                self.check_permissions(user)
                
                obj = get_object_or_404(self.model,id = pk)
                allowed , msg = check_tenant_access(user ,obj.tenant,obj.branch )
                if not allowed:
                        raise PermissionDenied(
                                _(msg)
                        )

                return obj
                        
                

