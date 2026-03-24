from rest_framework import serializers
from . import get_serializer_class
from ..models import Channel
from apps.tenants.models import Tenant,Branch
from apps.tenants.serializers import TenantSerializer,BranchSerializer
from core.permissions import check_tenant_access
from django.utils.translation import gettext_lazy as _

class ChannelReadSerializer(serializers.ModelSerializer):
        tenant = TenantSerializer(read_only = True)
        branch = BranchSerializer(read_only = True)

        class Meta:
                model = Channel
                fields = ['id','name','branch','tenant','channel_type','credentials','created_at','updated_at']
                
                read_only_fields = '__all__'

class ChannelWriteSerializer(serializers.ModelSerializer):
        tenant = serializers.PrimaryKeyRelatedField(queryset = Tenant.objects.all())
        branch = serializers.PrimaryKeyRelatedField(queryset = Branch.objects.all())      


        class Meta:
                model = Channel
                fields = ['name','branch','tenant','channel_type','credentials']

        
        def validate(self, data):

                creds = data.get('credentials')
                user = self.context.get('request').user

                serializer_cls = get_serializer_class(data.get('channel_type'))

                if serializer_cls is None:
                        raise serializers.ValidationError(
                                _("Invalid channel type")
                        )

                new_creds = serializer_cls().validate_credentials(creds)

                data['credentials'] = new_creds

                allowed ,msg = check_tenant_access(user,data.get('tenant'),data.get('branch'))

                if not allowed:
                        raise serializers.ValidationError(msg)        
                return data
        
