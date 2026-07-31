from django.apps import AppConfig
from django.db.models.signals import post_migrate
import logging


logger = logging.getLogger(__name__)


class AutomationsConfig(AppConfig):
        name = 'apps.automations'

        def ready(self):
                import apps.automations.signals
                from apps.automations.signals import register_existing_signal_triggers

                # Signal-type triggers connect their listeners from rows in the
                # database, but ready() runs before the schema is guaranteed to exist.
                # Querying here outright meant `migrate` on a fresh database died with
                # "no such table: automations_automationtrigger" - the app could not be
                # installed from scratch at all. Django also warns about DB access
                # during app initialization.
                #
                # post_migrate covers setup and deploys; the lazy call below covers a
                # normal boot where no migration runs.
                post_migrate.connect(self._register_triggers, sender=self)
                self._register_triggers()

        def _register_triggers(self,**kwargs):
                from django.db import DatabaseError
                from apps.automations.signals import register_existing_signal_triggers

                try:
                        register_existing_signal_triggers()
                except DatabaseError:
                        # Table not created yet (fresh checkout, pre-migrate). The
                        # post_migrate hook will register them once it exists.
                        logger.debug(
                                "automations: trigger table not ready, deferring signal registration"
                        )
