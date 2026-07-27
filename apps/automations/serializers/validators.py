from django.apps import apps
from django.core.validators import validate_email, URLValidator
from django.core.exceptions import ValidationError


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