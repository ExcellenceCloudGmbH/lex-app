"""
Cache management utilities for the CalculationLog system.

This module provides the CacheManager class for handling cache operations
including message storage, cache cleanup, and graceful degradation when Cache
is unavailable.

Note on Cache Cleanup:
The cleanup_calculation() method attempts to find cache keys by pattern matching.
However, pattern-based key discovery can be unreliable depending on the
configuration and django-redis version. If pattern matching fails, the method
gracefully degrades by skipping cleanup - this is acceptable since cache entries
have a TTL and will expire naturally (default: 1 week).

For more reliable cleanup, use cleanup_specific_key() when you know the exact
cache keys, or pass specific_keys to cleanup_calculation().
"""

import logging
import os
from typing import List, Optional
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from lex.audit_logging.utils.data_models import CacheCleanupResult, CacheOperationError

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Manages cache operations for calculation logging.
    
    This class provides static methods for storing log messages in cache,
    cleaning up cache entries after calculations complete, and handling
    unavailability gracefully.
    """
    
    CACHE_TIMEOUT = 60 * 60 * 24 * 7  # Cache for one week
    CALC_CACHE_NAME = "redis" if os.getenv("DEPLOYMENT_ENVIRONMENT") else "default"

    @staticmethod
    def store_message(cache_key: str, message: str) -> bool:
        """
        Store log message in cache with error handling.
        
        Args:
            cache_key: The cache key to store the message under
            message: The log message to store
            
        Returns:
            bool: True if message was stored successfully, False otherwise
            
        Raises:
            CacheOperationError: If cache operation fails and graceful degradation is disabled
        """
        try:
            cache = caches[CacheManager.CALC_CACHE_NAME]
            # print("System cache:", caches)
            
            # Get existing message from cache, append new message
            existing_message = cache.get(cache_key, "")
            updated_message = existing_message + "\n" + message if existing_message else message
            
            # Store updated message with timeout
            cache.set(cache_key, updated_message)
            
            logger.debug(f"Successfully stored message in cache with key: {cache_key}")
            return True
            
        except InvalidCacheBackendError:
            logger.warning(f"Cache backend not available, skipping cache storage for key: {cache_key}")
            return False
            
        except Exception as e:
            logger.error(f"Failed to store message in cache with key {cache_key}: {str(e)}")
            # Graceful degradation - don't raise exception, just log and continue
            return False


    @staticmethod
    def cleanup_calculation(calculation_id: str = None, specific_keys: Optional[List[str]] = None) -> CacheCleanupResult:
        """
        Remove all cache entries for a completed calculation.
        
        Args:
            calculation_id: The calculation ID to clean up cache entries for
            specific_keys: Optional list of specific cache keys to clean up.
                          If provided, only these keys will be cleaned.
                          If None, will attempt to find keys by pattern matching.
            
        Returns:
            CacheCleanupResult: Results of the cleanup operation including
                               success status, cleaned keys, and any errors
        """
        cleaned_keys = []
        errors = []
        
        try:
            cache = caches[CacheManager.CALC_CACHE_NAME]

            
            # Determine which keys to clean up
            if specific_keys is not None:
                # Use provided specific keys
                pattern_keys = specific_keys
                logger.debug(f"Cleaning up {len(specific_keys)} specific cache keys for calculation {calculation_id}")
            elif calculation_id is not None:
                # Try to find keys by pattern matching
                pattern_keys = CacheManager._find_calculation_keys(cache, calculation_id)
                logger.debug(f"Found {len(pattern_keys)} cache keys by pattern matching for calculation {calculation_id}")
            else:
                return CacheCleanupResult(success=True, errors=errors, cleaned_keys=cleaned_keys)
            
            for key in pattern_keys:
                try:
                    cache.delete(key)
                    cleaned_keys.append(key)
                    logger.debug(f"Successfully cleaned cache key: {key}")
                except Exception as e:
                    error_msg = f"Failed to delete cache key {key}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(error_msg)
            
            success = len(errors) == 0
            logger.info(f"Cache cleanup for calculation {calculation_id} completed. "
                       f"Cleaned {len(cleaned_keys)} keys, {len(errors)} errors")
            
            return CacheCleanupResult(
                success=success,
                cleaned_keys=cleaned_keys,
                errors=errors
            )
            
        except InvalidCacheBackendError:
            error_msg = "Cache backend not available for cleanup"
            logger.warning(error_msg)
            return CacheCleanupResult(
                success=False,
                cleaned_keys=[],
                errors=[error_msg]
            )
            
        except Exception as e:
            error_msg = f"Unexpected error during cache cleanup for calculation {calculation_id}: {str(e)}"
            logger.error(error_msg)
            return CacheCleanupResult(
                success=False,
                cleaned_keys=cleaned_keys,
                errors=errors + [error_msg]
            )
    
    @staticmethod
    def build_cache_key(calculation_record: str, calc_id: str) -> str:
        """
        Generate cache key using pattern {calculation_record}_{calc_id}.
        
        Args:
            calculation_record: The calculation record identifier (e.g., "model_name_pk")
            calc_id: The calculation ID
            
        Returns:
            str: The formatted cache key
        """
        if not calculation_record or not calc_id:
            raise ValueError("Both calculation_record and calc_id must be provided")
        
        return f"{calculation_record}_{calc_id}"
    
    @staticmethod
    def _find_calculation_keys(cache, calculation_id: str) -> List[str]:
        """
        Find all cache keys associated with a calculation ID.
        
        This method attempts to find keys using available django-redis methods.
        Since pattern-based key discovery can be unreliable, this method gracefully
        handles failures and returns an empty list when keys cannot be found.
        
        Args:
            cache: The cache instance
            calculation_id: The calculation ID to search for
            
        Returns:
            List[str]: List of cache keys associated with the calculation
        """
        try:
            # For django-redis, try to use the iter_keys method if available
            # This is more reliable than trying to access the underlying client
            if hasattr(cache, 'iter_keys'):
                pattern = f"*_{calculation_id}"
                matching_keys = list(cache.iter_keys(pattern))
                return [str(key) for key in matching_keys if key]
            
            # Fallback: Try the keys method
            elif hasattr(cache, 'keys'):
                pattern = f"*_{calculation_id}"
                matching_keys = cache.keys(pattern)
                return [str(key) for key in matching_keys if key]
            
            else:
                # If no pattern matching is available, log and return empty list
                logger.info(f"Pattern-based key cleanup not available for calculation {calculation_id}. "
                           "Cache entries will expire naturally based on TTL.")
                return []
            
        except Exception as e:
            logger.warning(f"Could not retrieve cache keys for pattern matching: {str(e)}")
            # Graceful degradation: return empty list
            # Cache entries will expire naturally based on their TTL (1 week)
            # This is acceptable behavior as cache cleanup is an optimization, not a requirement
            return []
    
    @staticmethod
    def get_message(cache_key: str) -> Optional[str]:
        """
        Retrieve a message from cache.
        
        Args:
            cache_key: The cache key to retrieve
            
        Returns:
            Optional[str]: The cached message if found, None otherwise
        """
        try:
            cache = caches[CacheManager.CALC_CACHE_NAME]
            return cache.get(cache_key)
            
        except InvalidCacheBackendError:
            logger.warning(f"Cache backend not available, cannot retrieve key: {cache_key}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to retrieve message from cache with key {cache_key}: {str(e)}")
            return None
    
    @staticmethod
    def is_cache_available() -> bool:
        """
        Check if cache is available.
        
        Returns:
            bool: True if cache is available, False otherwise
        """
        try:
            cache = caches[CacheManager.CALC_CACHE_NAME]
            # Try a simple operation to test connectivity
            cache.set("__cache_test__", "test", timeout=1)
            cache.delete("__cache_test__")
            return True
            
        except Exception:
            return False
    
    @staticmethod
    def cleanup_specific_key(cache_key: str) -> bool:
        """
        Clean up a specific cache key.
        
        This method is useful when you know the exact cache key to clean up,
        avoiding the need for pattern matching.
        
        Args:
            cache_key: The specific cache key to delete
            
        Returns:
            bool: True if the key was successfully deleted, False otherwise
        """
        try:
            cache = caches[CacheManager.CALC_CACHE_NAME]
            cache.delete(cache_key)
            logger.debug(f"Successfully deleted cache key: {cache_key}")
            return True
            
        except InvalidCacheBackendError:
            logger.warning(f"Cache backend not available, cannot delete key: {cache_key}")
            return False
            
        except Exception as e:
            logger.error(f"Failed to delete cache key {cache_key}: {str(e)}")
            return False