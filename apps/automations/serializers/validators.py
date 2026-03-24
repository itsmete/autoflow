from django.apps import apps


def validate_model_path(value):
        """Validates ''app_label.ModelName format"""

        try:
                apps.get_model(value)

        except (LookupError,ValueError):
                raise # _validate_schema catches the exception and prints the default error message from CONFIG_SCHEMA