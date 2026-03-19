from django.apps import AppConfig


class AutomationsConfig(AppConfig):
        name = 'apps.automations'

        def ready(self):
            import apps.automations.signals
            from apps.automations.signals import register_existing_signal_triggers
            register_existing_signal_triggers()