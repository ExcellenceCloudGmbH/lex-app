from datetime import datetime
import logging

from django.db import models, transaction

from lex.core.mixins.ModelModificationRestriction import (
    AdminReportsModificationRestriction,
)
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.DataModels import (
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
    calculationId = models.TextField(default="test_id", db_index=True)
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
    # Title of an object-less section node (``model_logging_context("...")``).
    # Heading rows carry the title here and leave content_type/object_id NULL;
    # model-backed rows leave this NULL.
    heading = models.TextField(null=True, blank=True)

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

    def __str__(self):
        # Heading nodes show their section title in the log tree. Model-backed
        # nodes keep Django's default string, which the frontend recognizes
        # and relabels ("Consolidated log" / "Section N").
        if self.heading:
            return self.heading
        return f"CalculationLog object ({self.pk})"

    @classmethod
    def _get_or_create_locked(cls, logger, **lookup_kwargs):
        existing_entries = list(
            cls.objects.filter(**lookup_kwargs).order_by("id")[:2]
        )
        if existing_entries:
            if len(existing_entries) > 1:
                logger.warning(
                    "Detected duplicate CalculationLog rows for %s; using id=%s.",
                    lookup_kwargs,
                    existing_entries[0].id,
                )
            return existing_entries[0], False

        return cls.objects.create(**lookup_kwargs), True

    @classmethod
    def _ensure_heading_node(cls, frame, parent_node, context_info, logger):
        """
        Get-or-create the row for a LogHeading frame, lazily on first log.

        The pk is cached on the frame so subsequent log calls inside the same
        with-block skip the lookup. Keyed by (calculationId, audit_log,
        heading, parent_log): the same title under two different parents is
        two nodes, while re-entering the same heading under the same parent
        reuses one node.
        """
        if frame.node_id:
            node = cls.objects.filter(pk=frame.node_id).first()
            if node is not None:
                return node
        node, _ = cls._get_or_create_locked(
            logger,
            calculationId=context_info.calculation_id,
            audit_log=context_info.audit_log,
            content_type=None,
            object_id=None,
            heading=frame.title,
            parent_log=parent_node,
        )
        frame.node_id = node.pk
        return node

    @classmethod
    def _persist_message(cls, context_info, message: str, logger):
        from lex.audit_logging.utils.ModelContext import LogHeading

        parent_debug = None
        log_debug = None
        parent_log = None
        audit_log_id = getattr(context_info.audit_log, "pk", None)

        frames = getattr(context_info, "frames", None) or []
        # Heading frames sitting between the nearest model parent and the top
        # of the stack — their nodes must exist before the current node can
        # hang off them.
        trailing_headings = []
        for frame in reversed(frames[:-1]):
            if isinstance(frame, LogHeading):
                trailing_headings.append(frame)
            else:
                break
        trailing_headings.reverse()

        with transaction.atomic():
            if audit_log_id:
                from lex.audit_logging.models.AuditLog import AuditLog

                AuditLog.objects.select_for_update().get(pk=audit_log_id)

            if context_info.parent_model and context_info.parent_content_type:
                parent_debug = {
                    "calculationId": context_info.calculation_id,
                    "audit_log": context_info.audit_log,
                    "content_type": context_info.parent_content_type,
                    "object_id": context_info.parent_model.pk,
                }
                parent_log, _ = cls._get_or_create_locked(
                    logger,
                    calculationId=context_info.calculation_id,
                    audit_log=context_info.audit_log,
                    content_type=context_info.parent_content_type,
                    object_id=context_info.parent_model.pk,
                )

            # Chain the heading nodes off the model parent (or off the root
            # when the headings have no model above them).
            for heading_frame in trailing_headings:
                parent_log = cls._ensure_heading_node(
                    heading_frame, parent_log, context_info, logger
                )

            current_frame = frames[-1] if frames else None
            if isinstance(current_frame, LogHeading):
                log_entry = cls._ensure_heading_node(
                    current_frame, parent_log, context_info, logger
                )
                log_debug = {
                    "calc_id": context_info.calculation_id,
                    "audit_log": context_info.audit_log,
                    "heading": current_frame.title,
                    "calclog": parent_log,
                }
            else:
                current_model_pk = (
                    context_info.current_model.pk if context_info.current_model else None
                )
                log_debug = {
                    "calc_id": context_info.calculation_id,
                    "audit_log": context_info.audit_log,
                    "content_type": context_info.content_type,
                    "object_id": current_model_pk,
                    "calclog": parent_log,
                }
                # heading=None keeps object-less model lookups (content_type
                # and object_id both NULL) from ever matching a heading row.
                log_entry, _ = cls._get_or_create_locked(
                    logger,
                    calculationId=context_info.calculation_id,
                    audit_log=context_info.audit_log,
                    content_type=context_info.content_type,
                    object_id=current_model_pk,
                    parent_log=parent_log,
                    heading=None,
                )

            logger.debug(f"Log: {log_debug}")
            logger.debug(f"Parent: {parent_debug}")
            log_entry.calculation_log = (log_entry.calculation_log or "") + f"\n{message}"
            log_entry.save(update_fields=["calculation_log"])

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

            from lex.audit_logging.utils.ContextResolver import ContextResolver
            context_info = ContextResolver.resolve()
            # logger.warning(f"Context: {context_info}")


            parent_debug = None
            log_debug = None



            # 2) Create parent log entry if needed
            db_alias = (
                getattr(getattr(context_info.audit_log, "_state", None), "db", None)
                or cls.objects.db
                or "default"
            )
            connection = transaction.get_connection(using=db_alias)

            if connection.in_atomic_block:
                def persist_after_commit():
                    try:
                        cls._persist_message(context_info, message, logger)
                    except Exception as persist_error:
                        logger.error("ERROR IN CALCULATION LOG")
                        logger.error(
                            "Unexpected deferred error in CalculationLog.log() for message: %s. Error: %s",
                            message,
                            persist_error,
                            exc_info=True,
                        )
                        logger.warning(f"Fallback logging: {message}")

                transaction.on_commit(persist_after_commit, using=db_alias)
            else:
                cls._persist_message(context_info, message, logger)


            root_record = context_info.root_record

            # logger.warning(f"The context_info from CalculationLog, context: {context_info}")
            # 6) Store in cache using CacheManager
            if context_info.current_record:
                cache_key = CacheManager.build_cache_key(
                    context_info.current_record,
                    context_info.calculation_id
                )
                CacheManager.store_message(cache_key, message)

            if context_info.root_record and context_info.root_record != context_info.current_record:
                cache_key = CacheManager.build_cache_key(
                    context_info.root_record,
                    context_info.calculation_id
                )
                CacheManager.store_message(cache_key, message)

            # 7) Log to standard logger (sends to WebSocket via WebSocketHandler)
            # Send to current record's WebSocket group
            logger.debug(
                message,
                extra={
                    "calculation_record": context_info.current_record,
                    "calculationId": context_info.calculation_id,
                },
            )

            # Also send to root record's WebSocket group so parent calculations
            # receive child logs in real-time (mirrors the cache store pattern above)
            if context_info.root_record and context_info.root_record != context_info.current_record:
                logger.debug(
                    message,
                    extra={
                        "calculation_record": context_info.root_record,
                        "calculationId": context_info.calculation_id,
                    },
                )
            
        except ContextResolutionError as e:
            # Context resolution failed (e.g. missing calculation_id in a Celery
            # worker where operation_context was not propagated).  We still want
            # real-time log streaming to work, so fall back to the model_context
            # stack for WebSocket routing — just skip DB persistence.
            logger.debug(
                f"Context resolution incomplete, using model-context fallback: {e}",
            )
            try:
                from lex.audit_logging.utils.ModelContext import _model_context as _mc
                model_ctx = _mc.get()['model_context']
                # Skip heading frames — WebSocket groups are keyed by real
                # model records, headings only shape the tree.
                current = model_ctx.get_current_model()
                root = model_ctx.get_root_model()
                current_record = f"{current._meta.model_name}_{current.pk}" if current else None
                root_record = f"{root._meta.model_name}_{root.pk}" if root else None

                # Send to WebSocket even without full context
                if current_record:
                    logger.debug(
                        message,
                        extra={
                            "calculation_record": current_record,
                            "calculationId": "",
                        },
                    )
                if root_record and root_record != current_record:
                    logger.debug(
                        message,
                        extra={
                            "calculation_record": root_record,
                            "calculationId": "",
                        },
                    )
            except Exception:
                logger.debug(f"Model-context fallback also failed for: {message}")
            
            
        except Exception as e:
            # Handle any other unexpected errors
            logger.error(f"ERROR IN CALCULATION LOG")
            logger.error(f"Log: {log_debug}")
            logger.error(f"Parent: {parent_debug}")
            logger.error(
                f"Unexpected error in CalculationLog.log() for message: {message}. Error: {str(e)}",
                exc_info=True
            )
            # Ensure the message is still logged somewhere
            logger.warning(f"Fallback logging: {message}")
