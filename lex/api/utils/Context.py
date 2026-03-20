import contextvars
import sys
from copy import deepcopy
from typing import Dict, Any
from uuid import uuid4

_DEFAULT_CONTEXT = {
    'operation_id': '',
    'request_obj': '',
    'calculation_id': '',
    'audit_log_temp': None,
    'actor': None,
}


def _build_context(request='', calculation_id='', audit_log=None, actor=None, operation_id=''):
    return {
        'operation_id': operation_id,
        'request_obj': request,
        'calculation_id': calculation_id,
        'audit_log_temp': audit_log,
        'actor': actor,
    }


# Define a context variable with meaningful name and proper type annotation
operation_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'operation_context',
    default=_DEFAULT_CONTEXT.copy()
)

for module_alias in ('api.utils.Context', 'lex.api.utils.Context'):
    if module_alias != __name__:
        sys.modules.setdefault(module_alias, sys.modules[__name__])

# Context manager to set operation id
class OperationContext:
    
    def __init__(self, request=None, calculation_id=None, audit_log=None, actor=None):
        self.request = request
        self.calculation_id = calculation_id
        self.audit_log = audit_log
        self.actor = actor
        self._token = None

    def __enter__(self):
        current_context = dict(operation_context.get() or {})

        if current_context.get('operation_id'):
            next_context = {**current_context}
            if self.request not in (None, ''):
                next_context['request_obj'] = self.request
            if self.calculation_id is not None:
                next_context['calculation_id'] = self.calculation_id
            if self.audit_log is not None:
                next_context['audit_log_temp'] = self.audit_log
            if self.actor is not None:
                next_context['actor'] = self.actor
        else:
            next_context = _build_context(
                request=self.request or '',
                calculation_id=self.calculation_id or '',
                audit_log=self.audit_log,
                actor=self.actor,
                operation_id=str(uuid4()),
            )

        self._token = operation_context.set(next_context)
        return next_context


    @staticmethod
    def extract_info_request(request):
        info_to_extract = ['user']
        return {key:deepcopy(getattr(request, key)) for key in info_to_extract if hasattr(request, key)}



    @staticmethod
    def get_request():
        return operation_context.get()['request_obj']

    @staticmethod
    def get_calc_id():
        return operation_context.get().get('calculation_id', None)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            operation_context.reset(self._token)
