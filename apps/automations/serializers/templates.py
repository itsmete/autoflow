from rest_framework import serializers
from ..models import Template
from apps.tenants.serializers import BranchSerializer,TenantSerializer
from apps.tenants.models import Tenant,Branch
from . import FIELD_META_SCHEMA
from django.utils.translation import gettext_lazy as _
from .base import validate_schema
from core.permissions import check_tenant_access
import re

                    

class TemplateReadSerializer(serializers.ModelSerializer):
        tenant = TenantSerializer(read_only= True)
        branch = BranchSerializer(read_only= True)

        class Meta:
                model = Template
                fields = ['name','raw_text','fields','tenant','branch','version','channel_type']
                read_only_fields = '__all__'

class TemplateWriteSerializer(serializers.ModelSerializer):

        tenant = serializers.PrimaryKeyRelatedField(queryset = Tenant.objects.all())
        branch = serializers.PrimaryKeyRelatedField(queryset = Branch.objects.all())


        class Meta:
                model = Template
                fields = ['name','raw_text','fields','tenant','branch','channel_type']      
                # version could be updated so that it is not read-only


        def validate_fields(self,value):
                
                # value = {"name": {"type": "string", ...}, "order_id": {...}}

                for field_name,field_def in value.items():
                        value = validate_schema(FIELD_META_SCHEMA,field_def)
                
                return value
                
        def validate(self,data):

                raw_text=  data.get('raw_text')
                fields = data.get('fields')

                user = self.context.get('request').user
                allowed, msg = check_tenant_access(user, data.get('tenant'), data.get('branch'))
                if not allowed:
                        raise serializers.ValidationError(msg)


                placeholders = set(re.findall(r'\{\{(\w+)\}\}'),raw_text)

                block_fields = set(re.findall(r'\{%if (\w+)%\}', raw_text))
                all_referenced = placeholders | block_fields

                missing = all_referenced - set(fields.keys())

                if missing:
                        raise serializers.ValidationError(
                                _("Template contains undefined fields : %(field)s") % {'fields': ", ".join(missing)}
                        )
                
                required_in_blocks = {
                        f for f in block_fields
                        if fields.get(f, {}).get('required')
                }

                if required_in_blocks:
                        raise serializers.ValidationError(
                                _("Required fields cannot be inside optional blocks: %(fields)s")
                                % {"fields": ", ".join(required_in_blocks)}
                        )

                

                return data
        
        def update(self, instance, validated_data):
                instance.version += 1
                return super().update(instance, validated_data)