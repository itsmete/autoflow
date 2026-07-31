"""
Test helpers for the cache layer.

The point of CacheTestMixin is that endpoint tests run against a REAL cache
implementation - real lua, real index sets - just backed by fakeredis instead of a
server. Mocking the cache away is what let two whole classes of defect through:
a mocked bulk_get hid the fact that single_set.lua never wrote an index, and a
mocked bulk_cache.delay hid a broken CELERY_RESULT_BACKEND.
"""
import os
from unittest.mock import patch

import redis

import core.cache.service as cache_module
from core.cache.service import CachingService

try:
    import fakeredis
except ImportError:  # pragma: no cover
    fakeredis = None


# fakeredis executes the lua for real (via lupa), which is enough to catch every
# defect this suite guards against. To re-run the same tests against an actual redis
# server - worth doing before trusting the layer in production:
#
#   docker run -d -p 6399:6379 redis:7-alpine
#   AUTOFLOW_TEST_REDIS_URL=redis://localhost:6399/0 python manage.py test tests
#
# The database is FLUSHed between tests, so point this at a throwaway instance.
TEST_REDIS_URL = os.environ.get('AUTOFLOW_TEST_REDIS_URL')


def make_test_redis(testcase):
    """A redis client for a test: a real one if configured, else fakeredis."""
    if TEST_REDIS_URL:
        client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        client.flushdb()
        testcase.addCleanup(client.flushdb)
        return client

    if fakeredis is None:
        testcase.skipTest("fakeredis[lua] not installed and AUTOFLOW_TEST_REDIS_URL unset")

    return fakeredis.FakeStrictRedis(decode_responses=True)


class CacheTestMixin:
    """
    Swap in a fakeredis-backed cache_service for the duration of a test, and run
    the celery cache tasks eagerly so writes land before the assertions.

    Use self.redis to inspect the resulting keyspace directly.
    """

    def setUp(self):
        super().setUp()

        self.redis = make_test_redis(self)

        self._real_cache_service = cache_module.cache_service
        cache_module.cache_service = CachingService(self.redis)
        self.addCleanup(self._restore_cache_service)

        # .delay() would need a broker; run the task body inline instead so the
        # cache is actually populated by the time the request returns.
        for task_path in ('core.tasks.bulk_cache', 'core.tasks.single_cache'):
            patcher = patch(f'{task_path}.delay', side_effect=self._eager(task_path))
            patcher.start()
            self.addCleanup(patcher.stop)

    def _restore_cache_service(self):
        cache_module.cache_service = self._real_cache_service

    @staticmethod
    def _eager(task_path):
        from core import tasks

        func = getattr(tasks, task_path.rsplit('.', 1)[1])

        def run(*args, **kwargs):
            return func(*args, **kwargs)

        return run
