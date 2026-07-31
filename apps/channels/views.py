from rest_framework.views import APIView,Response,status
from .serializers.channel import ChannelReadSerializer,ChannelWriteSerializer
from .models import Channel
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from core.mixins import RoleBasedAccessMixin

# channels/ [GET] [POST]
class ChannelListCreateView(RoleBasedAccessMixin,APIView):

        # `allowed_roles` is not read by the mixin - only allowed_roles_for_list is,
        # so the old name left channels open to every role including staff.
        allowed_roles_for_list = ['super_admin','owner','branch_manager']
        model = Channel
        list_serializer = ChannelReadSerializer


        def get(self,request):
                # get_queryset()/check_permission() vanished in the caching refactor;
                # calling them raised AttributeError and every channels request 500'd.
                # get_cached_queryset returns already-serialized dicts (Channel is
                # marked CACHED: False, so it goes straight to the database).
                data = self.get_cached_queryset(request.user)

                return Response(
                        data = data,
                        status= status.HTTP_200_OK
                )

        def post(self,request):
                self.check_role_access(request.user)

                serializer = ChannelWriteSerializer(
                        data = request.data,
                        context = {'request':request}  
                )

                serializer.is_valid(raise_exception=True)

                obj = serializer.save()

                return_data = ChannelReadSerializer(obj)

        
                return Response(
                        data = return_data.data,
                        status = status.HTTP_201_CREATED
                )
        




# channels/<uuid:pk>/ [GET] [PUT] [DELETE]
class ChannelDetailView(RoleBasedAccessMixin,APIView):

        allowed_roles_for_list = ['super_admin','owner','branch_manager']
        model = Channel
        detail_serializer = ChannelReadSerializer

        def get(self,request,pk):
                obj = self.get_object(request.user,pk)
                serializer = ChannelReadSerializer(obj)

                return Response(
                        data= serializer.data,
                        status = status.HTTP_200_OK
                )


        def put(self,request,pk):
                obj = self.get_object(request.user,pk)

                serializer = ChannelWriteSerializer(
                        instance= obj,
                        data = request.data,
                        context = {'request':request}
                )

                serializer.is_valid(raise_exception=True)

                updated = serializer.save()

                return Response(
                        data = ChannelReadSerializer(updated).data,
                        status = status.HTTP_200_OK
                )


        def delete(self,request,pk):
                obj = self.get_object(request.user, pk)

                obj.delete()

                return Response(
                        status= status.HTTP_204_NO_CONTENT
                )




        