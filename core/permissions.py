from rest_framework.permissions import BasePermission
from django.utils.translation import gettext_lazy as _

# class ExtendedPermission(BasePermission):

#         # If returns true , it passes, else returns 403 Forbidden.

#         #It is checked when the user requests the endpoint ,"Whether the user has the permission to the reach the endpoint"
#         # api/v1/auth/register
#         def has_permission(self, request, view):
#                 if (request.user.is_super_admin):
#                         return True
#                 if (request.user.is_owner):
#                         pass
#                 if (request.user.is_branch_manager):
#                         pass
#                 if (request.user.is_staff):
#                         pass
        
#         # It is checked when user wants a specific obj , like api/prod/<uuid>
#         def has_object_permission(self, request, view, obj):
#                 if (request.user.is_super_admin):
#                         pass
#                 if (request.user.is_owner):
#                         pass
#                 if (request.user.is_branch_manager):
#                         pass
#                 if (request.user.is_staff):
#                         pass


class IsSameTenant(BasePermission):

        #That function checks whether that object's tenant and the user has same tenant ,
        # It is a bit of rough filter, but , as merging many of these permissions bring a distributed permission system.
        
        # def has_object_permission(self, request, view, obj):
                
        #         return obj.tenant == request.user.tenant

        # But the problem in here is an object might not have the tenant object directly:
        # How we gonna solve it is "Convention".
        # It is a kind of rule that Every object of project directly or indirectly must have tenant object
        # For instance , an "automation" object might not have tenant but its branch object have,
        # So , each model must have an get_tenant() method, that is written specially for that object,and 
        # knows the way how to reach tenant , In example , automation.get_tenant() finds the tenant from its branch object.
        
        # By the new way, the our function must be like :

        def has_permission(self, request, view):
                return request.user.is_authenticated # is_authenticated is an property not method so it doesnt take paranthesis ()


        def has_object_permission(self, request, view, obj):
                return obj.get_tenant() == request.user.tenant


class IsSuperAdmin(BasePermission):
        def has_permission(self, request, view):
                return request.user.is_authenticated and request.user.is_super_admin 

class IsOwner(BasePermission):
        def has_permission(self, request, view):
                return request.user.is_authenticated and request.user.is_owner

class IsSuperAdminOrOwner(BasePermission):
        def has_permission(self, request, view):
                return request.user.is_authenticated and (request.user.is_super_admin or request.user.is_owner)



def _as_id(value):
        """
        Normalise a scope argument to a comparable string.

        Callers reach this function from three directions and each passes something
        different: the mixin passes a UUID, the cached path passes a string out of
        JSON, and serializers pass a whole model instance because
        PrimaryKeyRelatedField resolves to the object. The instance case used to be
        compared via str(), which yields Tenant.__str__ (the NAME) - so it never
        matched a uuid and owners could not create a channel, automation or template
        in their own tenant.
        """
        if value is None:
                return None

        return str(getattr(value,'pk',value))


def check_tenant_access(user,tenant_id,branch_id=None):
        """
                Returns (True, None) if access granted
                Returns (False, "message") if access denied

                tenant_id / branch_id may each be a model instance, a UUID or a string.
        """

        if user.is_super_admin:
                return (True,None)

        tenant_id = _as_id(tenant_id)
        branch_id = _as_id(branch_id)

        if user.tenant is None or str(user.tenant.id) != tenant_id:
                return (False,_("You can't access another tenant"))



        if user.is_owner:
                return (True,None)
        
        if user.is_branch_manager or user.is_staff_role:
                if user.branch is None or str(user.branch.id) != branch_id:
                        return (False,_("You can't access that branch "))

                return (True,None)
                
                
        return (False,_("Access Denied"))