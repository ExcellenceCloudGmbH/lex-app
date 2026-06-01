"""
Core exceptions for the LEX application.

This module contains custom exception classes used throughout the core functionality.
"""

GENERIC_SERVER_ERROR_MESSAGES = {
    "A server error occurred.",
    "A server error occurred",
    "Server Error",
}


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def iter_exception_chain(exception):
    current = exception
    seen = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def _normalize_string(value):
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def select_preferred_exception_detail(details):
    normalized_details = [
        detail
        for detail in (_normalize_string(item) for item in ensure_list(details))
        if detail
    ]

    for detail in normalized_details:
        if detail not in GENERIC_SERVER_ERROR_MESSAGES:
            return detail

    if normalized_details:
        return normalized_details[0]

    return None


def select_preferred_stack_trace(stack_traces):
    for stack_trace in ensure_list(stack_traces):
        normalized_trace = _normalize_string(stack_trace)
        if normalized_trace:
            return normalized_trace

    return None


def find_exception_artifacts(exception):
    for chained_exception in iter_exception_chain(exception):
        calc_obj = ensure_list(getattr(chained_exception, "calc_obj", None))
        exception_details = ensure_list(
            getattr(chained_exception, "exception_details", None)
        )
        stack_trace = ensure_list(getattr(chained_exception, "stack_trace", None))

        if calc_obj or exception_details or stack_trace:
            return calc_obj, exception_details, stack_trace

    return [], [], []


def resolve_exception_detail(exception, fallback=None):
    detail = None

    if exception is not None:
        _, exception_details, _ = find_exception_artifacts(exception)
        detail = select_preferred_exception_detail(exception_details)
        if not detail:
            detail = _normalize_string(exception)

    if detail:
        return detail

    return _normalize_string(fallback)


def resolve_exception_traceback(exception, fallback=None):
    stack_trace = None

    if exception is not None:
        _, _, stack_trace_chain = find_exception_artifacts(exception)
        stack_trace = select_preferred_stack_trace(stack_trace_chain)

    if stack_trace:
        return stack_trace

    return _normalize_string(fallback)


class ValidationError(Exception):
    """Custom validation error for rollback mechanism"""
    def __init__(self, message, original_exception=None, model_class=None):
        self.original_exception = original_exception
        self.model_class = model_class
        super().__init__(message)


class CalculationCancelled(Exception):
    """
    Raised by ``CalculationModel.check_cancelled()`` to cooperatively abort
    a running synchronous calculation.

    A customer's ``calculate()`` body can call ``self.check_cancelled()`` at
    safe interruption points (between loop iterations, between DB writes).
    When a user has clicked "Cancel" on the record, that call raises this
    exception and the framework settles the row in ``ABORTED`` state instead
    of ``ERROR`` — no error message, no traceback, just a clean cancellation
    that broadcasts ``calculation_aborted`` to the UI.

    Cooperative-only: a ``calculate()`` that never polls ``check_cancelled``
    cannot be hard-stopped on the sync route. The framework still settles
    the row as ``ABORTED`` (state-guard at SUCCESS write) so the UI reflects
    the user's intent, but the running calculation is allowed to finish.
    """

    def __init__(self, message: str = "Calculation cancelled by user"):
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
