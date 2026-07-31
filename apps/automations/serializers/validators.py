"""
Validators used by the schema walker in serializers/base.py.

CONTRACT: a validator rejects by RAISING. A falsy return value means nothing - the
walker only catches exceptions. croniter.is_valid() returns a bool and never raises,
so wiring it in directly meant every cron expression was accepted, valid or not;
validate_cron_expression below adapts it to the contract.
"""
from django.apps import apps
from django.core.validators import validate_email, URLValidator
from django.core.exceptions import ValidationError
from croniter import croniter


def validate_cron_expression(value):
        if not croniter.is_valid(value):
                raise ValidationError("Invalid cron expression")


def validate_model_path(value):
        """Validates ''app_label.ModelName format"""

        try:
                apps.get_model(value)

        except (LookupError,ValueError):
                raise # _validate_schema catches the exception and prints the default error message from CONFIG_SCHEMA

def validate_email_wrapped(value):

        try : 
                validate_email(value)
        except ValidationError:
                raise

url_validator = URLValidator()