from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
def validate_phone_number(value):
        cleaned = value.replace('-', '').replace(' ', '')
        if not cleaned.startswith('+') or not cleaned[1:].isdigit():
                raise serializers.ValidationError(_("Invalid phone number format"))