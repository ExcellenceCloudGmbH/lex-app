"""DRF view mixin that audits file export and download requests."""

import logging
import traceback

from lex.audit_logging.utils.file_operation_audit import open_file_operation_audit

logger = logging.getLogger(__name__)


def _as_int(value):
    """Header values are strings; the audit payload keeps sizes numeric."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FileOperationAuditMixin:
    """Record one ``AuditLog`` row per export / download request.

    The entry is opened once authentication and permissions have passed —
    so ``author`` names the real caller — and finalised from the response:
    ``success`` below HTTP 400, ``failure`` at or above it. A request the
    view rejects (an unreadable field, an empty export) is therefore audited
    as a failed attempt rather than not at all.

    A view supplies the request-side facts through :meth:`audit_model` and
    :meth:`audit_request_payload`, and adds what it learns while serving
    through :meth:`record_audit` and :meth:`attach_audit`.
    """

    #: ``ACTION_EXPORT`` or ``ACTION_DOWNLOAD``.
    audit_action = None

    #: Set per request by :meth:`initial`; ``None`` until then so a request
    #: rejected before the entry is opened finalises nothing.
    file_audit = None

    # -- what the concrete view describes --------------------------------

    def audit_model(self, request, *args, **kwargs):
        """The model whose data this request reads."""
        return getattr(kwargs.get("model_container"), "model_class", None)

    def audit_resource(self, request, *args, **kwargs):
        """The ``AuditLog.resource`` name — the model name, lowercased."""
        model = self.audit_model(request, *args, **kwargs)
        return model.__name__.lower() if model is not None else "unknown"

    def audit_request_payload(self, request, *args, **kwargs):
        """What the caller asked for, known before the work starts."""
        return {}

    # -- what the handler adds while serving ------------------------------

    def record_audit(self, **details):
        """Merge facts discovered while serving into the audit payload."""
        if self.file_audit is not None:
            self.file_audit.record(**details)

    def attach_audit(self, instance):
        """Point the audit entry at the record that was served."""
        if self.file_audit is not None:
            self.file_audit.attach(instance)

    # -- DRF hooks ---------------------------------------------------------

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.file_audit = open_file_operation_audit(
            request=request,
            action=self.audit_action,
            resource=self.audit_resource(request, *args, **kwargs),
            target=self.audit_model(request, *args, **kwargs),
            payload=self.audit_request_payload(request, *args, **kwargs),
        )

    def handle_exception(self, exc):
        error_traceback = traceback.format_exc()
        try:
            response = super().handle_exception(exc)
        except Exception:
            # DRF re-raises anything it has no response for, and that bypasses
            # ``finalize_response`` — the only point where such a failure can
            # still be recorded is here.
            self._finish_audit(success=False, error_traceback=error_traceback)
            raise

        # DRF turned the exception into a response, so let
        # ``finalize_response`` close the entry with that response's status.
        self._audit_error_traceback = error_traceback
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        success = response.status_code < 400
        self._finish_audit(
            success=success,
            error_traceback=None if success else getattr(self, "_audit_error_traceback", None),
            response=response,
        )
        return super().finalize_response(request, response, *args, **kwargs)

    def _finish_audit(self, *, success, error_traceback=None, response=None):
        if self.file_audit is None or self.file_audit.finished:
            return
        try:
            if response is not None:
                self.file_audit.record(
                    status_code=response.status_code,
                    content_type=response.headers.get("Content-Type"),
                    content_length=_as_int(response.headers.get("Content-Length")),
                )
            self.file_audit.finish(success=success, error_traceback=error_traceback)
        except Exception:
            # The data has already been served; losing the response is a
            # worse outcome than losing its audit row, which is logged here.
            logger.exception(
                "Failed to finalise the audit entry for a %s request",
                self.audit_action,
            )
