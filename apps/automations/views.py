from rest_framework.views import APIView,Response,status
from .authentication import TriggerSecretKeyAuthentication
from .tasks import run_automation
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404
from .models import Automation,TriggerType,AutomationTrigger
from rest_framework.exceptions import NotFound,PermissionDenied



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

     
class ManualTriggeredAutomationView(APIView):

        # automations/<uuid>/run 
        def _get_automation_object(self,request,pk):
                
                obj = get_object_or_404(Automation,id = pk)
                
                try: 
                        trigger = obj.automationtrigger_set.get(trigger_type = TriggerType.MANUAL )
                except AutomationTrigger.DoesNotExist :
                        raise NotFound(_("That object can't be manually triggered."))

                allowed_roles = obj.allowed_roles

                if not (request.user.role in allowed_roles):
                        raise PermissionDenied(_("You don't have a permission to run this automation"))
                
                if request.user.is_branch_manager  or request.user.is_staff:
                        if not (obj.branch == request.user.branch):
                                raise PermissionDenied(_("You don't have a permission to run this automation"))
                
                if  request.user.is_owner :
                        if not (obj.tenant == request.user.tenant):
                                raise PermissionDenied(_("You don't have a permission to run this automation"))
                
                return (obj,trigger)

        def post(self,request,pk):
                automation ,trigger = self._get_automation_object(request,pk)
                
                data = request.data if request.data else trigger.initial_data 

                run_automation.delay(automation.id,data)

                return Response(
                        {
                                "message" : _("Automation Triggered")
                        },
                        status=status.HTTP_202_ACCEPTED
                )

