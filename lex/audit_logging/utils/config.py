"""
Configuration module for audit logging functionality.

This module provides centralized configuration management for audit logging
features, including environment variable handling, validation, and default values.
"""

import os
from typing import Optional, Union


class AuditLoggingConfig:
    """
    Configuration class for audit logging functionality.
    
    This class provides a centralized way to manage audit logging configuration
    options, including environment variable parsing, validation, and default values.
    """
    
    # Default configuration values
    DEFAULT_AUDIT_LOGGING_ENABLED = True
    DEFAULT_BATCH_SIZE = 100
    
    # Environment variable names
    ENV_AUDIT_LOGGING_ENABLED = 'INITIAL_DATA_AUDIT_LOGGING'
    ENV_BATCH_SIZE = 'INITIAL_DATA_AUDIT_BATCH_SIZE'
    
    # Valid values for boolean environment variables
    TRUE_VALUES = {'true', '1', 'yes', 'on', 'enabled'}
    FALSE_VALUES = {'false', '0', 'no', 'off', 'disabled'}
    
    def __init__(self):
        """Initialize configuration with values from environment variables."""
        self._audit_logging_enabled = self._parse_audit_logging_enabled()
        self._batch_size = self._parse_batch_size()
    
    def _parse_audit_logging_enabled(self) -> bool:
        """
        Parse the audit logging enabled setting from environment variable.
        
        Returns:
            bool: True if audit logging is enabled, False otherwise
            
        Raises:
            ValueError: If the environment variable value is invalid
        """
        env_value = os.getenv(self.ENV_AUDIT_LOGGING_ENABLED, '').lower().strip()
        
        if not env_value:
            return self.DEFAULT_AUDIT_LOGGING_ENABLED
        
        if env_value in self.TRUE_VALUES:
            return True
        elif env_value in self.FALSE_VALUES:
            return False
        else:
            raise ValueError(
                f"Invalid value for {self.ENV_AUDIT_LOGGING_ENABLED}: '{env_value}'. "
                f"Valid values are: {', '.join(sorted(self.TRUE_VALUES | self.FALSE_VALUES))}"
            )
    
    def _parse_batch_size(self) -> int:
        """
        Parse the batch size setting from environment variable.
        
        Returns:
            int: The batch size for audit log operations
            
        Raises:
            ValueError: If the environment variable value is invalid
        """
        env_value = os.getenv(self.ENV_BATCH_SIZE, '').strip()
        
        if not env_value:
            return self.DEFAULT_BATCH_SIZE
        
        try:
            batch_size = int(env_value)
            if batch_size <= 0:
                raise ValueError(
                    f"Batch size must be a positive integer, got: {batch_size}"
                )
            return batch_size
        except ValueError as e:
            if "invalid literal" in str(e):
                raise ValueError(
                    f"Invalid value for {self.ENV_BATCH_SIZE}: '{env_value}'. "
                    f"Must be a positive integer."
                )
            raise
    
    @property
    def audit_logging_enabled(self) -> bool:
        """
        Get the audit logging enabled setting.
        
        Returns:
            bool: True if audit logging is enabled, False otherwise
        """
        return self._audit_logging_enabled
    
    @property
    def batch_size(self) -> int:
        """
        Get the batch size setting.
        
        Returns:
            int: The batch size for audit log operations
        """
        return self._batch_size
    
    def validate_configuration(self) -> None:
        """
        Validate the current configuration.
        
        This method performs additional validation checks beyond basic parsing
        to ensure the configuration is sensible for the application.
        
        Raises:
            ValueError: If the configuration is invalid
        """
        # Validate batch size is reasonable
        if self.batch_size > 10000:
            raise ValueError(
                f"Batch size {self.batch_size} is too large. "
                f"Maximum recommended batch size is 10000."
            )
        
        if self.batch_size < 1:
            raise ValueError(
                f"Batch size {self.batch_size} is too small. "
                f"Minimum batch size is 1."
            )
    
    def get_configuration_summary(self) -> dict:
        """
        Get a summary of the current configuration.
        
        Returns:
            dict: Configuration summary with all current settings
        """
        return {
            'audit_logging_enabled': self.audit_logging_enabled,
            'batch_size': self.batch_size,
            'environment_variables': {
                self.ENV_AUDIT_LOGGING_ENABLED: os.getenv(self.ENV_AUDIT_LOGGING_ENABLED, 'not set'),
                self.ENV_BATCH_SIZE: os.getenv(self.ENV_BATCH_SIZE, 'not set')
            },
            'defaults_used': {
                'audit_logging_enabled': os.getenv(self.ENV_AUDIT_LOGGING_ENABLED) is None,
                'batch_size': os.getenv(self.ENV_BATCH_SIZE) is None
            }
        }
    
    @classmethod
    def create_with_validation(cls) -> 'AuditLoggingConfig':
        """
        Create a configuration instance with validation.
        
        Returns:
            AuditLoggingConfig: Validated configuration instance
            
        Raises:
            ValueError: If the configuration is invalid
        """
        config = cls()
        config.validate_configuration()
        return config


# Global configuration instance
_config_instance: Optional[AuditLoggingConfig] = None


def get_audit_logging_config() -> AuditLoggingConfig:
    """
    Get the global audit logging configuration instance.
    
    This function provides a singleton pattern for accessing the configuration
    to ensure consistency across the application.
    
    Returns:
        AuditLoggingConfig: The global configuration instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = AuditLoggingConfig.create_with_validation()
    return _config_instance


def reset_audit_logging_config() -> None:
    """
    Reset the global configuration instance.
    
    This function is primarily used for testing to ensure a clean state
    between test runs.
    """
    global _config_instance
    _config_instance = None


# Convenience functions for common configuration access
def is_audit_logging_enabled() -> bool:
    """
    Check if audit logging is enabled.
    
    Returns:
        bool: True if audit logging is enabled, False otherwise
    """
    return get_audit_logging_config().audit_logging_enabled


def get_batch_size() -> int:
    """
    Get the configured batch size.
    
    Returns:
        int: The batch size for audit log operations
    """
    return get_audit_logging_config().batch_size
