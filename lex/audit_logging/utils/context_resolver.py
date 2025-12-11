"""
Context resolver for the improved CalculationLog system.

This module provides the ContextResolver class that integrates the dual context
system (operation context and model context) into a unified ContextInfo object
for use in calculation logging operations.
"""

import logging
from typing import Optional

from django.contrib.contenttypes.models import ContentType

from lex.api.utils import operation_context
from lex.audit_logging.utils.model_context import _model_context
from lex.audit_logging.utils.data_models import ContextInfo, ContextResolutionError

logger = logging.getLogger(__name__)


class ContextResolver:
    """
    Resolves and integrates dual context systems for calculation logging.
    
    This class combines operation context (from operation_context) and model context
    (from ModelContext) into a unified ContextInfo object that can be used
    throughout the calculation logging system.
    """
    
    @staticmethod
    def resolve() -> ContextInfo:
        """
        Resolve current context by integrating both context systems.
        
        Extracts:
        - Operation context from operation_context (calculation_id, request_obj)
        - Model context from ModelContext (current/parent models)
        
        Returns:
            ContextInfo: Unified context information for logging operations
            
        Raises:
            ContextResolutionError: When required context information is missing
                or when AuditLog cannot be resolved
        """
        try:
            # Extract calculation_id from operation context
            context_data = operation_context.get()
            calculation_id = context_data.get('calculation_id')
            
            if not calculation_id:
                raise ContextResolutionError(
                    "Missing calculation_id in operation context",
                    calculation_id=calculation_id
                )
            
            # Resolve AuditLog using calculation_id
            # Import here to avoid circular dependency
            from lex.audit_logging.models.audit_log import AuditLog
            
            try:
                audit_log = context_data.get('audit_log_temp') or AuditLog.objects.get(calculation_id=calculation_id)
                if audit_log.calculation_id == None:
                    audit_log.calculation_id = calculation_id
                    audit_log.save()

            except AuditLog.DoesNotExist:
                raise ContextResolutionError(
                    f"AuditLog not found for calculation_id: {calculation_id}",
                    calculation_id=calculation_id
                )
            except Exception as e:
                raise ContextResolutionError(
                    f"Error retrieving AuditLog for calculation_id {calculation_id}: {str(e)}",
                    calculation_id=calculation_id
                )
            
            # Extract model context using new ModelContext structure
            model_ctx = _model_context.get()['model_context']
            
            # Determine current and parent models using direct property access
            current_model = model_ctx.current
            parent_model = model_ctx.parent
            current_record = None
            parent_record = None
            content_type = None
            parent_content_type = None
            root = model_ctx.get_root()
            root_record = None
            
            # Process current model if it exists
            if current_model:
                try:
                    content_type = ContentType.objects.get_for_model(current_model)
                    current_record = f"{current_model._meta.model_name}_{current_model.pk}"
                except Exception as e:
                    logger.warning(
                        f"Error resolving ContentType for current model: {e}",
                        extra={'calculation_id': calculation_id}
                    )
            if root:
                try:
                    root_record = f"{root._meta.model_name}_{root.pk}"
                except Exception as e:
                    logger.warning(
                        f"Error resolving ContentType for root model: {e}",
                        extra={'calculation_id': calculation_id}
                    )

            # Process parent model if it exists
            if parent_model:
                try:
                    parent_content_type = ContentType.objects.get_for_model(parent_model)
                    parent_record = f"{parent_model._meta.model_name}_{parent_model.pk}"
                except Exception as e:
                    logger.warning(
                        f"Error resolving ContentType for parent model: {e}",
                        extra={'calculation_id': calculation_id}
                    )
            
            # Create and return unified ContextInfo
            context_info = ContextInfo(
                calculation_id=calculation_id,
                audit_log=audit_log,
                current_model=current_model,
                parent_model=parent_model,
                current_record=current_record,
                parent_record=parent_record,
                content_type=content_type,
                parent_content_type=parent_content_type,
                root_record=root_record
            )
            
            logger.debug(
                f"Context resolved successfully for calculation_id: {calculation_id}",
                extra={
                    'calculation_id': calculation_id,
                    'has_current_model': current_model is not None,
                    'has_parent_model': parent_model is not None
                }
            )
            
            return context_info
            
        except ContextResolutionError:
            # Re-raise ContextResolutionError as-is
            raise
        except Exception as e:
            # Wrap any other exceptions in ContextResolutionError
            calculation_id = None
            try:
                calculation_id = operation_context.get('calculation_id')
            except Exception:
                pass
            
            raise ContextResolutionError(
                f"Unexpected error during context resolution: {str(e)}",
                calculation_id=calculation_id,
                has_current_model=model_ctx.current,
                has_parent_model=model_ctx.parent
            )