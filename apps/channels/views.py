from rest_framework.views import APIView,Response,status
from .serializers.channel import ChannelReadSerializer,ChannelWriteSerializer
from .models import Channel
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from core.mixins import RoleBasedAccessMixin

# channels/ [GET] [POST]
class ChannelListCreateView(RoleBasedAccessMixin,APIView):
        
        allowed_roles = ['super_admin','owner','branch_manager']
        model = Channel


        def get(self,request):
                obj = self.get_queryset(request.user)
                serializer = ChannelReadSerializer(obj,many=True)

                return Response(
                        data = serializer.data,
                        status= status.HTTP_200_OK
                )
        
        def post(self,request):
                self.check_permission(request.user)

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
        
        allowed_roles = ['super_admin','owner','branch_manager']
        model = Channel

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




        