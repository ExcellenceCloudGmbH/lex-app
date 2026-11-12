"""``AuditLog`` entries for file export and download requests.

:class:`~lex.audit_logging.mixins.AuditLogMixin.AuditLogMixin` records the
create / update / delete surface by hooking DRF's ``perform_*`` methods.
Exports and downloads never pass through those hooks — they are read
operations that hand data out of the system — so they need their own opener.

They share the same contract, deliberately: one ``AuditLog`` row opened with
a ``pending`` :class:`~lex.audit_logging.models.AuditLogStatus.AuditLogStatus`
before the work starts, finalised to ``success`` or ``failure`` once the
outcome is known. A failed download is as interesting to a security audit as
a successful one, which is why the row is written up front rather than only
on the happy path.
"""

import logging

from django.db.models import Model
from django.utils import timezone

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.content_types import safe_get_content_type

logger = logging.getLogger(__name__)

#: Data left the system as a generated file built from model rows.
ACTION_EXPORT = "export"
#: Data left the system as a stored file, or as a link granting access to one.
ACTION_DOWNLOAD = "download"

#: Author recorded for a caller authenticating with an API key, matching
#: ``LexModel.API_KEY_AUDIT_ACTOR`` so both trails name the actor the same way.
API_KEY_AUTHOR = "Technical User"


def resolve_audit_author(request):
    """Name the actor behind ``request`` for :attr:`AuditLog.author`.

    An authenticated user is recorded by its string form, exactly as
    ``AuditLogMixin.log_change`` records it. An API-key caller authenticates
    as ``AnonymousUser``, which would name nobody, so it is recorded as
    ``"Technical User"`` instead.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user)

    try:
        from lex.api.utils.api_key_requests import get_api_key_request_identity

        if get_api_key_request_identity(request) is not None:
            return API_KEY_AUTHOR
    except Exception:
        logger.debug("API-key identity unavailable for audit author", exc_info=True)

    return str(user) if user is not None else None


class FileOperationAudit:
    """The open ``AuditLog`` row for one export or download request.

    The view enriches the payload while it works (:meth:`record`) and points
    the entry at the record it served (:meth:`attach`); the surrounding mixin
    calls :meth:`finish` once the response is known.
    """

    def __init__(self, audit_log, payload):
        self.audit_log = audit_log
        self.payload = payload
        self.finished = False

    def record(self, **details):
        """Merge facts discovered while serving the request into the payload."""
        self.payload.update(details)

    def attach(self, instance):
        """Point the entry's generic relation at the record that was served.

        This is what makes a download show up on that record's Audit Log tab.
        A model-wide export has no single record, so it keeps the content type
        it was opened with and leaves ``object_id`` unset.
        """
        content_type = safe_get_content_type(instance.__class__)
        self.audit_log.content_type = content_type
        self.audit_log.object_id = getattr(instance, "pk", None)

    def finish(self, *, success, error_traceback=None):
        """Persist the enriched payload and close the status row.

        Called at most once; later calls are ignored so a response that was
        produced by exception handling is not finalised twice.
        """
        if self.finished:
            return
        self.finished = True

        self.audit_log.payload = self.payload
        self.audit_log.save()
        AuditLogStatus.objects.filter(audit_log=self.audit_log).update(
            status="success" if success else "failure",
            error_traceback=error_traceback,
            updated_at=timezone.now(),
        )


def open_file_operation_audit(*, request, action, resource, target=None, payload=None):
    """Open a ``pending`` audit entry for an export or download request.

    ``target`` is the model class (a model-wide export) or the instance (a
    single record's file) the operation reads; it fixes the entry's content
    type so the operation is attributable to the data it touched.

    Errors are not swallowed: an export we cannot account for does not run.
    """
    audit_log = AuditLog.objects.create(
        author=resolve_audit_author(request),
        resource=resource,
        action=action,
        payload=payload or {},
        calculation_id=None,
    )
    AuditLogStatus.objects.create(audit_log=audit_log, status="pending")

    file_audit = FileOperationAudit(audit_log, dict(payload or {}))
    if target is not None:
        audit_log.content_type = safe_get_content_type(target)
        # ``Model.pk`` on the class is a descriptor, not a key — only an
        # instance identifies a single record.
        if isinstance(target, Model):
            audit_log.object_id = target.pk
    return file_audit
