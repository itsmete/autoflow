"""
Test package bootstrap.

core.apps builds a real Redis client at startup, and core/signals.py invalidates
through it on every model save. During tests that means each factory call tries to
open a TCP connection to localhost:6379, fails, logs a traceback and moves on -
correct behaviour, but slow and extremely noisy.

Swapping the global service for a fakeredis-backed one keeps the suite hermetic
while still exercising the real CachingService and the real lua. Tests that want to
assert on cache contents use tests.utils.CacheTestMixin, which installs its own
per-test instance on top of this.

Set AUTOFLOW_TEST_REDIS_URL to point the whole suite at a real server instead.
"""
import os

import core.cache.service as _cache_module
from core.cache.service import CachingService

if not os.environ.get('AUTOFLOW_TEST_REDIS_URL'):
    try:
        import fakeredis

        _cache_module.cache_service = CachingService(
            fakeredis.FakeStrictRedis(decode_responses=True)
        )
    except ImportError:  # pragma: no cover - fakeredis is a dev dependency
        pass
