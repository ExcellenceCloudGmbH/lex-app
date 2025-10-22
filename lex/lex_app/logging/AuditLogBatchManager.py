import logging
import traceback
from typing import List, Tuple, Optional
from django.db import transaction
from django.conf import settings
import os

from lex.lex_app.logging.AuditLog import AuditLog
from lex.lex_app.logging.AuditLogStatus import AuditLogStatus
from lex.lex_app.logging.config import get_batch_size

# Configure logger for batch operations
logger = logging.getLogger('lex_app.audit.batch_manager')


class AuditLogBatchManager:
    """
    Utility class for efficient batch operations on audit logs.
    
    This class manages batch updates to AuditLogStatus records to improve
    performance during initial data upload operations by reducing the number
    of database operations through batching.
    """
    
    def __init__(self, batch_size: Optional[int] = None):
        """
        Initialize the batch manager.
        
        Args:
            batch_size: Maximum number of operations to batch before flushing.
                       If not provided, uses the configured batch size from
                       audit logging configuration.
        """
        if batch_size is None:
            batch_size = get_batch_size()
        
        self.batch_size = batch_size
        self.pending_logs: List[AuditLog] = []
        self.success_updates: List[int] = []
        self.failure_updates: List[Tuple[int, str]] = []
    
    def add_pending_log(self, audit_log: AuditLog) -> None:
        """
        Add an audit log to the pending list for tracking.
        
        Args:
            audit_log: The audit log to track for future status updates
        """
        self.pending_logs.append(audit_log)
    
    def mark_success(self, audit_log: AuditLog) -> None:
        """
        Mark an audit log as successful and queue for batch update.
        
        Args:
            audit_log: The audit log to mark as successful
        """
        try:
            if audit_log is None:
                logger.warning("Attempted to mark None audit log as successful")
                return
                
            if not hasattr(audit_log, 'id') or audit_log.id is None:
                logger.warning("Attempted to mark audit log with no ID as successful")
                return
                
            self.success_updates.append(audit_log.id)
            logger.debug(f"Queued audit log {audit_log.id} for success update")
            self._check_and_flush()
        except Exception as e:
            logger.error(
                f"Failed to mark audit log as successful: {e}",
                extra={
                    'audit_log_id': getattr(audit_log, 'id', 'unknown'),
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
    
    def mark_failure(self, audit_log: AuditLog, error_msg: str) -> None:
        """
        Mark an audit log as failed and queue for batch update.
        
        Args:
            audit_log: The audit log to mark as failed
            error_msg: Error message or traceback to store with the failure
        """
        try:
            if audit_log is None:
                logger.warning("Attempted to mark None audit log as failed")
                return
                
            if not hasattr(audit_log, 'id') or audit_log.id is None:
                logger.warning("Attempted to mark audit log with no ID as failed")
                return
                
            # Truncate error message if it's too long to prevent database issues
            max_error_length = 10000  # Reasonable limit for error messages
            if len(error_msg) > max_error_length:
                truncated_msg = error_msg[:max_error_length] + "\n... [Error message truncated]"
                logger.warning(
                    f"Error message for audit log {audit_log.id} was truncated due to length ({len(error_msg)} chars)"
                )
                error_msg = truncated_msg
                
            self.failure_updates.append((audit_log.id, error_msg))
            logger.debug(f"Queued audit log {audit_log.id} for failure update")
            self._check_and_flush()
        except Exception as e:
            logger.error(
                f"Failed to mark audit log as failed: {e}",
                extra={
                    'audit_log_id': getattr(audit_log, 'id', 'unknown'),
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
    
    def _check_and_flush(self) -> None:
        """
        Check if batch size is reached and flush if necessary.
        
        This method automatically flushes the batch when the total number
        of pending updates reaches the configured batch size.
        """
        try:
            total_pending = len(self.success_updates) + len(self.failure_updates)
            if total_pending >= self.batch_size:
                logger.debug(
                    f"Batch size reached ({total_pending}/{self.batch_size}), triggering flush"
                )
                self.flush_batch()
        except Exception as e:
            logger.error(
                f"Error during batch size check: {e}",
                extra={
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
    
    def flush_batch(self) -> int:
        """
        Flush all pending batch operations to the database.
        
        This method performs bulk updates to AuditLogStatus records for both
        successful and failed operations. It uses database transactions to
        ensure consistency and handles errors gracefully to avoid breaking
        the data upload process.
        
        Returns:
            int: Number of AuditLogStatus records updated
        """
        updated_count = 0
        
        if not self.success_updates and not self.failure_updates:
            logger.debug("No pending batch operations to flush")
            return updated_count
        
        success_count = len(self.success_updates)
        failure_count = len(self.failure_updates)
        
        logger.debug(
            f"Flushing batch: {success_count} success updates, {failure_count} failure updates",
            extra={
                'success_count': success_count,
                'failure_count': failure_count,
                'batch_size': self.batch_size
            }
        )
        
        try:
            with transaction.atomic():
                # Batch update successful operations
                if self.success_updates:
                    try:
                        success_updated = AuditLogStatus.objects.filter(
                            auditlog_id__in=self.success_updates
                        ).update(status='success')
                        updated_count += success_updated
                        
                        logger.debug(
                            f"Successfully updated {success_updated} audit log statuses to success",
                            extra={'success_updated': success_updated}
                        )
                        
                        self.success_updates.clear()
                    except Exception as e:
                        logger.error(
                            f"Failed to batch update success statuses: {e}",
                            extra={
                                'success_ids': self.success_updates,
                                'error': str(e),
                                'traceback': traceback.format_exc()
                            }
                        )
                        raise  # Re-raise to trigger outer exception handling
                
                # Batch update failed operations
                # Note: We can't use bulk_update for failures because each has different error messages
                if self.failure_updates:
                    try:
                        failure_updated = 0
                        for audit_log_id, error_msg in self.failure_updates:
                            try:
                                count = AuditLogStatus.objects.filter(
                                    auditlog_id=audit_log_id
                                ).update(status='failure', error_traceback=error_msg)
                                failure_updated += count
                            except Exception as e:
                                logger.error(
                                    f"Failed to update individual failure status for audit log {audit_log_id}: {e}",
                                    extra={
                                        'audit_log_id': audit_log_id,
                                        'error_msg': error_msg,
                                        'error': str(e)
                                    }
                                )
                                # Continue with other updates even if one fails
                        
                        updated_count += failure_updated
                        
                        logger.debug(
                            f"Successfully updated {failure_updated} audit log statuses to failure",
                            extra={'failure_updated': failure_updated}
                        )
                        
                        self.failure_updates.clear()
                    except Exception as e:
                        logger.error(
                            f"Failed to batch update failure statuses: {e}",
                            extra={
                                'failure_updates_count': len(self.failure_updates),
                                'error': str(e),
                                'traceback': traceback.format_exc()
                            }
                        )
                        raise  # Re-raise to trigger outer exception handling
                        
        except Exception as e:
            # Log the error but don't raise it to avoid breaking the data upload process
            error_traceback = traceback.format_exc()
            logger.error(
                f"Critical error during batch flush: {e}",
                extra={
                    'success_count': len(self.success_updates),
                    'failure_count': len(self.failure_updates),
                    'error': str(e),
                    'traceback': error_traceback
                }
            )
            
            # Clear the batches even on error to prevent infinite retry loops
            self.success_updates.clear()
            self.failure_updates.clear()
        
        logger.info(
            f"Batch flush completed: {updated_count} records updated",
            extra={'updated_count': updated_count}
        )
        
        return updated_count
    
    def get_pending_count(self) -> int:
        """
        Get the number of pending operations waiting to be flushed.
        
        Returns:
            int: Total number of pending success and failure updates
        """
        return len(self.success_updates) + len(self.failure_updates)
    
    def get_batch_size(self) -> int:
        """
        Get the configured batch size.
        
        Returns:
            int: The batch size used for automatic flushing
        """
        return self.batch_size
    
    def clear_pending(self) -> None:
        """
        Clear all pending operations without flushing them.
        
        This method can be used to reset the batch manager state,
        typically in error recovery scenarios.
        """
        try:
            pending_count = self.get_pending_count()
            self.pending_logs.clear()
            self.success_updates.clear()
            self.failure_updates.clear()
            
            logger.warning(
                f"Cleared {pending_count} pending operations without flushing",
                extra={'cleared_count': pending_count}
            )
        except Exception as e:
            logger.error(
                f"Error during pending operations cleanup: {e}",
                extra={
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
    
    def emergency_flush_and_clear(self) -> int:
        """
        Emergency method to attempt flushing and then clear all pending operations.
        
        This method is designed for error recovery scenarios where we want to
        try to save what we can before clearing the batch state.
        
        Returns:
            int: Number of records successfully updated before clearing
        """
        updated_count = 0
        try:
            logger.warning("Performing emergency flush and clear of batch operations")
            updated_count = self.flush_batch()
        except Exception as e:
            logger.error(
                f"Emergency flush failed: {e}",
                extra={
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
        finally:
            # Always clear pending operations to prevent infinite loops
            self.clear_pending()
            
        return updated_count