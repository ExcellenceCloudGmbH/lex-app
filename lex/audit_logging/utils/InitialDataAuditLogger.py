import logging
import traceback
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Type, List

from django.db import models, transaction
from django.db.models import Model
from django.utils import timezone
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.serializers.AuditLogMixinSerializer import _serialize_payload, generic_instance_payload

# Configure logger for audit operations
logger = logging.getLogger('lex_app.audit.initial_data')


class InitialDataAuditLogger:
    """
    Audit logger for initial data upload operations.

    This class provides audit logging functionality for data operations that occur
    during initial data upload, ensuring consistency with the existing audit trail.

    Each create/update/delete operation produces one AuditLog + AuditLogStatus pair,
    matching the per-operation approach used by AuditLogMixin for API requests.
    """

    def __init__(self):
        """Initialize the audit logger with an empty tracking list."""
        self._logged_ids: List[int] = []

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

            self._logged_ids.append(audit_log.id)

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
                          update_data: Dict[str, Any], tag: Optional[str] = None, calculation_id=None) -> Optional[
        AuditLog]:
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

            # Create payload from serialized data for JSON safety
            serialized_updates['id'] = instance_pk
            payload = serialized_updates

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

            self._logged_ids.append(audit_log.id)

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
                payload = serialized_filters

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

            self._logged_ids.append(audit_log.id)

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
            updated_count = AuditLogStatus.objects.filter(audit_log=audit_log).update(
                status='success',
                error_traceback=None,
                updated_at=timezone.now(),
            )
            if updated_count == 0:
                AuditLogStatus.objects.create(
                    audit_log=audit_log,
                    status='success',
                    error_traceback=None
                )
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
            updated_count = AuditLogStatus.objects.filter(audit_log=audit_log).update(
                status='failure',
                error_traceback=error_msg,
                updated_at=timezone.now(),
            )
            if updated_count == 0:
                AuditLogStatus.objects.create(
                    audit_log=audit_log,
                    status='failure',
                    error_traceback=error_msg
                )
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

    def finalize_batch(self, failure_error=None) -> Dict[str, Any]:
        """
        Finalize the audit logging session.

        Resolves any remaining ``pending`` AuditLogStatus rows that were not
        explicitly marked during operation (safety net for crashes) and returns
        summary statistics for the entire initial-data-upload run.

        Args:
            failure_error: If set, all remaining pending statuses are marked
                           as ``failure`` with this message.  Otherwise they
                           are marked ``success``.

        Returns:
            Dict containing summary statistics of the audit logging session.
        """
        try:
            logged_ids = list(self._logged_ids)
            logger.info(
                f"Finalizing audit logging session — {len(logged_ids)} operations tracked",
            )

            # Safety net: resolve any remaining pending statuses
            pending_resolved = 0
            try:
                pending_qs = AuditLogStatus.objects.filter(
                    audit_log_id__in=logged_ids,
                    status='pending',
                )
                if failure_error:
                    pending_resolved = pending_qs.update(
                        status='failure',
                        error_traceback=failure_error,
                    )
                else:
                    pending_resolved = pending_qs.update(
                        status='success',
                        error_traceback=None,
                    )
                if pending_resolved:
                    logger.warning(
                        f"Resolved {pending_resolved} lingering pending status(es) "
                        f"as {'failure' if failure_error else 'success'}"
                    )
            except Exception as e:
                logger.error(f"Failed to resolve pending statuses: {e}", exc_info=True)

            # Gather summary statistics
            summary: Dict[str, Any] = {
                'total_audit_logs': len(logged_ids),
                'successful_operations': 0,
                'failed_operations': 0,
                'pending_operations': 0,
                'pending_resolved': pending_resolved,
            }

            try:
                status_counts = (
                    AuditLogStatus.objects
                    .filter(audit_log_id__in=logged_ids)
                    .values('status')
                    .annotate(count=models.Count('id'))
                )
                for entry in status_counts:
                    if entry['status'] == 'success':
                        summary['successful_operations'] = entry['count']
                    elif entry['status'] == 'failure':
                        summary['failed_operations'] = entry['count']
                    elif entry['status'] == 'pending':
                        summary['pending_operations'] = entry['count']
            except Exception as e:
                logger.error(f"Failed to compute summary statistics: {e}", exc_info=True)
                summary['statistics_error'] = str(e)

            logger.info(
                f"Audit logging finalization complete. "
                f"Total: {summary['total_audit_logs']}, "
                f"Success: {summary['successful_operations']}, "
                f"Failed: {summary['failed_operations']}, "
                f"Pending: {summary['pending_operations']}"
            )

            return summary

        except Exception as e:
            error_msg = f"Critical error during finalization: {e}"
            logger.error(error_msg, exc_info=True)
            return {
                'total_audit_logs': len(self._logged_ids),
                'finalization_error': error_msg,
            }