import unittest
from unittest.mock import MagicMock, patch
from core.cache.service import CachingService
from redis.exceptions import NoScriptError

class TestCachingService(unittest.TestCase):
    
    def setUp(self):
        self.mock_redis = MagicMock()
        self.service = CachingService(self.mock_redis)
        self.service.scripts = {
            'single_set': 'sha_single_set',
            'single_get': 'sha_single_get',
            'bulk_get': 'sha_bulk_get',
            'single_invalidate': 'sha_single_invalidate',
        }
        
    def test_single_get(self):
        self.mock_redis.evalsha.return_value = '{"id": 1}'
        res = self.service.get('test_key')
        self.mock_redis.evalsha.assert_called_with('sha_single_get', 1, 'test_key')
        self.assertEqual(res, '{"id": 1}')
        
    def test_single_set(self):
        self.service.set('test_key', ['idx1', 'idx2'], '{"id": 1}', 300)
        self.mock_redis.evalsha.assert_called_with('sha_single_set', 3, 'test_key', 'idx1', 'idx2', '{"id": 1}', '300')
        
    def test_bulk_get(self):
        self.mock_redis.evalsha.return_value = ['{"id": 1}', '{"id": 2}']
        res = self.service.bulk_get(['idx1'])
        self.mock_redis.evalsha.assert_called_with('sha_bulk_get', 1, 'idx1')
        self.assertEqual(res, ['{"id": 1}', '{"id": 2}'])

    def test_single_invalidate(self):
        self.service.single_invalidate('test_key', ['idx1'])
        self.mock_redis.evalsha.assert_called_with('sha_single_invalidate', 2, 'test_key', 'idx1')

    @patch('core.cache.service.settings')
    def test_handle_script_error_reload(self, mock_settings):
        # Setup mock to raise NoScriptError on first call, then succeed
        self.mock_redis.evalsha.side_effect = [NoScriptError(), '{"id": 1}']
        
        # We also need to mock load_scripts so it doesn't try to read actual files
        self.service.load_scripts = MagicMock()
        
        res = self.service.get('test_key')
        
        self.assertTrue(self.service.load_scripts.called)
        self.assertEqual(res, '{"id": 1}')

