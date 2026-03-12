from rest_framework import serializers
from .base import BaseActionSerilaizer
from django.utils.translation import gettext_lazy as _

from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError

class EmailActionSerializer(BaseActionSerilaizer):

        def validate_config(self,value):
                required = ['to','subject','body']


                missing = [field for field in required if not value.get(field)]

                if missing:
                        raise serializers.ValidationError(
                                _(f'Missing fields : {",".join(missing)}')
                        )
                
                email = value.get('to')

                try : 
                        validate_email(email)
                except DjangoValidationError:
                        raise serializers.ValidationError(_("Invalid email format"))

                return value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              