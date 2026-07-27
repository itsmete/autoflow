from rest_framework import serializers
from . import get_field_type
from django.utils.translation import gettext_lazy as _
import logging


logger = logging.getLogger(__name__)



class BaseConfigSerializer(serializers.Serializer):
        
        CONFIG_SCHEMA = {}
        
        def _validate_schema(self,schema,data):
                
                

                for k,v in schema.items():
                        
                        field_value =  data.get(k)
                        
                        if (field_value is None):
                                if v.get('required',True):
                                        raise serializers.ValidationError(
                                                _("This field can't leave empty.")
                                        )  
                                elif v.get('default') is not None:
                                        data[k] = v.get('default')
                                continue
                        
                                                            
                                                                 
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
                                        msg = v.get('error_message')
                                        if msg:
                                                raise serializers.ValidationError(_(msg))
                                        else :
                                                raise serializers.ValidationError(
                                                        _("Validation failed for field %(field)s") % {"field" : k}
                                                )

                        

                        if v.get('type') == 'dict' and v.get('fields'):
                                self._validate_schema(v['fields'], field_value)

                unknown_keys = set(data.keys() - set(self.CONFIG_SCHEMA.keys()))
                if unknown_keys:        
                        logger.info(f"[WARN] An unsupported field type(s) ({unknown_keys}) provided in serializer, will be ignored.")

                return data

        def validate_config(self,value):
                return self._validate_schema(self.CONFIG_SCHEMA,value)