from rest_framework.views import APIView,Response,status
from .authentication import TriggerSecretKeyAuthentication
from .tasks import run_automation
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404
from .models import Automation,TriggerType,AutomationTrigger
from rest_framework.exceptions import NotFound,PermissionDenied

from .serializers.automations import AutomationReadSerializer,\
        AutomationWriteSerializer



class AutomationAccessMixin:

        def get_automation(self,request,pk):
                obj = get_object_or_404(Automation,id = pk)

                allowed_roles = obj.allowed_roles

                if not (request.user.role in allowed_roles):
                        raise PermissionDenied(_("You don't have a permission to run this automation"))
                
                if  request.user.is_owner :
                        if not (obj.tenant == request.user.tenant):
                                raise PermissionDenied(_("You don't have a permission to run this automation"))
        
                if request.user.is_branch_manager  or request.user.is_staff:
                        if not (obj.branch == request.user.branch):
                                raise PermissionDenied(_("You don't have a permission to run this automation"))
                
                return obj
        

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
class ManualTriggeredAutomationView(APIView,AutomationAccessMixin):

        def post(self,request,pk):

                automation = self.get_automation(request,pk)

                try: 
                        trigger = automation.automationtrigger_set.get(trigger_type = TriggerType.MANUAL )
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
class AutomationListCreateView(APIView):
        
        def _get_qs(self,request):
                user = request.user

                qs = None 

                if  user.is_super_admin:
                        qs = Automation.objects.all()
                elif user.is_owner:
                        qs =Automation.objects.filter(
                                tenant = user.tenant,
                                allowed_roles__contains = [user.role] )
                elif user.is_branch_manager or user.is_staff:
                        qs = Automation.objects.filter(
                                branch = user.branch,
                                allowed_roles__contains = [user.role] 
                        )

                return qs


        def get(self,request):

                qs = self._get_qs(request)

                serializer = AutomationReadSerializer(qs,many=True)

                return Response(
                        data = serializer.data,
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
class AutomationDetailView(AutomationAccessMixin,APIView):
        
        def get(self,request,pk):
                automation = self.get_automation(request,pk)

                serializer = AutomationReadSerializer(automation)

                return Response(
                        data = serializer.data,
                        status= status.HTTP_200_OK
                )


        def put(self,request,pk):
                automation = self.get_automation(request,pk)

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
                automation = self.get_automation(request,pk)

                automation.delete()

                return Response(
                        status=status.HTTP_204_NO_CONTENT
                )                
