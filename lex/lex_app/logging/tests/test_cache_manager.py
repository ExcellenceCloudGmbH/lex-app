"""
Tests for the CacheManager class.

This module contains unit tests for the CacheManager class to ensure
proper functionality of cache operations, error handling, and graceful
degradation scenarios.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase
from django.core.cache.backends.base import InvalidCacheBackendError

from lex_app.logging.cache_manager import CacheManager
from lex_app.logging.data_models import CacheCleanupResult


class CacheManagerTestCase(TestCase):
    """Test cases for CacheManager functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_cache_key = "test_model_123_calc_456"
        self.test_message = "Test log message"
        self.test_calculation_id = "calc_456"
        self.test_calculation_record = "test_model_123"
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_store_message_success(self, mock_caches):
        """Test successful message storage in cache."""
        # Setup mock cache
        mock_cache = Mock()
        mock_cache.get.return_value = "existing message"
        mock_caches.__getitem__.return_value = mock_cache
        
        # Test store_message
        result = CacheManager.store_message(self.test_cache_key, self.test_message)
        
        # Assertions
        self.assertTrue(result)
        mock_cache.get.assert_called_once_with(self.test_cache_key, "")
        mock_cache.set.assert_called_once_with(
            self.test_cache_key, 
            "existing message\nTest log message", 
            timeout=CacheManager.CACHE_TIMEOUT
        )
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_store_message_new_key(self, mock_caches):
        """Test storing message with new cache key."""
        # Setup mock cache
        mock_cache = Mock()
        mock_cache.get.return_value = ""  # No existing message
        mock_caches.__getitem__.return_value = mock_cache
        
        # Test store_message
        result = CacheManager.store_message(self.test_cache_key, self.test_message)
        
        # Assertions
        self.assertTrue(result)
        mock_cache.set.assert_called_once_with(
            self.test_cache_key, 
            "Test log message", 
            timeout=CacheManager.CACHE_TIMEOUT
        )
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_store_message_cache_unavailable(self, mock_caches):
        """Test graceful degradation when cache is unavailable."""
        # Setup mock to raise InvalidCacheBackendError
        mock_caches.__getitem__.side_effect = InvalidCacheBackendError("Redis not available")
        
        # Test store_message
        result = CacheManager.store_message(self.test_cache_key, self.test_message)
        
        # Assertions
        self.assertFalse(result)  # Should return False but not raise exception
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_store_message_cache_error(self, mock_caches):
        """Test error handling during cache operations."""
        # Setup mock cache to raise exception
        mock_cache = Mock()
        mock_cache.get.side_effect = Exception("Cache connection error")
        mock_caches.__getitem__.return_value = mock_cache
        
        # Test store_message
        result = CacheManager.store_message(self.test_cache_key, self.test_message)
        
        # Assertions
        self.assertFalse(result)  # Should return False but not raise exception
    
    def test_build_cache_key(self):
        """Test cache key generation."""
        result = CacheManager.build_cache_key(self.test_calculation_record, self.test_calculation_id)
        expected = f"{self.test_calculation_record}_{self.test_calculation_id}"
        self.assertEqual(result, expected)
    
    def test_build_cache_key_empty_values(self):
        """Test cache key generation with empty values."""
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", self.test_calculation_id)
        
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key(self.test_calculation_record, "")
        
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key(None, self.test_calculation_id)
    
    @patch('lex.lex_app.logging.cache_manager.CacheManager._find_calculation_keys')
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_cleanup_calculation_success(self, mock_caches, mock_find_keys):
        """Test successful cache cleanup."""
        # Setup mocks
        mock_cache = Mock()
        mock_caches.__getitem__.return_value = mock_cache
        mock_find_keys.return_value = ["key1_calc_456", "key2_calc_456"]
        
        # Test cleanup_calculation
        result = CacheManager.cleanup_calculation(self.test_calculation_id)
        
        # Assertions
        self.assertIsInstance(result, CacheCleanupResult)
        self.assertTrue(result.success)
        self.assertEqual(len(result.cleaned_keys), 2)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(mock_cache.delete.call_count, 2)
    
    @patch('lex.lex_app.logging.cache_manager.CacheManager._find_calculation_keys')
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_cleanup_calculation_partial_failure(self, mock_caches, mock_find_keys):
        """Test cache cleanup with partial failures."""
        # Setup mocks
        mock_cache = Mock()
        mock_cache.delete.side_effect = [None, Exception("Delete failed")]  # First succeeds, second fails
        mock_caches.__getitem__.return_value = mock_cache
        mock_find_keys.return_value = ["key1_calc_456", "key2_calc_456"]
        
        # Test cleanup_calculation
        result = CacheManager.cleanup_calculation(self.test_calculation_id)
        
        # Assertions
        self.assertIsInstance(result, CacheCleanupResult)
        self.assertFalse(result.success)  # Should be False due to partial failure
        self.assertEqual(len(result.cleaned_keys), 1)  # Only first key cleaned
        self.assertEqual(len(result.errors), 1)  # One error recorded
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_cleanup_calculation_cache_unavailable(self, mock_caches):
        """Test cache cleanup when Redis is unavailable."""
        # Setup mock to raise InvalidCacheBackendError
        mock_caches.__getitem__.side_effect = InvalidCacheBackendError("Redis not available")
        
        # Test cleanup_calculation
        result = CacheManager.cleanup_calculation(self.test_calculation_id)
        
        # Assertions
        self.assertIsInstance(result, CacheCleanupResult)
        self.assertFalse(result.success)
        self.assertEqual(len(result.cleaned_keys), 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Redis cache backend not available", result.errors[0])
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_get_message_success(self, mock_caches):
        """Test successful message retrieval from cache."""
        # Setup mock cache
        mock_cache = Mock()
        mock_cache.get.return_value = "cached message"
        mock_caches.__getitem__.return_value = mock_cache
        
        # Test get_message
        result = CacheManager.get_message(self.test_cache_key)
        
        # Assertions
        self.assertEqual(result, "cached message")
        mock_cache.get.assert_called_once_with(self.test_cache_key)
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_get_message_cache_unavailable(self, mock_caches):
        """Test message retrieval when cache is unavailable."""
        # Setup mock to raise InvalidCacheBackendError
        mock_caches.__getitem__.side_effect = InvalidCacheBackendError("Redis not available")
        
        # Test get_message
        result = CacheManager.get_message(self.test_cache_key)
        
        # Assertions
        self.assertIsNone(result)
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_is_cache_available_true(self, mock_caches):
        """Test cache availability check when cache is available."""
        # Setup mock cache
        mock_cache = Mock()
        mock_caches.__getitem__.return_value = mock_cache
        
        # Test is_cache_available
        result = CacheManager.is_cache_available()
        
        # Assertions
        self.assertTrue(result)
        mock_cache.set.assert_called_once()
        mock_cache.delete.assert_called_once()
    
    @patch('lex.lex_app.logging.cache_manager.caches')
    def test_is_cache_available_false(self, mock_caches):
        """Test cache availability check when cache is unavailable."""
        # Setup mock to raise exception
        mock_caches.__getitem__.side_effect = Exception("Cache error")
        
        # Test is_cache_available
        result = CacheManager.is_cache_available()
        
        # Assertions
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()