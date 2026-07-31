"""
Unit tests for CachingService's call shapes.

These use a MagicMock redis, so they only prove that the right sha is invoked with
the right arguments. They CANNOT prove the lua does the right thing - that is what
tests/test_cache_lua.py is for, and the distinction matters: an earlier version of
this file passed all green while single_set.lua never wrote a single index, because
a mock happily accepts any command.
"""
import unittest
from unittest.mock import MagicMock
from core.cache.service import CachingService
from redis.exceptions import NoScriptError


class TestCachingService(unittest.TestCase):

    SHAS = {
        'single_set': 'sha_single_set',
        'single_get': 'sha_single_get',
        'bulk_set': 'sha_bulk_set',
        'bulk_get': 'sha_bulk_get',
        'single_invalidate': 'sha_single_invalidate',
    }

    def setUp(self):
        self.mock_redis = MagicMock()
        self.service = CachingService(self.mock_redis)
        self.service.scripts = dict(self.SHAS)

    def test_single_get(self):
        self.mock_redis.evalsha.return_value = '{"id": 1}'
        res = self.service.get('test_key')
        self.mock_redis.evalsha.assert_called_with('sha_single_get', 1, 'test_key')
        self.assertEqual(res, '{"id": 1}')

    def test_single_set_writes_no_index(self):
        self.service.set('test_key', '{"id": 1}', 300)
        # One key only: the data key. Index sets belong to bulk_set.
        self.mock_redis.evalsha.assert_called_with(
            'sha_single_set', 1, 'test_key', '{"id": 1}', '300'
        )

    def test_bulk_get_passes_a_single_index_key(self):
        self.mock_redis.evalsha.return_value = ['{"id": 1}', '{"id": 2}']
        res = self.service.bulk_get('users/all')
        # Regression: bulk_get used to splat its argument, so the string 'users/all'
        # became nine single-character keys.
        self.mock_redis.evalsha.assert_called_with('sha_bulk_get', 1, 'users/all')
        self.assertEqual(res, ['{"id": 1}', '{"id": 2}'])

    def test_bulk_set_flattens_pairs_after_the_ttl(self):
        ok = self.service.bulk_set(
            'users/all',
            [('users/u1', '{"id":"u1"}'), ('users/u2', '{"id":"u2"}')],
            300,
        )
        self.assertTrue(ok)
        self.mock_redis.evalsha.assert_called_with(
            'sha_bulk_set', 1, 'users/all',
            '300', 'users/u1', '{"id":"u1"}', 'users/u2', '{"id":"u2"}',
        )

    def test_bulk_set_skips_empty_results(self):
        ok = self.service.bulk_set('users/all', [], 300)
        self.assertFalse(ok)
        self.mock_redis.evalsha.assert_not_called()

    def test_single_invalidate(self):
        self.service.single_invalidate('test_key', ['idx1'])
        self.mock_redis.evalsha.assert_called_with(
            'sha_single_invalidate', 2, 'test_key', 'idx1'
        )

    def test_handle_script_error_reload(self):
        self.mock_redis.evalsha.side_effect = [NoScriptError(), '{"id": 1}']
        self.service.load_scripts = MagicMock()

        res = self.service.get('test_key')

        self.assertTrue(self.service.load_scripts.called)
        self.assertEqual(res, '{"id": 1}')

    def test_failures_degrade_to_none_instead_of_raising(self):
        # A cache must never break a request.
        self.mock_redis.evalsha.side_effect = ConnectionError("redis is down")
        with self.assertLogs('core.cache.service', level='ERROR'):
            self.assertIsNone(self.service.get('test_key'))
