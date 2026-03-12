from rest_framework.views import APIView,Response,status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied


from django.shortcuts import get_object_or_404

from core.tokens import ExtendedRefreshToken
from core.roles import get_visible_roles

from .models import User

from .serializers import UserLoginSerializer,UserRegisterSerializer,UserProfileSerializer ,\
UserAdminUpdateSerializer,UserSelfUpdateSerializer,UserReaderSerializer



class LoginView(APIView):

        serializer_class = UserLoginSerializer

        def post(self,request):
                serializer = self.serializer_class(data = request.data)

                serializer.is_valid(raise_exception=True) # automatically throws a error if an error occurs

                user = serializer.validated_data['user']

                
                refresh = ExtendedRefreshToken.for_user(user)
                access = refresh.access_token

                return Response(
                        {
                                'access' : str(access),
                                'refresh' : str(refresh)
                        }
                )

class RegisterView(APIView):
        permission_classes = [IsAuthenticated]

        def post(self,request):

                serializer = UserRegisterSerializer(
                        data = request.data,
                        context = {'request':request}
                )

                serializer.is_valid(raise_exception=True)

                user = serializer.save()

                return Response(
                        UserProfileSerializer(user).data,
                        status=status.HTTP_201_CREATED
                )
        



class UserAccessMixin:

        def _get_obj(self,request,pk):
                
                obj = get_object_or_404(User, id = pk)

                role = request.user.role
                
                obj_role = obj.role


                if (role == User.Role.SUPER_ADMIN):
                        return obj

                if (role == User.Role.OWNER ):
                        visible_roles = get_visible_roles(str(role))
                        if ( (str(obj_role) in visible_roles) and (request.user.tenant == obj.tenant) ):
                                return obj                
                        else:
                                raise PermissionDenied()
                        
                if (role == User.Role.BRANCH_MANAGER):
                        if not request.user.branch:
                                raise PermissionDenied()

                        if not obj.branch or obj.branch != request.user.branch:
                                raise PermissionDenied()

                        return obj

                if (role == User.Role.STAFF):
                        # not permitted
                        raise PermissionDenied()


class UserListView(APIView):
        """
                Super Admin : Sees everyone
                Owner : Sees its branch managers and staff
                BManager : Sees staffs
                Staff : cannot reach that endpoint
        
        """

        permission_classes = [IsAuthenticated]


        def _get_queryset(self,request):
                role = request.user.role
                visible_roles = get_visible_roles(str(role))
                
                if (role == User.Role.SUPER_ADMIN):
                        qs = User.objects.all()

                if (role == User.Role.OWNER ):
        
                        qs = User.objects.filter(
                                role__in = visible_roles,
                                tenant = request.user.tenant
                        )

                if (role == User.Role.BRANCH_MANAGER):

                        if not request.user.branch:
                                raise PermissionDenied()
                        
                        qs = User.objects.filter(
                                role__in = visible_roles,
                                tenant = request.user.tenant,
                                branch = request.user.branch
                        )                                                  


                if (role == User.Role.STAFF):
                        # not permitted
                        raise PermissionDenied()
                
                return qs
                

        def get(self,request):
                qs = self._get_queryset(request)

                serializer = UserReaderSerializer(qs , many=True)

                return Response(data = serializer.data ,status= status.HTTP_200_OK)
        



class UserDetailView(UserAccessMixin,APIView):
        
        permission_classes = [IsAuthenticated]

        

        def get(self,request,pk):
                obj = self._get_obj(request,pk)

                serializer = UserReaderSerializer(obj)

                return Response(data = serializer.data, status=status.HTTP_200_OK)





class UserSelfUpdateView(APIView):
        
        permission_classes = [IsAuthenticated]

        # /users/me

        # to see its own profile
        def get(self,request):
                serializer = UserReaderSerializer(request.user)
                return Response(data=serializer.data,status=status.HTTP_200_OK)


        def put(self,request):
                user = request.user

                serializer = UserSelfUpdateSerializer(instance = user , data = request.data)
                serializer.is_valid(raise_exception=True)

                serializer.save()

                response_serializer = UserReaderSerializer(user)

                return Response(data = response_serializer.data ,status=status.HTTP_200_OK)



class UserDeleteView(UserAccessMixin, APIView):


        permission_classes = [IsAuthenticated]

                

        def delete(self,request,pk):
                obj = self._get_obj(request,pk)

                obj.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)

                 
class UserAdminUpdateView(UserAccessMixin,APIView):
        permission_classes = [IsAuthenticated]



        def put(self,request,pk):
                obj = self._get_obj(request,pk)

                serializer = UserAdminUpdateSerializer(instance = obj,
                                                       data = request.data,
                                                       context = {'request':request}
                                                       )
                
                serializer.is_valid(raise_exception=True)
                serializer.save()

                response_serializer = UserReaderSerializer(obj)

                return Response(data=response_serializer.data, status=status.HTTP_200_OK)


