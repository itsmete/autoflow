"""

        AutoamationTriggerSerializer
        AutomationConditionSerializer
        AutomationActionSerilizer
        AutomationLogSerializer
        AutomationSerializer
        
"""

from .actions import get_action_serializer
from .triggers import get_trigger_serializer_class
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from ..models import Automation,AutomationCondition,\
                AutomationAction,AutomationLog,AutomationTrigger

from apps.tenants.serializers import TenantSerializer, BranchSerializer
from apps.tenants.models import Tenant ,Branch

from core.permissions import check_tenant_access

from apps.channels.serializers.channel import ChannelReadSerializer
from apps.channels.models import Channel

CHANNEL_REQUIRED_ACTIONS = {'email', 'sms', 'whatsapp', 'instagram'}



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
                fields = ['action_type','channel','order','on_failure','config']

        def validate(self,data):

                action_type = data.get('action_type')

                config = data.get('config')
                channel = data.get('channel')

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

                # Same fix as AutomationTriggerSerializer: the .is_valid() form
                # validated nothing and blanked the config.
                data['config'] = serializer_cls().validate_config(config)
                return data
        



class AutomationActionReadSerializer(serializers.ModelSerializer):
        channel = ChannelReadSerializer(read_only=True)
        class Meta:
                model = AutomationAction
                fields = ['action_type','channel','order','on_failure','config']

        

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

        logs = AutomationLogSerializer(many=True,read_only=True)


        class Meta:
                model = Automation
                fields = ['name','allowed_roles','metadata','tenant','branch','triggers','conditions','actions','logs']



                


class AutomationWriteSerializer(serializers.ModelSerializer):

        tenant = serializers.PrimaryKeyRelatedField(queryset = Tenant.objects.all())
        branch = serializers.PrimaryKeyRelatedField(queryset = Branch.objects.all())

        triggers = AutomationTriggerSerializer(many=True)
        conditions = AutomationConditionSerializer(many=True)
        actions = AutomationActionWriteSerializer(many=True)



        class Meta:
                model = Automation
                fields = ['name','allowed_roles','metadata','tenant','branch','triggers','conditions','actions','is_active']


        def validate(self,data):

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

        def create(self, validated_data):

                # user = self.context.get('request').user
                
                triggers = validated_data.pop('triggers')
                conditions = validated_data.pop('conditions')
                actions = validated_data.pop('actions')
                
                automation = Automation.objects.create(**validated_data)


                
                for t in triggers:
                        AutomationTrigger.objects.create(automation= automation ,**t)
                
                # order comes from the position in the submitted list: grouping reads
                # conditions in order, so authoring order IS evaluation order.
                for index,c in enumerate(conditions):
                        AutomationCondition.objects.create(automation= automation ,order = index ,**c)

                for a in actions:
                        AutomationAction.objects.create(automation= automation ,**a)


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
                actions = validated_data.pop('actions')

                automation = super().update(instance,validated_data)

                automation.triggers.all().delete()
                automation.conditions.all().delete()
                automation.actions.all().delete()



                for t in triggers:
                        
                        AutomationTrigger.objects.create(automation= automation ,**t)
                
                # order comes from the position in the submitted list: grouping reads
                # conditions in order, so authoring order IS evaluation order.
                for index,c in enumerate(conditions):
                        AutomationCondition.objects.create(automation= automation ,order = index ,**c)

                for a in actions:
                        AutomationAction.objects.create(automation= automation ,**a)

                return automation