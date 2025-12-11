from datetime import datetime
import logging

from django.db import models

from lex.core.mixins.modification_restriction import (
    AdminReportsModificationRestriction,
)
from django.contrib.contenttypes.fields import GenericForeignKey
from lex.core.models.base import LexModel
from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.utils.context_resolver import ContextResolver
from lex.audit_logging.utils.cache_manager import CacheManager
from lex.audit_logging.utils.websocket_notifier import WebSocketNotifier
from lex.audit_logging.utils.data_models import (
    CalculationLogError,
    ContextResolutionError,
    CacheOperationError
)

#### Note: Messages shall be delivered in the following format: "Severity: Message" The colon and the whitespace after are required for the code to work correctly ####
# Severity could be something like 'Error', 'Warning', 'Caution', etc. (See Static variables below!)


class CalculationLog(models.Model):
    modification_restriction = AdminReportsModificationRestriction()
    id = models.AutoField(primary_key=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    calculationId = models.TextField(default="test_id")
    calculation_log = models.TextField(default="")
    parent_log = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="parent_logs",
    )  # parent calculation log
    audit_log = models.ForeignKey(
        "audit_logging.AuditLog", on_delete=models.CASCADE, null=True, blank=True
    )
    # Generic fields to reference any calculatable object:
    # If you want to allow CalculationLog entries without a related instance,
    # consider setting null=True and blank=True. Otherwise, ensure an instance is always found.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    # This generic foreign key ties the above two fields, allowing dynamic reference.
    calculatable_object = GenericForeignKey("content_type", "object_id")

    # Severities – to be concatenated with the message in the create statement
    SUCCESS = "Success: "
    WARNING = "Warning: "
    ERROR = "Error: "
    START = "Start: "
    FINISH = "Finish: "

    # Message types
    PROGRESS = "Progress"
    INPUT = "Input Validation"
    OUTPUT = "Output Validation"

    class Meta:
        app_label = "audit_logging"

    @classmethod
    def log(cls, message: str):
        """
        Logs `message` against the current model context.
        
        This method has been refactored to use helper classes for better organization
        and error handling while maintaining full backward compatibility.
        
        Args:
            message: The log message to record
        """

        logger = logging.getLogger("lex.calclog")
        try:
            # 1) Resolve context using ContextResolver

            context_info = ContextResolver.resolve()
            # logger.warning(f"Context: {context_info}")


            parent_debug = None
            log_debug = None



            # 2) Create parent log entry if needed
            parent_log = None
            if context_info.parent_model and context_info.parent_content_type:
                parent_debug = {"calculationId": context_info.calculation_id,
                 "audit_log": context_info.audit_log,
                 "content_type": context_info.parent_content_type,
                 "object_id": context_info.parent_model.pk}
                parent_log, _ = cls.objects.get_or_create(
                    calculationId=context_info.calculation_id,
                    audit_log=context_info.audit_log,
                    content_type=context_info.parent_content_type,
                    object_id=context_info.parent_model.pk,
                )
            
            # 3) Create or get current log entry
            current_model_pk = context_info.current_model.pk if context_info.current_model else None

            log_debug = {"calc_id": context_info.calculation_id,
                 "audit_log": context_info.audit_log,
                 "content_type": context_info.content_type,
                 "object_id": current_model_pk,
                 "calclog": parent_log}

            # TODO: Test this
            log_entry, _ = cls.objects.get_or_create(
                calculationId=context_info.calculation_id,
                audit_log=context_info.audit_log,
                content_type=context_info.content_type,
                object_id=current_model_pk,
                parent_log=parent_log,
            )


            logger.info(f"Log: {log_debug}")
            logger.info(f"Parent: {parent_debug}")
            # 4) Append message and save
            log_entry.calculation_log = (log_entry.calculation_log or "") + f"\n{message}"
            log_entry.save()


            root_record = context_info.root_record

            # logger.warning(f"The context_info from CalculationLog, context: {context_info}")
            # 6) Store in cache using CacheManager
            if context_info.current_record:
                cache_key = CacheManager.build_cache_key(
                    context_info.current_record,
                    context_info.calculation_id
                )
                CacheManager.store_message(cache_key, message)
            
            # 7) Log to standard logger
            logger.debug(
                message,
                extra={
                    "calculation_record": context_info.current_record,
                    "calculationId": context_info.calculation_id,
                },
            )
            
        except ContextResolutionError as e:
            # Handle context resolution errors gracefully
            logger.error(
                f"Context resolution failed for log message: {message}. Error: {str(e)}",
                extra={
                    "calculation_id": getattr(e, 'calculation_id', None),
                    "stack_length": getattr(e, 'stack_length', None),
                },
                exc_info=True
            )
            # Continue with minimal logging to ensure message is not lost
            logger.warning(f"Fallback logging: {message}")
            
        except Exception as e:
            # Handle any other unexpected errors
            logger.info(f"ERROR IN CALCULATION LOG")
            logger.info(f"Log: {log_debug}")
            logger.info(f"Parent: {parent_debug}")
            logger.error(
                f"Unexpected error in CalculationLog.log() for message: {message}. Error: {str(e)}",
                exc_info=True
            )
            # Ensure the message is still logged somewhere
            logger.warning(f"Fallback logging: {message}")