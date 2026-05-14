"""
Sub-cluster 6k — Audit-log model declarative contracts.

Three files from the test-writing-plan PR-7 audit batch (Models & enums)
that no other 6-cluster test pins at the *declarative* level:

* ``lex/audit_logging/models/AuditLog.py`` — choices, frontend
  ``to_dict()`` shape, ``__str__`` format, and the deliberate override
  of ``LexModel``'s inherited timestamp fields. The frontend
  Audit-Log Tab parses the literal date format string and the four
  documented ``to_dict()`` keys; the log scraper greps the
  ``__str__`` shape; the customer-deploy migration history depends on
  ``created_at``/``edited_at``/``created_by``/``edited_by`` being
  ``None`` so Django does NOT add the default LexModel columns to
  ``audit_logging_auditlog``. A regression in any of these is silent
  at import time and surfaces only when a customer tab/log-pipeline
  breaks in production.

* ``lex/audit_logging/models/AuditLogStatus.py`` — default
  ``status='pending'`` (an op writes the audit row BEFORE the work
  starts; the worker / committer flips it later — losing this default
  would mark every op as silently complete on creation), the
  ``duration`` property's success/failure-only contract (operator
  dashboards plot this, ``None`` is rendered as "no duration yet"),
  and ``__str__`` (audit log forensics).

* ``lex/audit_logging/models/CalculationLog.py`` — the severity +
  message-type constants the audit-message parser splits on (the
  source comment is explicit: "Severity: Message — The colon and the
  whitespace after are required for the code to work correctly"), and
  the ``modification_restriction`` instance + GFK wiring.

All scenarios are pure declarative checks — no DB, no Keycloak, no
Celery. ``SimpleTestCase`` only. The richer end-to-end paths
(``log()`` deferral, ``_get_or_create_locked`` dedup, payload
content) are covered by 6a / 6b / 6d / 6h / 6i which already drive
real DB rows.

Scenario IDs 6.96 – 6.108. Range deliberately leaves 6.74 – 6.95
free for prior 6f / 6g / 6j extensions.

Run with::

    lex test lex.test_project.tests.audit_logging.test_6k_model_contracts \\
        --verbosity=2 --noinput --keepdb
"""

from __future__ import annotations

import datetime as _dt

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models as dj_models
from django.test import SimpleTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.mixins.ModelModificationRestriction import (
    AdminReportsModificationRestriction,
)


# =====================================================================
# AuditLog — declarative contracts
# =====================================================================


class TestCluster06k_AuditLogModel(SimpleTestCase):
    """``AuditLog`` field shape, choices, ``__str__`` and ``to_dict()``.

    These are the contracts the frontend Audit-Log Tab + log-scraper
    pipelines parse; silent drift here breaks downstream consumers
    without breaking any framework test.
    """

    # -- 6.96 ----------------------------------------------------------
    def test_6_96_action_choices_are_exactly_create_update_delete(self) -> None:
        """
        Scenario 6.96: ``AuditLog.ACTION_CHOICES`` must enumerate
        exactly the three documented operations (``create`` / ``update``
        / ``delete``). Adding or removing one is a contract change for
        every consumer that branches on action — the frontend's
        per-action icon/colour, the audit-log filter dropdown, and the
        compliance-team's CSV export.

        A regression that silently introduced (e.g.) ``"bulk_delete"``
        as a 4th choice would let unknown actions pass the model-level
        ``CharField(choices=...)`` validation but break every
        downstream switch statement.
        """
        choices = AuditLog.ACTION_CHOICES
        self.assertEqual(
            choices,
            (
                ("create", "Create"),
                ("update", "Update"),
                ("delete", "Delete"),
            ),
            "ACTION_CHOICES drift — frontend / scraper consumers branch "
            "on this exact tuple shape. Got: %r" % (choices,),
        )

    # -- 6.97 ----------------------------------------------------------
    def test_6_97_str_format_is_action_on_resource_by_author(self) -> None:
        """
        Scenario 6.97: ``str(audit_log)`` returns
        ``"{action} on {resource} by {author}"``. Log-scraping
        pipelines (greylog, splunk) grep this exact shape; a
        rearrangement (``"{author} did {action}..."``) would silently
        drop every alert that fires on the ``" on "`` infix.
        """
        row = AuditLog(
            action="update",
            resource="Invoice",
            author="alice@example.com",
        )
        self.assertEqual(
            str(row),
            "update on Invoice by alice@example.com",
            "AuditLog.__str__ shape changed — log scrapers / alerting "
            "rules grep this format verbatim. Got: %r" % (str(row),),
        )

    # -- 6.98 ----------------------------------------------------------
    def test_6_98_to_dict_keys_and_date_format_match_frontend_contract(self) -> None:
        """
        Scenario 6.98: ``AuditLog.to_dict()`` returns exactly the five
        keys the frontend Audit-Log Tab renders (``date``, ``author``,
        ``resource``, ``action``, ``payload``), with ``date`` formatted
        as ``"YYYY-MM-DD HH:MM:SS"`` (the format string the React
        component splits on) and ``payload`` defaulting to ``{}`` when
        the underlying column is NULL (the frontend assumes a dict —
        ``None`` would crash the rendering of "no changes recorded"
        rows).

        A regression that returned ISO-8601 (``"2026-05-12T15:30:45"``)
        would silently break the relative-time chip the UI renders;
        a missing ``payload: {}`` default would crash the tab on every
        legacy row written before payload was made nullable.
        """
        row = AuditLog(
            action="delete",
            resource="Counterparty",
            author="system",
            payload=None,
        )
        # Inject a deterministic timestamp — auto_now_add only fires on save().
        row.date = _dt.datetime(2026, 5, 12, 15, 30, 45)

        result = row.to_dict()

        self.assertEqual(
            set(result.keys()),
            {"date", "author", "resource", "action", "payload"},
            "to_dict() must expose exactly the 5 keys the Audit-Log Tab "
            "consumes. Got keys: %r" % (sorted(result.keys()),),
        )
        self.assertEqual(
            result["date"], "2026-05-12 15:30:45",
            "to_dict() date format drifted from %%Y-%%m-%%d %%H:%%M:%%S "
            "— React relative-time chip splits on this exact shape. "
            "Got: %r" % (result["date"],),
        )
        self.assertEqual(
            result["payload"], {},
            "to_dict() must coerce NULL payload to {} so the frontend's "
            "dict iteration cannot crash on legacy rows. Got: %r"
            % (result["payload"],),
        )

    # -- 6.99 ----------------------------------------------------------
    def test_6_99_inherited_lexmodel_timestamp_fields_are_disabled(self) -> None:
        """
        Scenario 6.99: ``AuditLog`` overrides the four inherited
        ``LexModel`` audit fields — ``created_at`` / ``edited_at`` /
        ``created_by`` / ``edited_by`` — to ``None`` because it has its
        own ``date`` + ``author`` columns serving the same purpose. If
        any of these were re-enabled, Django would add four extra
        columns to ``audit_logging_auditlog``, every existing customer
        deploy would need a destructive migration, AND the audit-write
        pipeline would silently start populating duplicate
        actor/timestamp pairs — the very confusion the override
        deliberately prevents.

        Asserted via ``_meta.get_field`` raising ``FieldDoesNotExist``
        rather than reading the class attr (which is ``None`` on the
        class but the model meta is the source of truth Django acts
        on).
        """
        from django.core.exceptions import FieldDoesNotExist

        for fname in ("created_at", "edited_at", "created_by", "edited_by"):
            with self.subTest(field=fname):
                with self.assertRaises(
                    FieldDoesNotExist,
                    msg=(
                        "Inherited LexModel field %r leaked back onto "
                        "AuditLog — destructive customer-side migration "
                        "+ duplicate actor/timestamp data would result."
                        % fname
                    ),
                ):
                    AuditLog._meta.get_field(fname)

    # -- 6.100 ---------------------------------------------------------
    def test_6_100_modification_restriction_is_admin_reports_instance(self) -> None:
        """
        Scenario 6.100: ``AuditLog.modification_restriction`` is an
        instance of ``AdminReportsModificationRestriction`` (the
        read-only profile shipped with HTMLReport — every write
        denied). Pinning the *type* rather than re-asserting every
        deny-method here means a regression that swapped the profile
        for a permissive one (or for a stub) is caught at the source
        without duplicating the per-method coverage that 7k already
        landed.
        """
        self.assertIsInstance(
            AuditLog.modification_restriction,
            AdminReportsModificationRestriction,
            "AuditLog.modification_restriction must remain the read-only "
            "AdminReportsModificationRestriction profile — swapping it "
            "silently re-enables write paths for compliance-critical "
            "rows. Got: %r" % (AuditLog.modification_restriction,),
        )

    # -- 6.101 ---------------------------------------------------------
    def test_6_101_generic_foreign_key_fields_are_wired(self) -> None:
        """
        Scenario 6.101: ``AuditLog`` exposes a complete
        GenericForeignKey wiring — ``content_type`` (FK to
        ``ContentType``), ``object_id`` (PositiveIntegerField), and
        ``calculatable_object`` (the GFK descriptor). Without all
        three, the per-record Audit-Log Tab cannot resolve audit rows
        back to their source instance (``content_type`` + ``object_id``
        is the lookup key the tab issues). Cluster 6d already verifies
        the *population* of these fields end-to-end; this scenario
        pins their *declaration* so a regression that dropped one is
        caught at import time, not at the next CRUD test run.
        """
        # content_type — FK to ContentType, nullable so legacy /
        # detached audit rows don't crash on backfill migrations.
        ct_field = AuditLog._meta.get_field("content_type")
        self.assertIsInstance(ct_field, dj_models.ForeignKey)
        self.assertIs(ct_field.related_model, ContentType)
        self.assertTrue(
            ct_field.null,
            "content_type must be nullable — detached audit rows "
            "(e.g. delete-then-purge) need a NULL anchor.",
        )

        # object_id — PositiveIntegerField, nullable.
        obj_field = AuditLog._meta.get_field("object_id")
        self.assertIsInstance(obj_field, dj_models.PositiveIntegerField)
        self.assertTrue(obj_field.null, "object_id must be nullable")

        # calculatable_object — GenericForeignKey descriptor (lives in
        # _meta.private_fields, not _meta.fields).
        gfk_fields = [
            f for f in AuditLog._meta.private_fields
            if isinstance(f, GenericForeignKey)
        ]
        self.assertEqual(
            len(gfk_fields), 1,
            "Exactly one GenericForeignKey must remain on AuditLog — "
            "the per-record Audit-Log Tab depends on it.",
        )
        gfk = gfk_fields[0]
        self.assertEqual(gfk.name, "calculatable_object")
        self.assertEqual(gfk.ct_field, "content_type")
        self.assertEqual(gfk.fk_field, "object_id")


# =====================================================================
# AuditLogStatus — defaults, duration property, __str__
# =====================================================================


class TestCluster06k_AuditLogStatusModel(SimpleTestCase):
    """``AuditLogStatus`` defaults and the ``duration`` property.

    ``AuditLogStatus`` walks ``pending → success | failure``. The
    *order* of those transitions is covered by 6a/6f; this batch pins
    the *static contract* the transition logic depends on — default,
    duration semantics, ``__str__`` shape.
    """

    # -- 6.102 ---------------------------------------------------------
    def test_6_102_status_field_defaults_to_pending(self) -> None:
        """
        Scenario 6.102: ``AuditLogStatus.status`` defaults to
        ``"pending"``. The audit-write pipeline creates the status row
        BEFORE the operation runs, so the row sits at ``pending`` until
        the worker / committer flips it. Losing the default would
        silently mark every op as complete on creation — failures would
        no longer be observable.
        """
        field = AuditLogStatus._meta.get_field("status")
        self.assertEqual(
            field.default, "pending",
            "AuditLogStatus.status default drifted from 'pending' — "
            "audit rows would be born already-complete and failures "
            "would be invisible. Got: %r" % (field.default,),
        )

    # -- 6.103 ---------------------------------------------------------
    def test_6_103_duration_returns_seconds_for_terminal_statuses(self) -> None:
        """
        Scenario 6.103: ``duration`` returns
        ``(updated_at - created_at).total_seconds()`` for the two
        terminal statuses ``success`` and ``failure``. Operator
        dashboards plot this as p95/p99 latency per resource — a
        regression returning ``None`` for terminal rows would silently
        empty every "audit-write latency" panel.
        """
        for status in ("success", "failure"):
            with self.subTest(status=status):
                row = AuditLogStatus(status=status)
                row.created_at = _dt.datetime(2026, 5, 12, 12, 0, 0)
                row.updated_at = _dt.datetime(2026, 5, 12, 12, 0, 1, 250000)
                self.assertAlmostEqual(
                    row.duration, 1.25, places=3,
                    msg=(
                        "duration for status=%r must return seconds "
                        "between created_at and updated_at — operator "
                        "latency dashboards depend on this." % status
                    ),
                )

    # -- 6.104 ---------------------------------------------------------
    def test_6_104_duration_is_none_for_pending_status(self) -> None:
        """
        Scenario 6.104: ``duration`` returns ``None`` for ``pending``
        even when ``created_at`` and ``updated_at`` are both populated
        (Django's ``auto_now_add`` + ``auto_now`` will set both at the
        moment of the initial save). The frontend renders ``None`` as
        "in flight" — a regression returning ``0.0`` here would render
        every pending op as "completed in 0 ms".
        """
        row = AuditLogStatus(status="pending")
        row.created_at = _dt.datetime(2026, 5, 12, 12, 0, 0)
        row.updated_at = _dt.datetime(2026, 5, 12, 12, 0, 0)
        self.assertIsNone(
            row.duration,
            "duration for status='pending' must be None (rendered as "
            "'in flight'); a 0.0 return would mis-label every pending "
            "row as instantly-complete.",
        )

    # -- 6.105 ---------------------------------------------------------
    def test_6_105_str_format_includes_audit_log_id_and_status(self) -> None:
        """
        Scenario 6.105: ``str(audit_log_status)`` returns
        ``"AuditLogStatus({audit_log.id}): {status}"``. Audit-log
        forensics (operators tracing a failed op back to its parent
        ``AuditLog`` row) splits on the parens + colon. A rearrange
        would silently break the documented forensic procedure.
        """
        # Unsaved AuditLog instance with an explicit id is enough —
        # the FK descriptor type-checks but does not require the row
        # to exist on disk; ``__str__`` only reads ``.audit_log.id``.
        row = AuditLogStatus(status="failure")
        row.audit_log = AuditLog(id=42)
        self.assertEqual(
            str(row),
            "AuditLogStatus(42): failure",
            "AuditLogStatus.__str__ shape drifted — operator forensics "
            "depend on the 'AuditLogStatus(<id>): <status>' format. "
            "Got: %r" % (str(row),),
        )


# =====================================================================
# CalculationLog — severity / message-type constants + class wiring
# =====================================================================


class TestCluster06k_CalculationLogConstants(SimpleTestCase):
    """``CalculationLog`` severity + message-type string constants.

    The source file carries an explicit comment: "Messages shall be
    delivered in the following format: 'Severity: Message' — The colon
    and the whitespace after are required for the code to work
    correctly". The audit-message parser splits on that exact shape;
    drift in any constant breaks every downstream consumer that greps
    for severity prefixes.
    """

    # -- 6.106 ---------------------------------------------------------
    def test_6_106_severity_constants_carry_colon_space_suffix(self) -> None:
        """
        Scenario 6.106: every severity constant on ``CalculationLog``
        ends in the literal ``": "`` (colon + space). The source file's
        own comment makes the format mandatory — the audit-message
        parser uses ``message.split(": ", 1)`` to peel the severity off.
        A regression dropping the trailing space (``"Error:"``) would
        silently route every log line into the "no severity" bucket.
        """
        expected = {
            "SUCCESS": "Success: ",
            "WARNING": "Warning: ",
            "ERROR": "Error: ",
            "START": "Start: ",
            "FINISH": "Finish: ",
        }
        for name, value in expected.items():
            with self.subTest(constant=name):
                actual = getattr(CalculationLog, name)
                self.assertEqual(
                    actual, value,
                    "CalculationLog.%s drifted from %r — the audit-"
                    "message parser splits on this exact shape "
                    "(colon-space suffix is mandatory). Got: %r"
                    % (name, value, actual),
                )

    # -- 6.107 ---------------------------------------------------------
    def test_6_107_message_type_constants_pinned(self) -> None:
        """
        Scenario 6.107: the three message-type constants (``PROGRESS``,
        ``INPUT``, ``OUTPUT``) carry their documented values. The
        validation pipeline tags pre/post hook results with these
        labels so the Calculation-Log Tab can group "Input Validation"
        vs "Output Validation" sections; drift would silently collapse
        the two groups into one undifferentiated stream.
        """
        self.assertEqual(CalculationLog.PROGRESS, "Progress")
        self.assertEqual(CalculationLog.INPUT, "Input Validation")
        self.assertEqual(CalculationLog.OUTPUT, "Output Validation")

    # -- 6.108 ---------------------------------------------------------
    def test_6_108_modification_restriction_and_gfk_wired(self) -> None:
        """
        Scenario 6.108: ``CalculationLog`` carries (a) the
        ``AdminReportsModificationRestriction`` read-only profile
        (calculation logs are write-once forensic records — re-
        permitting writes would let a misbehaving handler corrupt the
        log retroactively), and (b) the same GFK trio
        (``content_type`` + ``object_id`` + ``calculatable_object``)
        as ``AuditLog``, because the per-record Calculation-Log Tab
        also resolves rows back to their source instance.
        """
        # (a) modification restriction
        self.assertIsInstance(
            CalculationLog.modification_restriction,
            AdminReportsModificationRestriction,
            "CalculationLog.modification_restriction must remain the "
            "read-only AdminReports profile — drift would let in-flight "
            "handlers retroactively rewrite forensic logs.",
        )

        # (b) GFK trio
        ct_field = CalculationLog._meta.get_field("content_type")
        self.assertIsInstance(ct_field, dj_models.ForeignKey)
        self.assertIs(ct_field.related_model, ContentType)

        obj_field = CalculationLog._meta.get_field("object_id")
        self.assertIsInstance(obj_field, dj_models.PositiveIntegerField)

        gfk_fields = [
            f for f in CalculationLog._meta.private_fields
            if isinstance(f, GenericForeignKey)
        ]
        self.assertEqual(
            len(gfk_fields), 1,
            "Exactly one GenericForeignKey must remain on CalculationLog "
            "— per-record Calculation-Log Tab depends on it.",
        )
        gfk = gfk_fields[0]
        self.assertEqual(gfk.name, "calculatable_object")
        self.assertEqual(gfk.ct_field, "content_type")
        self.assertEqual(gfk.fk_field, "object_id")


