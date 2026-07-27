from rest_framework.views import APIView,Response,status
from .serializers import BranchSerializer,TenantSerializer
# from core.tokens import TokenObtainPairSerializer,ExtendedAccessToken
from rest_framework.permissions import IsAuthenticated
from core.permissions import IsSameTenant,IsSuperAdmin, IsOwner,IsSuperAdminOrOwner
from django.shortcuts import get_object_or_404
from .models import Tenant,Branch
from django.utils.translation import gettext_lazy as _ 

from rest_framework.exceptions import PermissionDenied,NotFound


        
class TenantListCreateView(APIView):
        permission_classes = [IsSuperAdmin]

        def get(self,request):
                tenants = Tenant.objects.all()
                data = TenantSerializer(tenants,many=True).data

                return Response(data = data ,status=status.HTTP_200_OK)
        

        def post(self,request): 
                serializer = TenantSerializer(data = request.data)

                serializer.is_valid(raise_exception=True)

                tenant = serializer.save()

                return Response(data=serializer.data,status=status.HTTP_201_CREATED)
                

class TenantDetailView(APIView):
        permission_classes = [IsSuperAdminOrOwner]

        
        def get(self,request,pk):
                obj = get_object_or_404(Tenant , id = pk)
                serializer = TenantSerializer(obj)

                return Response(data=serializer.data,status=status.HTTP_200_OK)
        

        def put(self,request,pk):

                instance = get_object_or_404(Tenant, id = pk)
                serializer = TenantSerializer(instance = instance , data =request.data)

                serializer.is_valid(raise_exception=True)

                serializer.save()

                return Response(data= serializer.data, status=status.HTTP_200_OK)
        
        def delete(self,request,pk):
                obj = get_object_or_404(Tenant, id = pk)

                obj.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)



"""
        Superuser can both change and list everything.
        Owner can only list and change its own branchs
        Branch Manager can only see and change its branch

"""

class BranchListView(APIView):

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
                qs = self._get_queryset(request)
                
                        
                # serializer = BranchSerializer(data = qs , many = True)
                """ 'data=' argument is for the data coming outside (request) , In querysets, It is not used"""
                serializer = BranchSerializer(qs , many = True)

                return Response(data=serializer.data,status=status.HTTP_200_OK)


        #Creation of branch
        #Only SuperUser or owners can add a branch
        def post(self,request):

                if not (request.user.is_super_admin or request.user.is_owner):
                        raise PermissionDenied()
                
                
                serializer = BranchSerializer(data = request.data)
                serializer.is_valid(raise_exception=True)

                serializer.save(tenant = request.user.tenant)

                return Response(data=serializer.data , status=status.HTTP_201_CREATED)




class BranchDetailView(APIView):

        permission_classes = [IsAuthenticated]

        def _get_obj(self,request,pk):
                obj = get_object_or_404(Branch, id = pk)
                user = request.user

                if (user.is_super_admin):
                        return obj

                if (user.is_owner):
                        if (user.tenant == obj.get_tenant()):
                                return obj
                        else:
                                # return Response(status=status.HTTP_403_FORBIDDEN)
                                #helper methods SHOULDN'T RESPONSE  , because it returns to main function and 
                                # it is hard to manage , instead , throw exception
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
                obj = self._get_obj(request,pk)
                serializer = BranchSerializer(obj)

                return Response(data = serializer.data, status=status.HTTP_200_OK)


        def put(self,request,pk):
                instance = self._get_obj(request,pk)
                serializer = BranchSerializer(instance = instance , data = request.data)
                serializer.is_valid(raise_exception=True)
                serializer.save()

                return Response(data= serializer.data , status=status.HTTP_200_OK)



        def delete(self,request,pk):
                if (request.user.is_branch_manager):
                        return Response(data = {'detail':_('Branch managers can not delete branchs')},status=status.HTTP_403_FORBIDDEN)
        
                obj = self._get_obj(request,pk)
                obj.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)