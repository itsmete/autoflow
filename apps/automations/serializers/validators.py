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


# Bounds for a per-request timeout. Below 1 a value is not a timeout at all; above 60
# an external call can hold a Celery worker for a minute per step, and the run is
# already lost by then. Rejected at authoring time so the engine never has to guess.
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60


def validate_timeout(value):
        try:
                seconds = int(value)
        except (TypeError,ValueError):
                raise ValidationError("Timeout must be an integer number of seconds")

        if seconds < MIN_TIMEOUT_SECONDS or seconds > MAX_TIMEOUT_SECONDS:
                raise ValidationError(
                        f"Timeout must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS} seconds"
                )