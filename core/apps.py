from django.apps import AppConfig
from redis import Redis


class CoreConfig(AppConfig):
        name = 'core'

        def ready(self):
                import core.signals
                from django.conf import settings
                from core.cache import service

                if service.cache_service is not None:
                        return
                service.cache_service = service.CachingService(redis_client=
                        Redis.from_url(settings.REDIS_URL)
                )
