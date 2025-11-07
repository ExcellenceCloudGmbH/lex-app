"""
Core exceptions for the LEX application.

This module contains custom exception classes used throughout the core functionality.
"""


class ValidationError(Exception):
    """Custom validation error for rollback mechanism"""
    def __init__(self, message, original_exception=None, model_class=None):
        self.original_exception = original_exception
        self.model_class = model_class
        super().__init__(message)




class CalculatedModelError(Exception):
    """
    Base exception for calculated model operations.

    This is the parent class for all calculated model related errors,
    providing a common base for error handling and categorization.
    """
    def __init__(self, message: str, model_class: str = None, **kwargs):
        self.model_class = model_class
        self.context = kwargs

        # Build detailed error message with context

        detailed_message = message
        if model_class:
            detailed_message = f"[{model_class}] {detailed_message}"
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            detailed_message = f"{detailed_message} - Context: {context_str}"


        super().__init__(detailed_message)




class ModelCreationError(CalculatedModelError):

    def __init__(self, message: str, model_class: str = None, **kwargs):
        self.model_class = model_class
        self.context = kwargs

        # Build detailed error message with context
        detailed_message = message
        if model_class:
            detailed_message = f"[{model_class}] {detailed_message}"
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            detailed_message = f"{detailed_message} - Context: {context_str}"

        super().__init__(detailed_message)



class ModelCombinationError(CalculatedModelError):
    """
    Raised when model combination generation fails.

    This exception is raised when there are issues during the expansion
    of defining fields into model combinations, such as missing field
    values, invalid field configurations, or expansion logic failures.
    """

    def __init__(self, message: str, field_name: str = None, model_class: str = None, **kwargs):
        self.field_name = field_name
        self.model_class = model_class
        self.context = kwargs

        # Build detailed error message with context
        detailed_message = message
        if model_class:
            detailed_message = f"[{model_class}] {detailed_message}"
        if field_name:
            detailed_message = f"{detailed_message} (field: {field_name})"
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            detailed_message = f"{detailed_message} - Context: {context_str}"

        super().__init__(detailed_message)


class ModelClusteringError(CalculatedModelError):
    """
    Raised when model clustering fails.

    This exception is raised when there are issues during the organization
    of models into clusters based on parallelizable fields, such as invalid
    field values, clustering logic failures, or hierarchy construction errors.
    """

    def __init__(self, message: str, parallelizable_fields: list = None, model_count: int = None, **kwargs):
        self.parallelizable_fields = parallelizable_fields
        self.model_count = model_count
        self.context = kwargs

        # Build detailed error message with context
        detailed_message = message
        if model_count is not None:
            detailed_message = f"{detailed_message} (processing {model_count} models)"
        if parallelizable_fields:
            fields_str = ", ".join(parallelizable_fields)
            detailed_message = f"{detailed_message} - Parallelizable fields: [{fields_str}]"
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            detailed_message = f"{detailed_message} - Context: {context_str}"

        super().__init__(detailed_message)


class CeleryDispatchError(CalculatedModelError):
    """
    Raised when Celery task dispatch fails.

    This exception is raised when there are issues during Celery task
    creation, dispatch, or result handling, such as connection failures,
    task creation errors, or result processing failures.
    """

    def __init__(self, message: str, group_index: int = None, group_size: int = None, task_id: str = None, **kwargs):
        self.group_index = group_index
        self.group_size = group_size
        self.task_id = task_id
        self.context = kwargs

        # Build detailed error message with context
        detailed_message = message
        if group_index is not None and group_size is not None:
            detailed_message = f"{detailed_message} (group {group_index + 1} with {group_size} models)"
        elif group_size is not None:
            detailed_message = f"{detailed_message} (group with {group_size} models)"
        if task_id:
            detailed_message = f"{detailed_message} - Task ID: {task_id}"
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            detailed_message = f"{detailed_message} - Context: {context_str}"

        super().__init__(detailed_message)
