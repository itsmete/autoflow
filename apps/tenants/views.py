from rest_framework.views import APIView,Response,status
from .serializers import BranchSerializer,TenantSerializer
# from core.tokens import TokenObtainPairSerializer,ExtendedAccessToken
from rest_framework.permissions import IsAuthenticated
from core.permissions import IsSameTenant,IsSuperAdmin, IsOwner,IsSuperAdminOrOwner
from django.shortcuts import get_object_or_404
from .models import Tenant,Branch
from django.utils.translation import gettext_lazy as _ 

from rest_framework.exceptions import PermissionDenied,NotFound
from core.mixins import RoleBasedAccessMixin

        
class TenantListCreateView(RoleBasedAccessMixin, APIView):
        model = Tenant
        list_serializer = TenantSerializer
        permission_classes = [IsSuperAdmin]

        def get(self,request):
                data = self.get_cached_queryset(request.user)

                return Response(data = data ,status=status.HTTP_200_OK)
        

        def post(self,request): 
                serializer = TenantSerializer(data = request.data)

                serializer.is_valid(raise_exception=True)

                tenant = serializer.save()

                return Response(data=serializer.data,status=status.HTTP_201_CREATED)
                

class TenantDetailView(RoleBasedAccessMixin, APIView):
        model = Tenant
        detail_serializer = TenantSerializer
        permission_classes = [IsSuperAdminOrOwner]

        
        def get(self,request,pk):
                data = self.get_cached_object(request.user, pk)
                return Response(data=data, status=status.HTTP_200_OK)
        

        def put(self,request,pk):

                instance = self.get_object(request.user, pk)
                serializer = TenantSerializer(instance = instance , data =request.data)

                serializer.is_valid(raise_exception=True)

                serializer.save()

                return Response(data= serializer.data, status=status.HTTP_200_OK)
        
        def delete(self,request,pk):
                obj = self.get_object(request.user, pk)

                obj.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)



"""
        Superuser can both change and list everything.
        Owner can only list and change its own branchs
        Branch Manager can only see and change its branch

"""

class BranchListView(RoleBasedAccessMixin, APIView):
        model = Branch
        list_serializer = BranchSerializer
        permission_classes = [IsAuthenticated]

        def _get_queryset(self,request):
                user = request.user

                if user.is_super_admin:
                        return Branch.objects.all()
                if user.is_owner:
                        return Branch.objects.filter(tenant = user.tenant)
                if user.is_branch_manager:
                        return Branch.objects.filter(id = user.branch.id)
        
        def get(self,request):
                data = self.get_cached_queryset(request.user)
                return Response(data=data, status=status.HTTP_200_OK)


        #Creation of branch
        #Only SuperUser or owners can add a branch
        def post(self,request):

                if not (request.user.is_super_admin or request.user.is_owner):
                        raise PermissionDenied()
                
                
                serializer = BranchSerializer(data = request.data)
                serializer.is_valid(raise_exception=True)

                serializer.save(tenant = request.user.tenant)

                return Response(data=serializer.data , status=status.HTTP_201_CREATED)




class BranchDetailView(RoleBasedAccessMixin, APIView):
        model = Branch
        detail_serializer = BranchSerializer
        permission_classes = [IsAuthenticated]

        def _get_obj(self,request,pk):
                obj = get_object_or_404(Branch, id = pk)
                user = request.user

                if (user.is_super_admin):
                        return obj

                if (user.is_owner):
                        tenant_obj = getattr(obj, 'tenant', None)
                        if tenant_obj and user.tenant == tenant_obj:
                                return obj
                        else:
                                raise PermissionDenied()

                        
                if (user.is_branch_manager):
                        if ( user.branch == obj ):
                                return obj
                        else:
                                # return Response(status=status.HTTP_403_FORBIDDEN)
                                raise PermissionDenied()
                else:
                                raise PermissionDenied()


                


        def get(self,request,pk):
                self._get_obj(request, pk) # Still do manual checks since get_cached_object permission check might not cover complex branch rules
                data = self.get_cached_object(request.user, pk)
                return Response(data = data, status=status.HTTP_200_OK)


        def put(self,request,pk):
                instance = self.get_object(request.user, pk)
                self._get_obj(request, pk) # Check permission
                serializer = BranchSerializer(instance = instance , data = request.data)
                serializer.is_valid(raise_exception=True)
                serializer.save()

                return Response(data= serializer.data , status=status.HTTP_200_OK)



        def delete(self,request,pk):
                if (request.user.is_branch_manager):
                        return Response(data = {'detail':_('Branch managers can not delete branchs')},status=status.HTTP_403_FORBIDDEN)
        
                obj = self.get_object(request.user, pk)
                self._get_obj(request, pk) # Check permission
                obj.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)