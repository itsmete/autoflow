from rest_framework import serializers
from ..providers import get_provider_class
from ..providers.schema_registry import get_field_type,FIELDS

from django.utils.translation import gettext_lazy as _
import logging

logger = logging.getLogger(__name__)

class BaseProviderSerializer():
        
        channel_type = None

        def _validate_schema(self,schema,data):

                for k,v in schema.items():
                        

                        field_value =  data.get(k)
                        
                        if (field_value is None):
                                if v.get('required') is False and v.get('default') is not None:
                                        data[k] = v.get('default')
                                        continue
                                else:
                                        raise serializers.ValidationError(
                                                _("This field can't leave empty.")
                                        )                      
                                                                 
                        if v.get('choices') is not None:
                                if field_value not in v.get('choices'):
                                        raise serializers.ValidationError(
                                                _("Invalid choice is given")
                                        )

                        if type(field_value) != get_field_type(v.get('type')):
                                raise serializers.ValidationError(
                                        _("The type of field doesn't match with the required type")
                        )

                        if (v.get('validator') is not None):
                                func = v.get('validator')
                                try:
                                        func(data.get(k))
                                except Exception:
                                        raise serializers.ValidationError(
                                                _(f"Validation failed for field {k}")
                                        )

                        

                        if v.get('type') == 'dict' and v.get('fields'):
                                self._validate_schema(v['fields'], field_value)

                unknown_keys = set(data.keys() - set(schema.keys()))
                if unknown_keys:        
                        logger.info(f"[WARN] An unsupported field type(s) ({unknown_keys}) provided in serializer, will be ignored.")

                return data
       
       
        def validate_credentials(self,value):
                schema = get_provider_class(self.channel_type).CREDENTIALS_SCHEMA
                
                value = self._validate_schema(schema,value)
                        

                return value
