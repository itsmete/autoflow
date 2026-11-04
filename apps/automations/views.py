from rest_framework.views import APIView,Response,status
from rest_framework.throttling import ScopedRateThrottle
from .authentication import TriggerSecretKeyAuthentication
from .tasks import run_automation
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404
from .models import Automation,TriggerType,AutomationTrigger,Template,\
        AutomationRun,RunMode,RunStatus
from rest_framework.exceptions import NotFound,PermissionDenied,ValidationError
from core.mixins import RoleBasedAccessMixin
from core.pagination import StandartPagination
from .engine.orchestrator import AutomationOrchestrator
from . import runtime
from .serializers.automations import AutomationReadSerializer,\
        AutomationWriteSerializer,AutomationRunSerializer,AutomationStepRunSerializer
from .serializers.templates import TemplateReadSerializer,TemplateWriteSerializer



class WebhookAutomationView(APIView):
        permission_classes = []
        authentication_classes = [TriggerSecretKeyAuthentication]

        # The one endpoint anyone on the internet can reach. Authentication rejects
        # bad callers but does so only after a database lookup, so an unthrottled
        # endpoint is a free way to make us do work. Rate configured under
        # REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['webhook'].
        throttle_classes = [ScopedRateThrottle]
        throttle_scope = 'webhook'

        def post(self,request):
                automation = request.auth.automation
                payload = request.data

                run_automation.delay(str(automation.id), payload)

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

                run_automation.delay(str(automation.id),data)

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


#
# Run history, dry-run and replay (ROADMAP section 5.2, Faz 2.B / 2.C)
#

class RunAccessMixin(RoleBasedAccessMixin):
        """
        The two-layer check every run endpoint needs, in one place.

        Layer one is the mixin's tenant/branch scope (`get_object` raises 403 for a
        row outside the caller's scope and 404 for one that does not exist). Layer two
        is the automation's own `allowed_roles`, exactly as AutomationDetailView
        applies it. A run has no roles of its own: it inherits them from the
        automation it belongs to, which is why every run lookup goes through
        `get_automation` instead of being trusted on its own.
        """

        model = Automation

        def get_automation(self,user,pk):
                automation = self.get_object(user,pk)

                if user.role not in (automation.allowed_roles or []):
                        raise PermissionDenied(
                                _("You are not permitted to perform this")
                        )

                return automation

        def get_run(self,user,run_id):
                # select_related because the access check needs the automation
                # immediately - the run itself carries no scope of its own.
                run = get_object_or_404(
                        AutomationRun.objects.select_related('automation'),pk = run_id
                )

                self.get_automation(user,run.automation_id)

                return run

        def paginated(self,queryset,request,serializer_class):
                paginator = StandartPagination()
                page = paginator.paginate_queryset(queryset,request,view = self)

                serializer = serializer_class(page,many = True)

                return paginator.get_paginated_response(serializer.data)


# automations/<uuid:pk>/runs/
class AutomationRunListView(RunAccessMixin,APIView):

        def get(self,request,pk):
                automation = self.get_automation(request.user,pk)

                queryset = automation.runs.all().order_by('-created_at')

                # Validated rather than ignored: a typo'd filter that silently returned
                # everything would look like a working filter over a bigger history.
                run_status = request.query_params.get('status')
                if run_status:
                        if run_status not in RunStatus.values:
                                raise ValidationError({'status' : _("Unknown run status")})
                        queryset = queryset.filter(status = run_status)

                mode = request.query_params.get('mode')
                if mode:
                        if mode not in RunMode.values:
                                raise ValidationError({'mode' : _("Unknown run mode")})
                        queryset = queryset.filter(mode = mode)

                return self.paginated(queryset,request,AutomationRunSerializer)


# automations/runs/<uuid:run_id>/
class AutomationRunDetailView(RunAccessMixin,APIView):

        def get(self,request,run_id):
                run = self.get_run(request.user,run_id)

                return Response(
                        data = AutomationRunSerializer(run).data,
                        status = status.HTTP_200_OK
                )


# automations/runs/<uuid:run_id>/steps/
class AutomationStepRunListView(RunAccessMixin,APIView):

        def get(self,request,run_id):
                run = self.get_run(request.user,run_id)

                queryset = run.step_runs.all().order_by('created_at')

                return self.paginated(queryset,request,AutomationStepRunSerializer)


# automations/<uuid:pk>/test/
class TestAutomationView(RunAccessMixin,APIView):
        """
        Dry-run an automation: walk the whole flow, touch nothing.

        The run is created HERE, before the orchestrator is called, so the response can
        name the exact row this request produced. Without that, "the newest run of this
        automation" would be a race between two concurrent tests.
        """

        def post(self,request,pk):
                automation = self.get_automation(request.user,pk)

                first_trigger = automation.triggers.first()
                payload = request.data if request.data else (
                        first_trigger.initial_data if first_trigger else {}
                )
                payload = dict(payload) if payload else {}

                run = AutomationRun.objects.create(
                        automation = automation,
                        status = RunStatus.QUEUED,
                        mode = RunMode.TEST,
                        trigger_payload = payload,
                )

                AutomationOrchestrator().run(
                        str(automation.id),payload,
                        run_id = str(run.id),
                        mode = RunMode.TEST,
                )

                run.refresh_from_db()

                data = AutomationRunSerializer(run).data
                data['step_runs'] = AutomationStepRunSerializer(
                        run.step_runs.all(),many = True
                ).data

                return Response(data = data,status = status.HTTP_200_OK)


# automations/runs/<uuid:run_id>/replay/
class AutomationRunReplayView(RunAccessMixin,APIView):

        def post(self,request,run_id):
                source_run = self.get_run(request.user,run_id)

                mode = request.data.get('mode') or source_run.mode

                if mode not in RunMode.values:
                        raise ValidationError({'mode' : _("Unknown run mode")})

                new_run = runtime.create_replay_run(source_run,mode = mode)

                # Same payload the original attempt was given; the new row starts with
                # an empty step history on purpose - a replay that skipped the source's
                # successful steps would do nothing at all.
                run_automation.delay(
                        str(source_run.automation_id),source_run.trigger_payload,
                        run_id = str(new_run.id),
                        mode = new_run.mode,
                )

                return Response(
                        data = AutomationRunSerializer(new_run).data,
                        status = status.HTTP_202_ACCEPTED
                )
