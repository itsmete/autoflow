from rest_framework import serializers
from ..models import Template
from apps.tenants.serializers import BranchSerializer,TenantSerializer
from apps.tenants.models import Tenant,Branch
from . import FIELD_META_SCHEMA
from django.utils.translation import gettext_lazy as _
from .base import validate_schema
from core.permissions import check_tenant_access
# The expression layer owns the definition of "resolves from the context"; the
# serializer must not keep a second, drifting copy of that list.
from apps.automations.engine.expressions import RESERVED_NAMESPACES
from apps.automations.engine.paths import split_path
import re


# `{{total | currency:TRY}}` refers to the field `total`, and `{%if customer.vip%}`
# to the path `customer.vip`. Validation has to look at the path, not the whole
# expression, or a filter or a dotted path reads as an undefined field name.
PLACEHOLDER_PATTERN = re.compile(r'\{\{\s*(.+?)\s*\}\}')
BLOCK_PATTERN = re.compile(r'\{%\s*if\s+(.+?)\s*%\}')


def referenced_path(expression):
        """`total | currency:TRY` -> `total`."""
        return expression.split('|')[0].strip()


def needs_declaration(path,fields):
        """
        Must this placeholder be listed in `fields`?

        No, when it comes from the execution context rather than from the template's
        own declared inputs: a reserved root (`steps`, `trigger`, `now`, ...) or any
        dotted path. And no when the template declares no fields at all - that is a
        pure context-resolved template, which is the whole point of section 4.5.

        Yes for a bare name like `{{customer}}` in a template that does declare
        fields: that is the case the declaration exists to catch, and silently
        allowing a typo there is exactly the bug the field map prevents.
        """
        if not fields:
                return False

        segments = split_path(path)

        if not segments:
                # `{{ }}` names nothing; there is no declaration that could satisfy it.
                return False

        if segments[0] in RESERVED_NAMESPACES:
                return False

        if '.' in path or '[' in path:
                return False

        return path not in fields


class TemplateReadSerializer(serializers.ModelSerializer):
        tenant = TenantSerializer(read_only= True)
        branch = BranchSerializer(read_only= True)

        class Meta:
                model = Template
                fields = ['id','name','raw_text','fields','tenant','branch','version','channel_type']
                # Must be a list/tuple - the string '__all__' raises TypeError.
                read_only_fields = fields

class TemplateWriteSerializer(serializers.ModelSerializer):

        tenant = serializers.PrimaryKeyRelatedField(queryset = Tenant.objects.all())
        branch = serializers.PrimaryKeyRelatedField(queryset = Branch.objects.all())


        class Meta:
                model = Template
                fields = ['name','raw_text','fields','tenant','branch','channel_type']      
                # version could be updated so that it is not read-only
                extra_kwargs = {
                        # `fields` is optional now: a template that resolves everything
                        # from the context declares nothing, and `{}` is a valid body
                        # for that. The empty dict is the default so the column keeps
                        # being a JSON object instead of None.
                        'fields' : {'required' : False, 'default' : dict},
                }


        def validate_fields(self,value):

                # value = {"name": {"type": "string", ...}, "order_id": {...}}

                # Rebind the definitions, not `value` itself: the loop used to assign
                # its result back to `value`, so it mutated the dict it was iterating
                # and returned the LAST field's definition instead of the field map.
                validated = {}
                for field_name,field_def in value.items():
                        validated[field_name] = validate_schema(FIELD_META_SCHEMA,field_def)

                return validated
                
        def validate(self,data):

                # A partial update may touch only the body or only the field map; fall
                # back to what is stored so validation sees the template that will
                # exist, not half of the request.
                raw_text = data.get('raw_text')
                if raw_text is None and self.instance is not None:
                        raw_text = self.instance.raw_text
                raw_text = raw_text or ''

                fields = data.get('fields')
                if fields is None and self.instance is not None:
                        fields = self.instance.fields
                fields = fields or {}

                user = self.context.get('request').user
                allowed, msg = check_tenant_access(user, data.get('tenant'), data.get('branch'))
                if not allowed:
                        raise serializers.ValidationError(msg)


                # The closing paren was misplaced: re.findall(pattern) with raw_text
                # handed to set() instead. Every template create/update raised
                # TypeError before it could validate anything.
                #
                # The pattern was also `\w+`, which cannot see a dotted path, an index
                # or a filter pipe - `{{customer.name}}` was invisible to validation.
                placeholders = {referenced_path(p) for p in PLACEHOLDER_PATTERN.findall(raw_text)}
                block_fields = {referenced_path(p) for p in BLOCK_PATTERN.findall(raw_text)}
                all_referenced = placeholders | block_fields

                missing = {p for p in all_referenced if needs_declaration(p,fields)}

                if missing:
                        raise serializers.ValidationError(
                                # placeholder name has to match the key below, or the
                                # error formatting itself raises KeyError
                                _("Template contains undefined fields : %(fields)s") % {'fields': ", ".join(sorted(missing))}
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