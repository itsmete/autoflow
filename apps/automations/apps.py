from django.apps import AppConfig


class AutomationsConfig(AppConfig):
        name = 'apps.automations'

        def ready(self):
            import apps.automations.signals