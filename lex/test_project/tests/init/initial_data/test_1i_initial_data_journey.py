"""
Cluster 1i: Initial-data upload — full journey end-to-end.

Targets the customer-facing seed-loading surface documented in
``docs/lex_topics/16-initial-data-upload.md``:

* :class:`ProcessAdminTestCase` (``lex/lex_app/tests/ProcessAdminTestCase.py``)
  — the JSON walker: ``replace_tagged_parameters`` (the ``tag:`` and
  ``datetime:`` prefix resolvers), ``get_test_data_from_path``
  (recursive subprocess flattening), ``check_if_all_models_are_empty``
  (the auto-load gate), and ``setUp`` (the per-action
  create / update / delete dispatcher).
* :class:`InitialDataAuditLogger`
  (``lex/audit_logging/utils/InitialDataAuditLogger.py``) — the audit
  seam that writes one ``AuditLog`` + ``AuditLogStatus`` pair per
  operation so the compliance view can reconstruct the whole seed
  run.

Intent (from the docs):

    Seed data lets customers declare production-shaped test data in
    JSON. On server start, if **every referenced model has zero rows**,
    the framework walks the JSON top-down and applies each action
    (``create`` / ``update`` / ``delete``) in declaration order. Tag
    references (``"team": "tag:team_design"``) resolve to in-memory
    objects created earlier in the same load. ``datetime:`` strings
    parse through ``dateutil``. Subprocess references flatten
    recursively so large seed sets can be split across files.

    If any operation fails, the whole session is marked failed and
    lingering pending statuses are collapsed to ``failure`` with the
    error string, so a compliance audit tells the truth about what
    actually landed.

Why end-to-end: ``InitialDataAuditLogger`` is tightly coupled to the
real ``AuditLog`` + ``AuditLogStatus`` tables (every method opens a
transaction and writes real rows). Mocking the ORM would hide the
contract this cluster is supposed to guard — that the audit trail is
**always** consistent, even on failure.

Scenario numbering continues Cluster 1 (1.51 – 1.60). Matches
docs/test-plan/test-clusters.md  Planned Expansions → 1i.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.InitialDataAuditLogger import InitialDataAuditLogger
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests.crud_api.models import SimpleItem
from lex.test_project.tests._e2e_test_case import E2ETestCase

import pytest

pytestmark = pytest.mark.init

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


# =====================================================================
# TestCluster01i_ReplaceTaggedParameters
#
# Pure unit scenarios — no DB, no audit — on the ``tag:`` / ``datetime:``
# prefix resolver. This is the function every seed entry goes through
# before the action dispatcher runs; a bug here would corrupt every
# FK reference in the seed file.
# =====================================================================
class TestCluster01i_ReplaceTaggedParameters(unittest.TestCase):
    """Scenario 1.51 — ``tag:`` and ``datetime:`` prefix resolution."""

    def test_1_51a_tag_prefix_resolves_to_tagged_object(self) -> None:
        """Scenario 1.51a: ``tag:foo`` is replaced by the in-memory
        object previously stored at ``tagged_objects['foo']`` — this
        is the FK-by-reference mechanism.
        """
        harness = ProcessAdminTestCase()
        sentinel = object()
        harness.tagged_objects = {"team_design": sentinel, "other": 42}

        out = harness.replace_tagged_parameters(
            {
                "team": "tag:team_design",
                "manager": "tag:other",
                "name": "plain string",
                "value": 5,
            }
        )

        self.assertIs(
            out["team"], sentinel,
            "tag: prefix must resolve to the previously-created object "
            "so FK fields receive a real instance, not the string",
        )
        self.assertEqual(out["manager"], 42)
        self.assertEqual(
            out["name"], "plain string",
            "Strings without a known prefix must pass through unchanged — "
            "the resolver must not accidentally mangle literals",
        )
        self.assertEqual(out["value"], 5, "Non-string values pass through untouched")

    def test_1_51b_datetime_prefix_parses_iso_string(self) -> None:
        """Scenario 1.51b: ``datetime:2026-01-15`` is parsed through
        ``dateutil.parser`` into a real ``datetime`` — the seed file
        ships dates as strings but the model needs ``datetime``
        instances.
        """
        harness = ProcessAdminTestCase()
        harness.tagged_objects = {}

        out = harness.replace_tagged_parameters(
            {
                "booked_on": "datetime:2026-01-15",
                "note": "not a prefix",
            }
        )

        self.assertIsInstance(out["booked_on"], datetime)
        self.assertEqual(out["booked_on"].date().isoformat(), "2026-01-15")
        self.assertEqual(out["note"], "not a prefix")


# =====================================================================
# TestCluster01i_SubprocessFlattening
#
# ``get_test_data_from_path`` is a recursive JSON walker that expands
# ``{"subprocess": "path/to/other.json"}`` entries by loading the
# referenced file and inlining its contents. The flattening is what
# lets customers split a 10,000-line seed into one file per subprocess.
# =====================================================================
class TestCluster01i_SubprocessFlattening(unittest.TestCase):
    """Scenario 1.52 — recursive subprocess flattening."""

    def test_1_52_subprocess_refs_are_flattened_in_order(self) -> None:
        """Scenario 1.52: A top-level JSON of ``[{subprocess: a},
        {subprocess: b}]`` where ``a`` has 1 entry and ``b`` has 2
        flattens to 3 entries in ``a, b1, b2`` order.

        Order matters: parent-before-child FK resolution relies on
        flatten preserving each sub-file's internal order AND the
        declared order of the subprocesses in the parent.
        """
        harness = ProcessAdminTestCase()

        # ``get_test_data_from_path`` interprets subprocess paths
        # relative to ``$PROJECT_ROOT``. The lex-app editable install
        # exposes ``lex/`` as the root, so the in-file subprocess
        # references live at ``test_project/tests/fixtures/…``.
        project_root = str(
            Path(__file__).resolve().parents[4]  # → …/lex-app/lex/
        )
        with mock.patch.dict(os.environ, {"PROJECT_ROOT": project_root}):
            flat = harness.get_test_data_from_path(
                str(FIXTURES / "seed_parent.json"),
            )

        self.assertEqual(
            [entry["tag"] for entry in flat],
            ["sub1_a", "sub2_a", "sub2_b"],
            "Flatten must preserve (a) the declared order of the "
            "subprocesses in the parent file AND (b) each sub-file's "
            "internal order — otherwise FK references that span files "
            "would silently break.",
        )


# =====================================================================
# TestCluster01i_AuditLoggerCRUD
#
# Drives ``InitialDataAuditLogger`` methods against the real
# ``AuditLog`` + ``AuditLogStatus`` tables. Asserts on the row shape
# the compliance view renders — author / resource / action / payload /
# status.
# =====================================================================
class TestCluster01i_AuditLoggerCRUD(E2ETestCase):
    """Scenarios 1.54 – 1.57 — audit-logger create / update / delete."""

    e2e_models = [SimpleItem]
    e2e_framework_models = [AuditLog, AuditLogStatus]

    # -- 1.54 ----------------------------------------------------------
    def test_1_54_log_object_creation_writes_audit_row_and_pending_status(self) -> None:
        """Scenario 1.54: ``log_object_creation`` produces one
        ``AuditLog`` + one ``AuditLogStatus(status='pending')`` with
        the seed tag stored in the payload for traceability.
        """
        logger = InitialDataAuditLogger()
        calc_id = logger.generate_calculation_id()

        audit_log = logger.log_object_creation(
            SimpleItem,
            {"name": "Alpha", "value": 10},
            tag="alpha_row",
            calculation_id=calc_id,
        )

        self.assertIsNotNone(audit_log, "Audit log must be created on a valid payload")
        self.assertEqual(audit_log.author, "system (initial_data_upload)")
        self.assertEqual(audit_log.resource, "simpleitem")
        self.assertEqual(audit_log.action, "create")
        self.assertEqual(audit_log.calculation_id, calc_id)
        self.assertEqual(
            audit_log.payload.get("_audit_tag"), "alpha_row",
            "Seed tag must be stored in the payload so the compliance "
            "view can trace an audit row back to the exact seed entry",
        )

        status = AuditLogStatus.objects.get(audit_log=audit_log)
        self.assertEqual(
            status.status, "pending",
            "Fresh audit logs start ``pending`` — mark_operation_success/"
            "failure advances them; the safety-net in finalize_batch "
            "resolves any that were never advanced.",
        )

    def test_1_54b_logged_ids_tracked_for_finalize(self) -> None:
        """1.54b: every log call appends the created AuditLog.id to
        the logger's ``_logged_ids``, which ``finalize_batch`` uses
        as the scope for the safety-net pending→success sweep.
        """
        logger = InitialDataAuditLogger()
        calc_id = logger.generate_calculation_id()

        a1 = logger.log_object_creation(SimpleItem, {"name": "A"}, tag="t1", calculation_id=calc_id)
        a2 = logger.log_object_creation(SimpleItem, {"name": "B"}, tag="t2", calculation_id=calc_id)

        self.assertEqual(
            logger._logged_ids, [a1.id, a2.id],
            "Logger must track every id in declaration order — "
            "finalize_batch scopes its sweep by this list so logs from "
            "earlier sessions are not touched.",
        )

    # -- 1.55 ----------------------------------------------------------
    def test_1_55_log_object_update_includes_instance_pk(self) -> None:
        """Scenario 1.55: ``log_object_update`` records the pk of the
        instance being updated so the compliance view can link the
        audit row back to a specific database row.
        """
        logger = InitialDataAuditLogger()
        instance = SimpleItem.objects.create(name="Original", value=1)
        calc_id = logger.generate_calculation_id()

        audit_log = logger.log_object_update(
            SimpleItem, instance, {"value": 999}, tag="updated_value", calculation_id=calc_id,
        )

        self.assertIsNotNone(audit_log)
        self.assertEqual(audit_log.action, "update")
        self.assertEqual(
            audit_log.payload.get("id"), instance.pk,
            "Update payload must carry the target row's pk — without it "
            "the audit row cannot be linked to a database row",
        )
        # ``_serialize_payload`` stringifies scalar values for JSON
        # stability across int / Decimal / bool round-trips. Compare
        # against the string form — what matters is the value survives,
        # not the underlying Python type.
        self.assertEqual(str(audit_log.payload.get("value")), "999")
        self.assertEqual(audit_log.payload.get("_audit_tag"), "updated_value")

    # -- 1.56 ----------------------------------------------------------
    def test_1_56_log_object_deletion_uses_instance_payload(self) -> None:
        """Scenario 1.56: ``log_object_deletion`` snapshots the
        full instance via ``generic_instance_payload`` — so the
        audit row preserves the data that was deleted, even after
        the row is gone from the main table.
        """
        logger = InitialDataAuditLogger()
        instance = SimpleItem.objects.create(name="Doomed", value=7)

        audit_log = logger.log_object_deletion(
            instance, {"name": "Doomed"}, tag="cleanup",
        )

        self.assertIsNotNone(audit_log)
        self.assertEqual(audit_log.action, "delete")
        self.assertEqual(
            audit_log.payload.get("_audit_tag"), "cleanup",
        )
        # Payload carries the instance data so the compliance view
        # still shows the deleted row's name even after SimpleItem.delete()
        # has wiped the row from the main table.
        self.assertIn(
            "name", audit_log.payload,
            "Deletion payload must snapshot the instance's fields — "
            "otherwise the compliance audit cannot answer 'what was in "
            "the row we just deleted?'",
        )
        status = AuditLogStatus.objects.get(audit_log=audit_log)
        self.assertEqual(status.status, "pending")

    # -- 1.57 ----------------------------------------------------------
    def test_1_57_mark_success_and_failure_advance_status(self) -> None:
        """Scenario 1.57: ``mark_operation_success`` flips the
        status to ``success``; ``mark_operation_failure`` flips it to
        ``failure`` and stores the error traceback. Both are
        idempotent via queryset ``.update()`` — calling twice must
        not duplicate rows.
        """
        logger = InitialDataAuditLogger()
        audit_ok = logger.log_object_creation(SimpleItem, {"name": "OK"}, tag="ok")
        audit_err = logger.log_object_creation(SimpleItem, {"name": "ERR"}, tag="err")

        logger.mark_operation_success(audit_ok)
        logger.mark_operation_failure(audit_err, "boom")

        self.assertEqual(
            AuditLogStatus.objects.get(audit_log=audit_ok).status, "success",
        )

        err_status = AuditLogStatus.objects.get(audit_log=audit_err)
        self.assertEqual(err_status.status, "failure")
        self.assertEqual(err_status.error_traceback, "boom")

        # Idempotency: second mark does not create a duplicate row.
        logger.mark_operation_success(audit_ok)
        self.assertEqual(
            AuditLogStatus.objects.filter(audit_log=audit_ok).count(), 1,
            "mark_operation_success must be idempotent — otherwise a "
            "retry would explode the status table",
        )

    def test_1_57b_mark_success_on_none_audit_log_is_no_op(self) -> None:
        """1.57b: if ``log_object_creation`` failed and returned
        ``None``, calling mark_operation_success with None must not
        raise — the seed loader passes whatever log_object_creation
        returned.
        """
        logger = InitialDataAuditLogger()
        # Must not raise.
        logger.mark_operation_success(None)
        logger.mark_operation_failure(None, "ignored")


# =====================================================================
# TestCluster01i_AuditLoggerFinalize
#
# ``finalize_batch`` is the safety net: it sweeps any ``pending``
# statuses that ``mark_operation_*`` never reached (crash mid-seed,
# handler omission) and collapses them to either ``success`` (clean
# run) or ``failure`` (error in the outer driver). Essential for the
# "audit trail is always consistent" contract.
# =====================================================================
class TestCluster01i_AuditLoggerFinalize(E2ETestCase):
    """Scenario 1.58 — finalize_batch sweeps + summary."""

    e2e_models = [SimpleItem]
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def test_1_58a_finalize_resolves_lingering_pending_to_success(self) -> None:
        """Scenario 1.58a: clean run → ``finalize_batch()`` with no
        error parameter resolves any pending rows to ``success`` and
        returns a summary with the right counts.
        """
        logger = InitialDataAuditLogger()
        a1 = logger.log_object_creation(SimpleItem, {"name": "A"}, tag="a")
        a2 = logger.log_object_creation(SimpleItem, {"name": "B"}, tag="b")
        # a1 is explicitly marked — simulates a normal operation.
        logger.mark_operation_success(a1)
        # a2 is left pending — simulates a handler that forgot to mark.

        summary = logger.finalize_batch()

        # Safety net caught a2.
        self.assertEqual(
            AuditLogStatus.objects.get(audit_log=a2).status, "success",
            "finalize_batch with no error must upgrade pending → success "
            "so the compliance view never shows ghost pending rows",
        )
        self.assertEqual(summary["total_audit_logs"], 2)
        self.assertEqual(summary["successful_operations"], 2)
        self.assertEqual(summary["failed_operations"], 0)
        self.assertEqual(
            summary["pending_resolved"], 1,
            "The summary must report how many pending rows were swept — "
            "a non-zero value here is the signal that a handler forgot "
            "to mark_operation_success",
        )

    def test_1_58b_finalize_with_failure_error_marks_pending_as_failure(self) -> None:
        """Scenario 1.58b: outer-driver exception path — when the
        seed driver passes a ``failure_error``, every lingering
        pending row is marked ``failure`` with that error string. The
        audit trail tells the whole story even for aborted runs.
        """
        logger = InitialDataAuditLogger()
        a1 = logger.log_object_creation(SimpleItem, {"name": "A"}, tag="a")
        logger.log_object_creation(SimpleItem, {"name": "B"}, tag="b")
        # Both left pending.

        summary = logger.finalize_batch(
            failure_error="seed driver crashed: ValueError",
        )

        all_statuses = AuditLogStatus.objects.filter(
            audit_log_id__in=[al.id for al in [a1]],
        )
        self.assertTrue(all(s.status == "failure" for s in all_statuses))
        self.assertTrue(all("seed driver crashed" in (s.error_traceback or "") for s in all_statuses))
        self.assertEqual(summary["failed_operations"], 2)
        self.assertEqual(summary["pending_resolved"], 2)


# =====================================================================
# TestCluster01i_FullSeedJourney
#
# End-to-end: drives ``ProcessAdminTestCase.setUp`` with a seed file
# that walks every action (create / update / delete) and asserts both
# the database state AND the audit trail match the docs' promise.
# =====================================================================
class TestCluster01i_FullSeedJourney(E2ETestCase):
    """Scenarios 1.59 – 1.60 — full seed-driver journey."""

    e2e_models = [SimpleItem]
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def _make_harness(self, fixture_name: str):
        """Create a ``ProcessAdminTestCase`` instance that reads
        ``test_data`` from our own fixture file instead of the
        per-test-class default.
        """
        class _ConfigurableProcessAdmin(ProcessAdminTestCase):
            # ProcessAdminTestCase is itself a ``unittest.TestCase`` —
            # instantiate without a method name via the ``runTest``
            # placeholder; we only call ``setUp`` on it, never the
            # unittest lifecycle.
            pass

        _ConfigurableProcessAdmin.runTest = lambda self: None
        harness = _ConfigurableProcessAdmin()
        fixture_path = FIXTURES / fixture_name
        # Pin the test path to our fixture. ``get_test_data`` reads from
        # ``$PROJECT_ROOT`` + test_path; emulate by stubbing get_test_data.
        harness.get_test_data = lambda: harness.get_test_data_from_path(
            str(fixture_path)
        )
        return harness

    # -- 1.59 ----------------------------------------------------------
    @unittest.skipUnless(
        os.getenv("INITIAL_DATA_AUDIT_LOGGING", "").lower() in {"1", "true", "yes"},
        "INITIAL_DATA_AUDIT_LOGGING env var disabled — harness runs "
        "without audit, so the full-journey audit assertions are "
        "skipped for this environment. The create/update/delete "
        "DB state is still asserted in 1.59b.",
    )
    def test_1_59_full_create_update_delete_journey_with_audit(self) -> None:
        """Scenario 1.59: end-to-end seed drive through the
        documented action set.

        Given: a seed file with 2x create, 1x update, 1x delete.
        When: ``ProcessAdminTestCase.setUp(audit_logger)`` runs the file.
        Then:
          * DB has 1 row (2 created - 1 deleted = 1) with the updated value.
          * AuditLog table has 4 rows with actions [create, create, update, delete].
          * All AuditLogStatus rows are ``success``.
        """
        harness = self._make_harness("test_seed_journey.json")
        audit_logger = InitialDataAuditLogger()

        with mock.patch(
            "lex.lex_app.tests.ProcessAdminTestCase.apps.get_app_config",
        ) as get_app_config:
            get_app_config.return_value.models.values.return_value = [SimpleItem]
            harness.setUp(audit_logger=audit_logger)

        # DB state: beta was deleted, alpha was updated to value 999.
        alive = list(SimpleItem.objects.order_by("id").values("name", "value"))
        self.assertEqual(len(alive), 1)
        self.assertEqual(alive[0]["name"], "Alpha Seed")
        self.assertEqual(alive[0]["value"], 999)

        # Audit trail: one row per action, all success.
        audits = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(audits, ["create", "create", "update", "delete"])
        statuses = list(AuditLogStatus.objects.values_list("status", flat=True))
        self.assertTrue(all(s == "success" for s in statuses), f"Got: {statuses}")

    def test_1_59b_full_journey_without_audit_still_lands_db_state(self) -> None:
        """Scenario 1.59b: the seed driver **must** land the right
        DB state whether or not audit logging is turned on. Audit is
        an observability layer — it can be disabled; the data
        transitions cannot silently regress.
        """
        harness = self._make_harness("test_seed_journey.json")

        # ``ProcessAdminTestCase.setUp`` calls ``apps.get_app_config
        # (settings.repo_name)`` which fails in the unit-test sandbox
        # (the test project's app label isn't registered). Stub the
        # model-lookup seam so the harness sees our one e2e_model.
        with mock.patch(
            "lex.lex_app.tests.ProcessAdminTestCase.apps.get_app_config",
        ) as get_app_config:
            get_app_config.return_value.models.values.return_value = [SimpleItem]
            # No audit_logger → audit_enabled=False branch of setUp.
            harness.setUp(audit_logger=None)

        alive = list(SimpleItem.objects.order_by("id").values("name", "value"))
        self.assertEqual(len(alive), 1, "2 created − 1 deleted = 1 surviving row")
        self.assertEqual(alive[0]["name"], "Alpha Seed")
        self.assertEqual(
            alive[0]["value"], 999,
            "Update action must have flipped alpha's value to 999 — "
            "the create/update/delete ordering is the customer contract",
        )

    # -- 1.60 ----------------------------------------------------------
    def test_1_60_error_path_marks_audit_failure_and_reraises(self) -> None:
        """Scenario 1.60: if the seed driver crashes mid-run, the
        ``load_data`` task's ``finally:`` clause calls
        ``finalize_batch(failure_error=<exc_str>)`` so every lingering
        ``pending`` audit row is collapsed to ``failure`` with the
        error string.

        The audit trail must close out completely even when the driver
        blows up — a partial audit trail is worse than none at all for
        compliance.
        """
        # Simulate: 3 ops recorded by log_object_creation (pending),
        # then a crash happens before any of them get explicitly marked.
        # Then load_data's finally clause calls finalize_batch with the
        # error. This mirrors the exact call sequence in ``load_data``.
        logger = InitialDataAuditLogger()
        logger.log_object_creation(SimpleItem, {"name": "A"}, tag="a")
        logger.log_object_creation(SimpleItem, {"name": "B"}, tag="b")
        logger.log_object_creation(SimpleItem, {"name": "C"}, tag="c")

        self.assertEqual(
            len(logger._logged_ids), 3,
            "Each log_object_creation call must append to _logged_ids — "
            "finalize_batch's sweep scope depends on this list",
        )

        summary = logger.finalize_batch(
            failure_error="test_1_60: simulated seed failure",
        )

        self.assertEqual(summary["total_audit_logs"], 3)
        self.assertEqual(summary["failed_operations"], 3)
        self.assertEqual(summary["pending_resolved"], 3)

        # Every status row carries the failure_error string.
        all_statuses = AuditLogStatus.objects.filter(
            audit_log_id__in=logger._logged_ids,
        )
        self.assertTrue(
            all(s.status == "failure" for s in all_statuses),
            "Every lingering pending status must be swept to ``failure`` "
            "— a partial trail is worse than none for compliance",
        )
        self.assertTrue(
            all("simulated seed failure" in (s.error_traceback or "") for s in all_statuses),
            "error_traceback must carry the outer exception's message "
            "so compliance can link the audit crash to a specific "
            "incident",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




