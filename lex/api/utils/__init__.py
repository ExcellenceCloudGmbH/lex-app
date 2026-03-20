import sys

from .helpers import (
    convert_dfs_in_excel,
    resolve_target_model,
    build_shadow_instance,
    can_read_from_payload,
)
from .collection_utils import flatten
from .converters import create_model_converter
from .Context import operation_context, OperationContext
from .temporal import parse_as_of_datetime

for module_alias in ('api.utils', 'lex.api.utils'):
    if module_alias != __name__:
        sys.modules.setdefault(module_alias, sys.modules[__name__])

__all__ = [
    'convert_dfs_in_excel',
    'resolve_target_model',
    'build_shadow_instance',
    'can_read_from_payload',
    'flatten',
    'create_model_converter',
    'operation_context',
    'OperationContext',
    'parse_as_of_datetime',
]
