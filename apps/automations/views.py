from rest_framework.views import APIView,Response,status
from .authentication import TriggerSecretKeyAuthentication
from .tasks import run_automation
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404
from .models import Automation,TriggerType,AutomationTrigger,Template
from rest_framework.exceptions import NotFound,PermissionDenied
from core.mixins import RoleBasedAccessMixin
from .serializers.automations import AutomationReadSerializer,\
        AutomationWriteSerializer
from .serializers.templates import TemplateReadSerializer,TemplateWriteSerializer



class WebhookAutomationView(APIView):
        permission_classes = []
        authentication_classes = [TriggerSecretKeyAuthentication]

        def post(self,request):
                automation = request.auth.automation
                payload = request.data

                run_automation.delay(automation.id, payload)

                return Response(
                        data= {"message":_("Automation Triggered")},
                        status=status.HTTP_202_ACCEPTED
                )

# automations/<uuid>/run 
class ManualTriggeredAutomationView(RoleBasedAccessMixin,APIView):
        model  = Automation

        def post(self,request,pk):

                automation = self.get_object(request.user ,pk)
                if request.user.role not in automation.allowed_roles:
                        raise PermissionDenied(
                                _("You are not permitted to perform this")
                        )



                try: 
                        trigger = automation.triggers.get(trigger_type = TriggerType.MANUAL )
                except AutomationTrigger.DoesNotExist :
                        raise NotFound(_("That object can't be manually triggered."))

                
                data = request.data if request.data else trigger.initial_data 

                run_automation.delay(automation.id,data)

                return Response(
                        {
                                "message" : _("Automation Triggered")
                        },
                        status=status.HTTP_202_ACCEPTED
                )

# automations/   [GET, POST]
class AutomationListCreateView(RoleBasedAccessMixin,APIView):
        
        model  = Automation
        list_serializer = AutomationReadSerializer

        def get(self,request):

                serialized = self.get_cached_queryset(request.user)
        
                filtered = [
                        item for item in serialized 
                        if request.user.role in item.get('allowed_roles', [])
                ]
                
                return Response(
                        data = filtered,
                        status = status.HTTP_200_OK
                )
        
        def post(self,request):
                
                serializer = AutomationWriteSerializer(
                        data =request.data,
                        context = {'request':request}
                )

                serializer.is_valid(raise_exception=True)

                obj  = serializer.save()

                return Response(
                        data = AutomationReadSerializer(obj).data ,
                        status=status.HTTP_201_CREATED
                )




# automations/ [GET,PUT,DELETE]
class AutomationDetailView(RoleBasedAccessMixin,APIView):
        model  = Automation
        detail_serializer = AutomationReadSerializer

        def _check_allowed_roles(self, user, automation):
                roles = automation.get('allowed_roles', []) if isinstance(automation, dict) else getattr(automation, 'allowed_roles', [])
                if user.role not in roles:
                        raise PermissionDenied(
                                _("You are not permitted to perform this")
                        )
        def get(self,request,pk):
                serialized = self.get_cached_object(request.user ,pk)
                self._check_allowed_roles(request.user , serialized)

                

                return Response(
                        data = serialized,
                        status= status.HTTP_200_OK
                )


        def put(self,request,pk):
                automation = self.get_object(request.user ,pk)
                self._check_allowed_roles(request.user , automation)

                serializer = AutomationWriteSerializer(
                        instance = automation ,
                        data = request.data,
                        context = {'request':request}
                )

                serializer.is_valid(raise_exception=True)

                obj = serializer.save()

                return Response(
                        data = AutomationReadSerializer(obj).data ,
                        status=status.HTTP_200_OK
                )


        def delete(self,request,pk):
                automation = self.get_object(request.user ,pk)
                self._check_allowed_roles(request.user , automation)

                automation.delete()

                return Response(
                        status=status.HTTP_204_NO_CONTENT
                )                

# automations/templates/
class TemplateListCreateView(RoleBasedAccessMixin,APIView):

        model = Template
        allowed_roles_for_list = ['super_admin','owner','branch_manager'] 
        
        list_serializer = TemplateReadSerializer
        
        def get(self,request):
                serialized = self.get_cached_queryset(request.user)

                return Response(
                        data = serialized,
                        status = status.HTTP_200_OK
                )        


        def post(self,request):
                serializer = TemplateWriteSerializer(
                        data = request.data,
                        context = {'request': request}
                )

                serializer.is_valid(raise_exception=True)

                obj = serializer.save()

                return Response(
                        data = TemplateReadSerializer(obj).data,
                        status=status.HTTP_201_CREATED
                )




# automation/templates/<uuid:pk>/
class TemplateDetailView(RoleBasedAccessMixin,APIView):

        model = Template
        allowed_roles = ['super_admin','owner','branch_manager']
        detail_serializer = TemplateReadSerializer

        def get(self,request,pk):
                serialized = self.get_cached_object(request.user,pk)
                

                return Response(
                        data = serialized,
                        status = status.HTTP_200_OK
                )

        def put(self,request,pk):
                instance = self.get_object(request.user,pk)

                serializer = TemplateWriteSerializer(
                        instance = instance,
                        data = request.data,
                        context = {'request' : request}
                )

                serializer.is_valid(raise_exception=True)

                obj = serializer.save()

                return Response(
                        data = TemplateReadSerializer(obj).data,
                        status=status.HTTP_200_OK
                )
        


        def delete(self,request,pk):
                
                obj = self.get_object(request.user,pk)
                
               
                obj.delete()
                return Response(status =status.HTTP_204_NO_CONTENT)
