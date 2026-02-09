"""
Data models and exception classes for the improved CalculationLog system.

This module contains dataclasses and custom exceptions used throughout
the calculation logging system to provide better structure and error handling.
"""

from dataclasses import dataclass
from typing import Optional, List
from django.contrib.contenttypes.models import ContentType
from django.db import models


@dataclass
class ContextInfo:
    """
    Dataclass containing model context information for calculation logging.
    
    This class encapsulates all the context information needed to create
    and manage calculation log entries, including model references,
    calculation IDs, and audit log information.
    """
    calculation_id: str
    audit_log: 'AuditLog'  # Forward reference to avoid circular imports
    current_model: Optional[models.Model] = None
    parent_model: Optional[models.Model] = None
    current_record: Optional[str] = None
    parent_record: Optional[str] = None
    content_type: Optional[ContentType] = None
    parent_content_type: Optional[ContentType] = None
    root_record: Optional[str] = None


@dataclass
class CacheCleanupResult:
    """
    Dataclass containing the results of a cache cleanup operation.
    
    This class provides structured information about cache cleanup operations,
    including success status, cleaned keys, and any errors encountered.
    """
    success: bool
    cleaned_keys: List[str]
    errors: List[str]
    
    def __post_init__(self):
        """Ensure lists are initialized if None is passed."""
        if self.cleaned_keys is None:
            self.cleaned_keys = []
        if self.errors is None:
            self.errors = []


# Custom Exception Classes

class CalculationLogError(Exception):
    """
    Base exception for calculation log errors.
    
    This is the base class for all calculation logging related exceptions.
    It provides a common interface for error handling throughout the system.
    """
    
    def __init__(self, message: str, calculation_id: Optional[str] = None):
        self.calculation_id = calculation_id
        super().__init__(message)


class CacheOperationError(CalculationLogError):
    """
    Raised when Redis cache operations fail.
    
    This exception is raised when cache operations (store, retrieve, cleanup)
    encounter errors, such as Redis connection failures or timeout issues.
    """
    
    def __init__(self, message: str, calculation_id: Optional[str] = None, 
                 cache_key: Optional[str] = None):
        self.cache_key = cache_key
        super().__init__(message, calculation_id)





class ContextResolutionError(CalculationLogError):
    """
    Raised when model context cannot be resolved.
    
    This exception is raised when the system cannot properly resolve
    the model context from the model stack or when required context
    information is missing or invalid.
    """
    
    def __init__(self, message: str, calculation_id: Optional[str] = None,
                 stack_length: Optional[int] = None):
        self.stack_length = stack_length
        super().__init__(message, calculation_id)