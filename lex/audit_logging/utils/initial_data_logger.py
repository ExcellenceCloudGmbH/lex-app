import logging
import traceback
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Dict, Any, Optional, Type
from django.db import models, transaction
from django.db.models import Model

from lex.audit_logging.models.audit_log import AuditLog
from lex.audit_logging.models.audit_log_status import AuditLogStatus
from lex.audit_logging.serializers.audit_log_mixin_serializer import _serialize_payload, generic_instance_payload
# from lex.audit_logging.models.audit_logBatchManager import AuditLogBatchManager  # TODO: This class doesn't exist
from lex.process_admin.settings import processAdminSite
from lex.audit_logging.mixins.audit_mixin import AuditLogMixin

# Configure logger for audit operations
logger = logging.getLogger('lex_app.audit.initial_data')


class InitialDataAuditLogger:
    """
    Audit logger for initial data upload operations.
    
    This class provides audit logging functionality for data operations that occur
    during initial data upload, ensuring consistency with the existing audit trail
    while supporting batch operations for performance.
    """
    
    def __init__(self, calculation_id: Optional[str] = None):
        """
        Initialize the audit logger.
        
        Args:
            calculation_id: Optional calculation ID. If not provided, a unique ID will be generated.
        """
        # self.batch_manager = AuditLogBatchManager()  # TODO: Implement AuditLogBatchManager
        self.batch_manager = None  # Placeholder until AuditLogBatchManager is implemented
    def generate_calculation_id(self) -> str:
        return self._generate_calculation_id()


    def _generate_calculation_id(self) -> str:
        """Generate a unique calculation ID for initial data upload sessions."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"initial_data_upload_{timestamp}_{unique_id}"
    
    def log_object_creation(self, model_class: Type[Model], instance_data: Dict[str, Any], 
                          tag: Optional[str] = None, calculation_id=None) -> Optional[AuditLog]:
        """
        Log object creation operation.
        
        Args:
            model_class: The Django model class being created
            instance_data: Dictionary containing the data used to create the instance
            tag: Optional tag to identify this operation in the audit trail
            
        Returns:
            AuditLog: The created audit log entry, or None if logging failed
        """



        try:
            resource = model_class.__name__.lower()
            
            # Safely serialize payload with error handling
            try:
                payload = _serialize_payload(instance_data)
            except Exception as e:
                logger.warning(
                    f"Failed to serialize payload for {resource} creation. "
                    f"Using fallback serialization. Error: {e}",
                    extra={
                        'calculation_id': calculation_id,
                        'resource': resource,
                        'action': 'create',
                        'tag': tag
                    }
                )
                # Fallback to basic serialization
                payload = {'_serialization_error': str(e), '_original_keys': list(instance_data.keys())}


            # Add tag information to payload if provided
            if tag:
                payload['_audit_tag'] = tag
            
            # Create audit log with transaction safety
            with transaction.atomic():
                audit_log = AuditLog.objects.create(
                    author="system (initial_data_upload)",
                    resource=resource,
                    action="create",
                    payload=payload,
                    calculation_id=calculation_id
                )
                
                # Create initial status record
                AuditLogStatus.objects.create(audit_log=audit_log, status='pending')
                if self.batch_manager:
                    self.batch_manager.add_pending_log(audit_log)
            
            logger.debug(
                f"Successfully created audit log for {resource} creation",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'resource': resource,
                    'action': 'create',
                    'tag': tag
                }
            )
            
            return audit_log
            
        except Exception as e:
            error_msg = f"Failed to create audit log for {model_class.__name__} creation: {e}"
            logger.error(
                error_msg,
                extra={
                    'calculation_id': calculation_id,
                    'resource': model_class.__name__.lower(),
                    'action': 'create',
                    'tag': tag,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            # Don't raise exception to avoid breaking data upload process
            return None
    
    def log_object_update(self, model_class: Type[Model], instance: Model, 
                         update_data: Dict[str, Any], tag: Optional[str] = None, calculation_id=None) -> Optional[AuditLog]:
        """
        Log object update operation.
        
        Args:
            model_class: The Django model class being updated
            instance: The model instance being updated
            update_data: Dictionary containing the update data
            tag: Optional tag to identify this operation in the audit trail
            
        Returns:
            AuditLog: The created audit log entry, or None if logging failed
        """
        try:
            resource = model_class.__name__.lower()

            # Safely get instance primary key
            try:
                instance_pk = instance.pk
            except Exception as e:
                logger.warning(
                    f"Failed to get primary key for {resource} update. Using fallback.",
                    extra={
                        'calculation_id': calculation_id,
                        'resource': resource,
                        'action': 'update',
                        'tag': tag,
                        'error': str(e)
                    }
                )
                instance_pk = 'unknown'
            
            # Safely serialize update data
            try:
                serialized_updates = _serialize_payload(update_data)
            except Exception as e:
                logger.warning(
                    f"Failed to serialize update data for {resource}. Using fallback serialization.",
                    extra={
                        'calculation_id': calculation_id,
                        'resource': resource,
                        'action': 'update',
                        'tag': tag,
                        'instance_pk': instance_pk,
                        'error': str(e)
                    }
                )
                # Fallback to basic serialization
                serialized_updates = {'_serialization_error': str(e), '_original_keys': list(update_data.keys())}
            
            # Create payload with both old and new values for better audit trail

            serialized_updates['id'] = instance_pk
            payload = deepcopy(update_data)
            payload['id'] = instance_pk
            payload['updates'] = serialized_updates

            # Add tag information to payload if provided
            if tag:
                payload['_audit_tag'] = tag
            
            # Create audit log with transaction safety
            with transaction.atomic():
                audit_log = AuditLog.objects.create(
                    author="system (initial_data_upload)",
                    resource=resource,
                    action="update",
                    payload=payload,
                    calculation_id=calculation_id
                )
                
                # Create initial status record
                AuditLogStatus.objects.create(audit_log=audit_log, status='pending')
                if self.batch_manager:
                    self.batch_manager.add_pending_log(audit_log)
            
            logger.debug(
                f"Successfully created audit log for {resource} update",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'resource': resource,
                    'action': 'update',
                    'instance_pk': instance_pk,
                    'tag': tag
                }
            )
            
            return audit_log
            
        except Exception as e:
            error_msg = f"Failed to create audit log for {model_class.__name__} update: {e}"
            logger.error(
                error_msg,
                extra={
                    'calculation_id': calculation_id,
                    'resource': model_class.__name__.lower(),
                    'action': 'update',
                    'tag': tag,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            # Don't raise exception to avoid breaking data upload process
            return None
    
    def log_object_deletion(self, instance: Model, filter_params: Dict[str, Any],
                          tag: Optional[str] = None) -> Optional[AuditLog]:
        """
        Log object deletion operation.
        
        Args:
            model_class: The Django model class being deleted from
            filter_params: Dictionary containing the filter parameters used for deletion
            tag: Optional tag to identify this operation in the audit trail
            
        Returns:
            AuditLog: The created audit log entry, or None if logging failed
        """
        model_class = instance.__class__
        payload = {}



        resource = model_class.__name__.lower()
        try:
            # Safely serialize filter parameters
            try:
                # model_container = processAdminSite.get_container_func(model_class.__name__)
                # mapping = model_container.serializers_map
                # serializer = mapping['default']
                payload = generic_instance_payload(instance)
            except Exception as e:
                logger.warning(
                    f"Failed to serialize filter parameters for {resource} deletion. Using fallback serialization.",
                    extra={
                        'calculation_id': None,
                        'resource': resource,
                        'action': 'delete',
                        'tag': tag,
                        'error': str(e)
                    }
                )
                # Fallback to basic serialization
                serialized_filters = {'_serialization_error': str(e), '_original_keys': list(filter_params.keys())}
            
            # payload = {
            #     'id': instance.pk,
            #     'filter_parameters': serialized_filters
            # }
            
            # Add tag information to payload if provided
            if tag:
                payload['_audit_tag'] = tag
            
            # Create audit log with transaction safety
            with transaction.atomic():
                audit_log = AuditLog.objects.create(
                    author="system (initial_data_upload)",
                    resource=resource,
                    action="delete",
                    payload=payload,
                    calculation_id=None
                )
                
                # Create initial status record
                AuditLogStatus.objects.create(audit_log=audit_log, status='pending')
                if self.batch_manager:
                    self.batch_manager.add_pending_log(audit_log)
            
            logger.debug(
                f"Successfully created audit log for {resource} deletion",
                extra={
                    'calculation_id': None,
                    'audit_log_id': audit_log.id,
                    'resource': resource,
                    'action': 'delete',
                    'tag': tag
                }
            )
            
            return audit_log
            
        except Exception as e:
            error_msg = f"Failed to create audit log for {model_class.__name__} deletion: {e}"
            logger.error(
                error_msg,
                extra={
                    'calculation_id': None,
                    'resource': model_class.__name__.lower(),
                    'action': 'delete',
                    'tag': tag,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            # Don't raise exception to avoid breaking data upload process
            return None
    
    def mark_operation_success(self, audit_log: AuditLog) -> None:
        """
        Mark an audit log operation as successful.
        
        Args:
            audit_log: The audit log to mark as successful
        """
        if audit_log is None:
            logger.debug("Skipping success marking for None audit log")
            return

        calculation_id = audit_log.calculation_id
        try:
            if self.batch_manager:
                self.batch_manager.mark_success(audit_log)
            logger.debug(
                f"Marked audit log {audit_log.id} as successful",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'resource': audit_log.resource,
                    'action': audit_log.action
                }
            )
        except Exception as e:
            logger.error(
                f"Failed to mark audit log {audit_log.id} as successful: {e}",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            # Don't raise exception to avoid breaking data upload process
    
    def mark_operation_failure(self, audit_log: AuditLog, error_msg: str) -> None:
        """
        Mark an audit log operation as failed.
        
        Args:
            audit_log: The audit log to mark as failed
            error_msg: Error message or traceback
        """
        if audit_log is None:
            logger.debug("Skipping failure marking for None audit log")
            return


        calculation_id = audit_log.calculation_id

        try:
            if self.batch_manager:
                self.batch_manager.mark_failure(audit_log, error_msg)
            logger.warning(
                f"Marked audit log {audit_log.id} as failed: {error_msg}",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'resource': audit_log.resource,
                    'action': audit_log.action,
                    'error_message': error_msg
                }
            )
        except Exception as e:
            logger.error(
                f"Failed to mark audit log {audit_log.id} as failed: {e}",
                extra={
                    'calculation_id': calculation_id,
                    'audit_log_id': audit_log.id,
                    'error': str(e),
                    'original_error_msg': error_msg,
                    'traceback': traceback.format_exc()
                }
            )
            # Don't raise exception to avoid breaking data upload process
    
    def finalize_batch(self) -> Dict[str, Any]:
        """
        Finalize the batch and return summary statistics.
        
        Returns:
            Dict containing summary statistics of the audit logging session
        """
        calculation_id = None
        try:
            logger.info(
                f"Finalizing audit logging batch for calculation ID: {calculation_id}",
                extra={'calculation_id': calculation_id}
            )

            # Flush any remaining batch operations
            updated_count = 0
            try:
                if self.batch_manager:
                    updated_count = self.batch_manager.flush_batch()
                logger.debug(
                    f"Flushed {updated_count} batch operations",
                    extra={'calculation_id': calculation_id, 'updated_count': updated_count}
                )
            except Exception as e:
                logger.error(
                    f"Failed to flush batch operations: {e}",
                    extra={
                        'calculation_id': calculation_id,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    }
                )
            
            # Generate summary statistics with error handling
            summary = {
                'calculation_id': calculation_id,
                'total_audit_logs': 0,
                'successful_operations': 0,
                'failed_operations': 0,
                'pending_operations': 0,
                'batch_updates_processed': updated_count,
                'statistics_errors': []
            }
            return summary

        #     try:
        #         summary['total_audit_logs'] = AuditLog.objects.filter(calculation_id=calculation_id).count()
        #     except Exception as e:
        #         error_msg = f"Failed to count total audit logs: {e}"
        #         summary['statistics_errors'].append(error_msg)
        #         logger.error(error_msg, extra={'calculation_id': calculation_id})
        #
        #     try:
        #         summary['successful_operations'] = AuditLogStatus.objects.filter(
        #             audit_log=calculation_id,
        #             status='success'
        #         ).count()
        #     except Exception as e:
        #         error_msg = f"Failed to count successful operations: {e}"
        #         summary['statistics_errors'].append(error_msg)
        #         logger.error(error_msg, extra={'calculation_id': calculation_id})
        #
        #     try:
        #         summary['failed_operations'] = AuditLogStatus.objects.filter(
        #             audit_log=calculation_id,
        #             status='failure'
        #         ).count()
        #     except Exception as e:
        #         error_msg = f"Failed to count failed operations: {e}"
        #         summary['statistics_errors'].append(error_msg)
        #         logger.error(error_msg, extra={'calculation_id': calculation_id})
        #
        #     try:
        #         summary['pending_operations'] = AuditLogStatus.objects.filter(
        #             audit_log=calculation_id,
        #             status='pending'
        #         ).count()
        #     except Exception as e:
        #         error_msg = f"Failed to count pending operations: {e}"
        #         summary['statistics_errors'].append(error_msg)
        #         logger.error(error_msg, extra={'calculation_id': calculation_id})
        #
        #     # Log summary
        #     logger.info(
        #         f"Audit logging finalization complete. "
        #         f"Total: {summary['total_audit_logs']}, "
        #         f"Success: {summary['successful_operations']}, "
        #         f"Failed: {summary['failed_operations']}, "
        #         f"Pending: {summary['pending_operations']}",
        #         extra={
        #             'calculation_id': calculation_id,
        #             'summary': summary
        #         }
        #     )
        #
        #     # Remove empty statistics_errors list for cleaner output
        #     if not summary['statistics_errors']:
        #         del summary['statistics_errors']
        #
        #     return summary

        except Exception as e:
            error_msg = f"Critical error during batch finalization: {e}"
            logger.error(
                error_msg,
                extra={
                    'calculation_id': calculation_id,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            # Return minimal summary on critical error
            return {
                'calculation_id': calculation_id,
                'finalization_error': error_msg,
                'batch_updates_processed': 0
            }