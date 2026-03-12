from rest_framework import serializers
from .base import BaseActionSerilaizer

from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator


class WebhookActionSerializer(BaseActionSerilaizer):

        def validate_config(self, value):
                required = ['url','method']
                method = ['GET','POST','PUT','DELETE']

                missing = [field for field in required if not value.get(field)]

                if missing:
                        raise serializers.ValidationError(
                                _(f'Missing fields : {",".join(missing)}')
                        )
                
                validator = URLValidator()
                try:
                        validator(value.get('url'))
                
                except DjangoValidationError:
                        raise serializers.ValidationError(
                                _("Invalid URL")
                        )


                if str(value.get('method')).lower() not in method:
                        raise serializers.ValidationError(
                                _("Invalid HTTP Method")
                        )


                
                return value