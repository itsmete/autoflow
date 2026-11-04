"""

        AutoamationTriggerSerializer
        AutomationConditionSerializer
        AutomationActionSerilizer
        AutomationLogSerializer
        AutomationSerializer
        
"""

from .actions import get_action_serializer
from .triggers import get_trigger_serializer_class
from .steps import (CHANNEL_REQUIRED_ACTIONS,AutomationEdgeReadSerializer,
                AutomationEdgeWriteSerializer,AutomationStepReadSerializer,
                AutomationStepWriteSerializer,validate_step_graph)
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from ..models import Automation,AutomationCondition,\
                AutomationAction,AutomationEdge,AutomationLog,AutomationStep,\
                AutomationTrigger,sync_actions_from_steps,sync_steps_from_actions

from apps.tenants.serializers import TenantSerializer, BranchSerializer
from apps.tenants.models import Tenant ,Branch

from core.permissions import check_tenant_access

from apps.channels.serializers.channel import ChannelReadSerializer
from apps.channels.models import Channel

# CHANNEL_REQUIRED_ACTIONS lives in steps.py now (imported above): the action
# serializer and the step config validator must agree on which action types cannot
# work without a channel, and two copies of that set would drift.



class AutomationTriggerSerializer(serializers.ModelSerializer):
        
        class Meta:
                model = AutomationTrigger
                fields = ['trigger_type','config','initial_data']

        
        def validate(self,data):

                trigger_type = data.get('trigger_type')
                config = data.get('config')

                serializer_cls = get_trigger_serializer_class(trigger_type)

                if not serializer_cls:
                        raise serializers.ValidationError(_("Invalid Serializer type"))

                # Call validate_config directly, the way apps/channels does with
                # validate_credentials.
                #
                # The previous form was serializer_cls(data=config).is_valid(), which
                # did nothing at all: BaseConfigSerializer declares no fields, so
                # to_internal_value had nothing to bind, validate_config (a field-level
                # hook for a field named "config") was never invoked, and
                # validated_data came back EMPTY. Every trigger config was silently
                # discarded and stored as {} - which is why a scheduled trigger then
                # died on config['cron_expression'].
                data['config'] = serializer_cls().validate_config(config)
                return data


class AutomationActionWriteSerializer(serializers.ModelSerializer):
        channel = serializers.PrimaryKeyRelatedField(
                queryset=Channel.objects.all(),
                required=False,
                allow_null=True
        )

        class Meta:
                model = AutomationAction
                fields = ['action_type','channel','template','order','on_failure','config']

        def validate(self,data):

                action_type = data.get('action_type')

                config = data.get('config')
                channel = data.get('channel')
                template = data.get('template')

                serializer_cls = get_action_serializer(action_type)

                if not serializer_cls:
                        raise serializers.ValidationError(
                                _("Invalid action type")
                        )
                
                if action_type in CHANNEL_REQUIRED_ACTIONS and not channel:
                        raise serializers.ValidationError(
                                _("Channel is required for this action type")
                        )

                if channel and channel.channel_type != action_type:
                        raise serializers.ValidationError(
                                _("Channel type doesn't match with action type")
                        )

                # A template is rendered into a specific medium: an e-mail body sent
                # through the SMS provider would silently lose its formatting (or
                # fail at the provider). Rejecting it here is the only place that can
                # see both halves at once.
                if template is not None and template.channel_type != action_type:
                        raise serializers.ValidationError(
                                _("Template channel type doesn't match with action type")
                        )

                # Same fix as AutomationTriggerSerializer: the .is_valid() form
                # validated nothing and blanked the config.
                data['config'] = serializer_cls().validate_config(config)
                return data
        



class AutomationActionReadSerializer(serializers.ModelSerializer):
        channel = ChannelReadSerializer(read_only=True)
        class Meta:
                model = AutomationAction
                fields = ['action_type','channel','template','order','on_failure','config']

        

class AutomationConditionSerializer(serializers.ModelSerializer):

        class Meta:
                model = AutomationCondition
                fields= ['field','operator','value','logical_operator','quantifier','on_missing','order']

                # Derived from the position in the submitted list, never sent. Grouping
                # reads the conditions in order ('or' opens a new AND-group), so the
                # authored order has to be the evaluated order - letting a client set
                # both a position and an order invites the two to disagree.
                read_only_fields = ['order']

class AutomationLogSerializer(serializers.ModelSerializer):
        class Meta:
                model = AutomationLog
                fields = ['status','triggered_at','error_message','execution_context','decision']





class AutomationReadSerializer(serializers.ModelSerializer):
        tenant = TenantSerializer(read_only = True)
        branch = BranchSerializer(read_only = True)

        triggers = AutomationTriggerSerializer(many=True,read_only = True)
        conditions = AutomationConditionSerializer(many=True,read_only = True)
        actions = AutomationActionReadSerializer(many=True,read_only = True)

        # The graph, next to the list it was derived from: a client that only knows
        # `actions` sees the chain it just wrote, and a client that authors `steps`
        # can read back the branching the list cannot express.
        steps = AutomationStepReadSerializer(many = True,read_only = True)
        edges = AutomationEdgeReadSerializer(many = True,read_only = True)

        logs = AutomationLogSerializer(many=True,read_only=True)


        class Meta:
                model = Automation
                fields = ['name','allowed_roles','metadata','tenant','branch','triggers','conditions','actions','steps','edges','logs']



                


class AutomationWriteSerializer(serializers.ModelSerializer):

        tenant = serializers.PrimaryKeyRelatedField(queryset = Tenant.objects.all())
        branch = serializers.PrimaryKeyRelatedField(queryset = Branch.objects.all())

        triggers = AutomationTriggerSerializer(many=True)
        conditions = AutomationConditionSerializer(many=True)

        # Two ways to describe the same flow, both accepted for the whole of Faz 1
        # (ROADMAP §5.1): `actions` is the legacy linear list, `steps`+`edges` is the
        # graph. Whichever is submitted, the other side is derived in create/update so
        # neither representation is ever stale.
        actions = AutomationActionWriteSerializer(many = True,required = False)
        steps = AutomationStepWriteSerializer(many = True,required = False)
        edges = AutomationEdgeWriteSerializer(many = True,required = False)



        class Meta:
                model = Automation
                fields = ['name','allowed_roles','metadata','tenant','branch','triggers','conditions','actions','steps','edges','is_active']


        def validate(self,data):

                # The graph checks run for EVERY caller, super_admin included: they are
                # about the shape of the payload, not about who may send it. That is why
                # they come before the early return below.
                actions = data.get('actions')
                steps = data.get('steps')
                edges = data.get('edges')

                if actions is None and steps is None:
                        raise serializers.ValidationError(
                                {'actions' : _("En az bir aksiyon veya adım tanımlanmalıdır.")}
                        )

                if steps is None and edges is not None:
                        raise serializers.ValidationError(
                                {'edges' : _("Adım tanımlanmadan bağlantı tanımlanamaz.")}
                        )

                if steps is not None:
                        # Replaced by the normalized list: linear flows get their chain
                        # generated here, so create/update never has to guess.
                        data['edges'] = validate_step_graph(steps,edges)

                user = self.context.get('request').user

                if user.is_super_admin:
                        return data

                
                if user.role not in data.get('allowed_roles'):
                        raise serializers.ValidationError(_("You don't have a permission to do that"))

                
                if not user.is_super_admin and not data.get('tenant'):
                        raise serializers.ValidationError(_("Tenant field is required"))

                if (user.is_branch_manager or user.is_staff_role) and not data.get('branch'):
                        raise serializers.ValidationError(_("Branch field is required for your role"))

                allowed, msg = check_tenant_access(user, data.get('tenant'), data.get('branch'))
                if not allowed:
                        raise serializers.ValidationError(msg)

                return data                            

        def _write_flow(self,automation,actions,steps,edges):
                """
                Persist the flow from whichever representation the client sent.

                Rules, in order:
                  - `steps` given: the graph is written as given; the action rows are
                    written as given too, or derived from the action steps when the
                    client did not send any (sync_actions_from_steps). Without that
                    second half, a graph-only automation would be inert - the legacy
                    executor reads AutomationAction.
                  - only `actions` given: the list is written and the graph is derived
                    from it (sync_steps_from_actions).
                """
                if steps is not None:
                        step_map = {}

                        for step_data in steps:
                                step_data = dict(step_data)
                                key = step_data.pop('key')
                                step_map[key] = AutomationStep.objects.create(
                                        automation = automation,key = key,**step_data
                                )

                        for edge_data in edges or []:
                                edge_data = dict(edge_data)
                                from_key = edge_data.pop('from_step')
                                to_key = edge_data.pop('to_step')

                                AutomationEdge.objects.create(
                                        automation = automation,
                                        from_step = step_map[from_key] if from_key else None,
                                        to_step = step_map[to_key],
                                        **edge_data
                                )

                        if actions is not None:
                                for a in actions:
                                        AutomationAction.objects.create(automation = automation ,**a)
                        else:
                                sync_actions_from_steps(automation)

                elif actions is not None:
                        for a in actions:
                                AutomationAction.objects.create(automation = automation ,**a)

                        sync_steps_from_actions(automation)

        def create(self, validated_data):

                # user = self.context.get('request').user
                
                triggers = validated_data.pop('triggers')
                conditions = validated_data.pop('conditions')
                actions = validated_data.pop('actions',None)
                steps = validated_data.pop('steps',None)
                edges = validated_data.pop('edges',None)
                
                automation = Automation.objects.create(**validated_data)


                
                for t in triggers:
                        AutomationTrigger.objects.create(automation= automation ,**t)
                
                # order comes from the position in the submitted list: grouping reads
                # conditions in order, so authoring order IS evaluation order.
                for index,c in enumerate(conditions):
                        AutomationCondition.objects.create(automation= automation ,order = index ,**c)

                self._write_flow(automation,actions,steps,edges)


                # We can't use bulk_create since bulk_create optimizes the multiple creations in SQL wise, me
                # Meaning that the python logic is skipped, also our overrided save() method 

                # AutomationTrigger.objects.bulk_create(
                #         [AutomationTrigger(automation = automation,**t) for t in triggers]
                # )
                # AutomationCondition.objects.bulk_create(
                #         [AutomationCondition(automation = automation,**c) for c in conditions]
                # )
                # AutomationAction.objects.bulk_create(
                #         [AutomationAction(automation = automation,**a)for a in actions]
                # )
                
                return automation
        
        def update(self,instance,validated_data):

                triggers = validated_data.pop('triggers')
                conditions = validated_data.pop('conditions')
                actions = validated_data.pop('actions',None)
                steps = validated_data.pop('steps',None)
                edges = validated_data.pop('edges',None)

                automation = super().update(instance,validated_data)

                automation.triggers.all().delete()
                automation.conditions.all().delete()

                # Switching representations has to clear BOTH sides. Keeping the old
                # action rows while writing a graph would leave the legacy executor
                # running a flow the author just replaced.
                if actions is not None or steps is not None:
                        automation.edges.all().delete()
                        automation.steps.all().delete()
                        automation.actions.all().delete()



                for t in triggers:
                        
                        AutomationTrigger.objects.create(automation= automation ,**t)
                
                # order comes from the position in the submitted list: grouping reads
                # conditions in order, so authoring order IS evaluation order.
                for index,c in enumerate(conditions):
                        AutomationCondition.objects.create(automation= automation ,order = index ,**c)

                self._write_flow(automation,actions,steps,edges)

                return automation