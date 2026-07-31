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

                # from_url() does not connect, so this cannot fail at boot even with
                # redis down. Scripts are registered lazily on first use rather than
                # here, for the same reason.
                #
                # decode_responses=True keeps the whole layer on str: without it get()
                # hands back bytes and the payloads that come out of the cache differ
                # in type from the ones that go into it.
                service.cache_service = service.CachingService(
                        redis_client=Redis.from_url(
                                settings.REDIS_URL,
                                decode_responses=True,
                        )
                )
