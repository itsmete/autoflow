from rest_framework.permissions import BasePermission


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

