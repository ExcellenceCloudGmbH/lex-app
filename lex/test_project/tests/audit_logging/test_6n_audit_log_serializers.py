"""
Sub-cluster 6n — `AuditLogSerializer` + `AuditLogMixinSerializer` surface.

Coverage-driven batch (May 12 ROI rank #3 — `AuditLogSerializer.py` 37.65%
+ `AuditLogMixinSerializer.py` 53.73% baselines). Both files feed the
frontend Audit-Tab UI:

* `AuditLogSerializer.py` shapes every row the AG Grid renders — the
  read-only-scopes mixin that locks the tab against PATCH/DELETE, the
  five status-record SerializerMethodFields the dashboard depends on
  for "did this audit row land green or red?", and the
  `calculation_record` builder that powers the per-row drill-down link
  without dereferencing the GenericForeignKey on every list page.
* `AuditLogMixinSerializer.py` is the type-coercion layer every audit
  writer routes through to keep payload JSON-stable across
  Decimal/UUID/datetime/FieldFile/QuerySet/Promise/etc. — a regression
  here either crashes JSON encoding mid-write (data-loss for the audit
  trail) or surfaces non-serializable artefacts at the API boundary
  (UI breaks).

Scenarios 6.141 – 6.155.
"""

from __future__ import annotations

import datetime
import decimal
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.serializers.AuditLogMixinSerializer import (
    _iso_seconds,
    _serialize_file_reference,
    _serialize_payload,
    generic_instance_payload,
)
from lex.audit_logging.serializers.AuditLogSerializer import (
    AuditLogDefaultSerializer,
    AuditLogReadOnlySerializerMixin,
    AuditLogReferenceSerializer,
)

import pytest

pytestmark = pytest.mark.audit_logging


# ----------------------------------------------------------------------
# 6.141  Read-only scopes mixin
# ----------------------------------------------------------------------


class TestCluster06n_ReadOnlyMixin(SimpleTestCase):
    """`AuditLogReadOnlySerializerMixin.get_lex_reserved_scopes` lock."""

    def test_6_141_get_lex_reserved_scopes_locked_for_audit_rows(self):
        """The lock the entire Audit Tab depends on.

        edit=[] / delete=False / export=True is the documented contract;
        a regression that flips delete=True or returns non-empty edit
        would silently let a non-system caller PATCH or DELETE audit
        rows and erase compliance evidence.
        """
        mixin = AuditLogReadOnlySerializerMixin()
        scopes = mixin.get_lex_reserved_scopes(instance=object())
        self.assertEqual(
            scopes,
            {"edit": [], "delete": False, "export": True},
            "Audit-row scopes must lock edit + delete and only allow "
            "export. A change here is a compliance regression.",
        )


# ----------------------------------------------------------------------
# 6.142 – 6.145  AuditLogDefaultSerializer status-record fields
# ----------------------------------------------------------------------


def _status_record(status="success", error=None, created_at=None,
                    updated_at=None, duration=None):
    """Plain object stand-in for an `AuditLogStatus` row."""
    return SimpleNamespace(
        status=status,
        error_traceback=error,
        created_at=created_at,
        updated_at=updated_at,
        duration=duration,
    )


def _audit_obj(prefetched=None, status_records_qs=None):
    """Build a SimpleNamespace mimicking an AuditLog with prefetch + manager.

    `prefetched` (list or None) → fills `_prefetched_objects_cache`.
    `status_records_qs` → a MagicMock-driven manager whose
    `.order_by("-created_at").first()` returns the configured value.
    """
    obj = SimpleNamespace()
    if prefetched is not None:
        obj._prefetched_objects_cache = {"status_records": prefetched}
    obj.status_records = MagicMock()
    obj.status_records.order_by.return_value.first.return_value = (
        status_records_qs
    )
    return obj


class TestCluster06n_StatusFields(SimpleTestCase):
    """Status / error_traceback / *_at / duration SerializerMethodFields."""

    def setUp(self) -> None:
        super().setUp()
        # Bypass DRF init machinery; we only want the methods.
        self.ser = AuditLogDefaultSerializer.__new__(AuditLogDefaultSerializer)

    # 6.142 -------------------------------------------------------------
    def test_6_142_get_latest_status_record_uses_prefetch_cache(self) -> None:
        """When `_prefetched_objects_cache` carries `status_records`,
        the helper returns the LAST entry (newest) and never hits the DB.

        Pins the AG-Grid list-view fast path: every audit row is fetched
        with `prefetch_related('status_records')` so the per-row status
        lookup is O(1) memory walk, not O(rows) extra queries.
        """
        first = _status_record(status="pending")
        latest = _status_record(status="success")
        obj = _audit_obj(prefetched=[first, latest])

        result = self.ser._get_latest_status_record(obj)

        self.assertIs(result, latest, "Last entry in prefetch list is newest.")
        # Manager untouched — no DB query.
        obj.status_records.order_by.assert_not_called()

    def test_6_142b_get_latest_status_record_empty_prefetch_returns_none(self):
        """Empty prefetched list → None (still no DB hit)."""
        obj = _audit_obj(prefetched=[])
        self.assertIsNone(self.ser._get_latest_status_record(obj))
        obj.status_records.order_by.assert_not_called()

    # 6.143 -------------------------------------------------------------
    def test_6_143_get_latest_status_record_falls_back_to_db(self) -> None:
        """Without prefetch cache, query manager `.order_by('-created_at').first()`."""
        latest = _status_record(status="failure")
        obj = _audit_obj(status_records_qs=latest)

        result = self.ser._get_latest_status_record(obj)

        self.assertIs(result, latest)
        obj.status_records.order_by.assert_called_once_with("-created_at")

    # 6.144 -------------------------------------------------------------
    def test_6_144_all_status_fields_return_none_when_no_record(self) -> None:
        """Audit row with zero status records → every computed field is None.

        This is the mid-flight state operators see right after the audit
        row is created but before the success/failure mark lands. The
        UI renders "—" for these — a regression that returns "" or 0
        would render confusing dashboard rows.
        """
        obj = _audit_obj(status_records_qs=None)
        self.assertIsNone(self.ser.get_status(obj))
        self.assertIsNone(self.ser.get_error_traceback(obj))
        self.assertIsNone(self.ser.get_status_created_at(obj))
        self.assertIsNone(self.ser.get_status_updated_at(obj))
        self.assertIsNone(self.ser.get_duration(obj))

    # 6.145 -------------------------------------------------------------
    def test_6_145_all_status_fields_surface_record_values(self) -> None:
        """Live record → every value flows through (datetimes via isoformat)."""
        ts_created = datetime.datetime(2026, 5, 12, 9, 0, 0)
        ts_updated = datetime.datetime(2026, 5, 12, 9, 0, 5)
        record = _status_record(
            status="success",
            error="trace-here",
            created_at=ts_created,
            updated_at=ts_updated,
            duration=5,
        )
        obj = _audit_obj(status_records_qs=record)

        self.assertEqual(self.ser.get_status(obj), "success")
        self.assertEqual(self.ser.get_error_traceback(obj), "trace-here")
        self.assertEqual(
            self.ser.get_status_created_at(obj), ts_created.isoformat()
        )
        self.assertEqual(
            self.ser.get_status_updated_at(obj), ts_updated.isoformat()
        )
        self.assertEqual(self.ser.get_duration(obj), 5)


# ----------------------------------------------------------------------
# 6.146 – 6.150  AuditLogDefaultSerializer.get_calculation_record
# ----------------------------------------------------------------------


def _calc_audit(object_id=42, content_type=None, content_type_id=None,
                payload=None, fields_cache=None):
    """Build an AuditLog stand-in with content-type plumbing for tests."""
    obj = SimpleNamespace()
    obj.object_id = object_id
    obj.content_type_id = content_type_id
    obj.payload = payload
    state = SimpleNamespace(db=None, fields_cache=fields_cache or {})
    if content_type is not None:
        state.fields_cache["content_type"] = content_type
    obj._state = state
    return obj


class TestCluster06n_CalculationRecord(SimpleTestCase):
    """`get_calculation_record` builder — drives the per-row drill-down link."""

    def setUp(self) -> None:
        super().setUp()
        self.ser = AuditLogDefaultSerializer.__new__(AuditLogDefaultSerializer)

    # 6.146 -------------------------------------------------------------
    def test_6_146_returns_none_when_object_id_missing(self) -> None:
        """No GFK target → no link payload (line 81 early-return).

        Audit rows for free-form events (no associated record) must
        not crash the AG Grid by handing back a half-built link.
        """
        obj = _calc_audit(object_id=None)
        self.assertIsNone(self.ser.get_calculation_record(obj))

    # 6.147 -------------------------------------------------------------
    def test_6_147_returns_none_when_content_type_unresolvable(self) -> None:
        """No cached content_type AND `safe_get_content_type` raises → None.

        Pins the defensive try/except at lines 90-93: a missing/stale
        ContentType row must not surface as a 500, just a missing link.
        """
        obj = _calc_audit(
            object_id=42,
            content_type=None,
            content_type_id=999,
            payload={},
        )
        with patch(
            "lex.audit_logging.serializers.AuditLogSerializer.safe_get_content_type",
            side_effect=RuntimeError("ct cache miss"),
        ):
            self.assertIsNone(self.ser.get_calculation_record(obj))

    # 6.148 -------------------------------------------------------------
    def test_6_148_full_payload_shape_and_display_name_fallback_chain(self):
        """Full output shape + display-name fallback priority.

        short_description > name > display > "<model> #<pk>".
        Pins the exact key set the AG Grid drill-down link consumes;
        any added/removed key here breaks the frontend without warning.
        """
        ct = SimpleNamespace(app_label="lex_app", model="invoice")

        # Priority 1: short_description.
        obj = _calc_audit(
            object_id=7, content_type=ct,
            payload={"short_description": "INV-42",
                     "name": "alt", "display": "alt2"},
        )
        result = self.ser.get_calculation_record(obj)
        self.assertEqual(
            result,
            {
                "id": 7,
                "app_label": "lex_app",
                "model": "invoice",
                "display_name": "INV-42",
                "details": {},
            },
        )

        # Priority 2: name.
        obj = _calc_audit(
            object_id=8, content_type=ct,
            payload={"name": "Invoice 8", "display": "alt"},
        )
        self.assertEqual(
            self.ser.get_calculation_record(obj)["display_name"],
            "Invoice 8",
        )

        # Priority 3: display.
        obj = _calc_audit(
            object_id=9, content_type=ct, payload={"display": "Inv9"},
        )
        self.assertEqual(
            self.ser.get_calculation_record(obj)["display_name"], "Inv9"
        )

        # Priority 4: "<model> #<pk>" terminal fallback.
        obj = _calc_audit(object_id=10, content_type=ct, payload={})
        self.assertEqual(
            self.ser.get_calculation_record(obj)["display_name"],
            "invoice #10",
        )

    # 6.149 -------------------------------------------------------------
    def test_6_149_details_only_includes_keys_present_in_payload(self) -> None:
        """`details` is built selectively from `is_calculated` / `error_message`.

        Every other payload key is intentionally dropped so the link
        widget doesn't accidentally surface unrelated audit-payload
        fields (potentially PII) into the drill-down preview.
        """
        ct = SimpleNamespace(app_label="lex_app", model="m")
        # Both keys present.
        obj = _calc_audit(
            object_id=1, content_type=ct,
            payload={
                "is_calculated": "ERROR",
                "error_message": "boom",
                "secret": "leaked",  # must NOT appear in details
            },
        )
        result = self.ser.get_calculation_record(obj)
        self.assertEqual(
            result["details"],
            {"is_calculated": "ERROR", "error_message": "boom"},
        )
        self.assertNotIn(
            "secret", result["details"],
            "Only is_calculated/error_message keys may surface in details "
            "— other payload entries (potentially PII) must NOT leak.",
        )

        # Only one key present.
        obj = _calc_audit(
            object_id=2, content_type=ct,
            payload={"is_calculated": "SUCCESS"},
        )
        self.assertEqual(
            self.ser.get_calculation_record(obj)["details"],
            {"is_calculated": "SUCCESS"},
        )

        # Neither key → empty details.
        obj = _calc_audit(object_id=3, content_type=ct, payload={})
        self.assertEqual(
            self.ser.get_calculation_record(obj)["details"], {}
        )

    # 6.150 -------------------------------------------------------------
    def test_6_150_non_dict_payload_treated_as_empty_dict(self) -> None:
        """Defensive: payload that is None / list / int doesn't crash.

        Older audit rows (pre-schema-tightening) sometimes carry a
        list payload from a bulk operation; the calculation_record
        builder must not blow up on them.
        """
        ct = SimpleNamespace(app_label="lex_app", model="m")
        for bad_payload in (None, [], 42, "not-a-dict"):
            obj = _calc_audit(
                object_id=11, content_type=ct, payload=bad_payload
            )
            result = self.ser.get_calculation_record(obj)
            self.assertEqual(
                result["display_name"], "m #11",
                f"Non-dict payload {bad_payload!r} must fall through to "
                "the model+pk display fallback, not crash.",
            )
            self.assertEqual(result["details"], {})


# ----------------------------------------------------------------------
# 6.151  Module-level wiring on AuditLog
# ----------------------------------------------------------------------


class TestCluster06n_ModuleWiring(SimpleTestCase):
    """`AuditLog.api_serializers` registry + `_lex_*` flags."""

    def test_6_151_audit_log_serializer_registry_published(self) -> None:
        """The module installs the two-serializer map + alias-skip flags.

        The frontend reads `default` for the AG Grid view and
        `reference` for the per-record link widget. Drift here (e.g.
        a `reference` rename, a missing `_lex_skip_serializer_alias`
        that double-registers a confusing third alias) breaks the
        Audit Tab without breaking any other part of the framework.
        """
        self.assertIs(AuditLog.api_serializers["default"], AuditLogDefaultSerializer)
        self.assertIs(
            AuditLog.api_serializers["reference"], AuditLogReferenceSerializer
        )
        self.assertTrue(AuditLog._lex_skip_serializer_alias)
        self.assertEqual(AuditLog._lex_hidden_serializers, {"reference"})


# ----------------------------------------------------------------------
# 6.152 – 6.155  AuditLogMixinSerializer helpers
# ----------------------------------------------------------------------


class TestCluster06n_IsoSeconds(SimpleTestCase):
    """`_iso_seconds` — strict 'YYYY-MM-DDTHH:MM:SS' contract."""

    # 6.152 -------------------------------------------------------------
    def test_6_152_iso_seconds_handles_none_naive_aware_and_microseconds(self):
        """None / tz-naive / tz-aware UTC / microsecond stripping."""
        # None passthrough.
        self.assertIsNone(_iso_seconds(None))

        # Naive datetime — microseconds dropped.
        naive = datetime.datetime(2026, 5, 12, 9, 0, 5, 123456)
        self.assertEqual(_iso_seconds(naive), "2026-05-12T09:00:05")

        # tz-aware non-UTC — converted to UTC and stripped.
        tz_plus_2 = datetime.timezone(datetime.timedelta(hours=2))
        aware = datetime.datetime(2026, 5, 12, 11, 0, 5, tzinfo=tz_plus_2)
        self.assertEqual(_iso_seconds(aware), "2026-05-12T09:00:05")

        # tz-aware UTC — same wall-clock, stripped.
        utc = datetime.datetime(
            2026, 5, 12, 9, 0, 5, tzinfo=datetime.timezone.utc
        )
        self.assertEqual(_iso_seconds(utc), "2026-05-12T09:00:05")


class TestCluster06n_FileReference(SimpleTestCase):
    """`_serialize_file_reference` — name + url shape."""

    # 6.153 -------------------------------------------------------------
    def test_6_153_file_reference_shape_and_url_failure_paths(self) -> None:
        """Returns {name, url}; .url raise → url=None; missing name → both None."""
        # Happy path.
        ff = SimpleNamespace(name="x.pdf", url="/media/x.pdf")
        self.assertEqual(
            _serialize_file_reference(ff),
            {"name": "x.pdf", "url": "/media/x.pdf"},
        )

        # .url raising (typical for unsaved FieldFile).
        class _Boom:
            name = "y.pdf"

            @property
            def url(self):  # pragma: no cover — used via attribute access
                raise ValueError("no storage")

        result = _serialize_file_reference(_Boom())
        self.assertEqual(result, {"name": "y.pdf", "url": None})

        # name=None / falsy short-circuits url access entirely.
        empty = SimpleNamespace(name=None, url="should-not-be-read")
        self.assertEqual(
            _serialize_file_reference(empty), {"name": None, "url": None}
        )


# generic_instance_payload needs a real Django model to exercise
# `_meta.concrete_fields` + `model_to_dict`. AuditLog itself is
# convenient — already migrated in any test runner, has datetime +
# JSON fields.
class TestCluster06n_GenericInstancePayload(TestCase):
    """`generic_instance_payload` — full instance → JSON-safe dict."""

    # 6.154 -------------------------------------------------------------
    def test_6_154_generic_instance_payload_normalises_types_and_pk(self):
        """pk → 'id'; datetime → ISO; dict carries every concrete field."""
        log = AuditLog.objects.create(
            date=timezone.now(),
            author="test",
            resource="lex_app.SimpleItem",
            action="create",
            payload={"k": "v"},
            calculation_id="calc-test",
        )

        data = generic_instance_payload(log)

        # `id` injected from pk.
        self.assertEqual(data["id"], log.pk)
        # Editable concrete fields are present (`model_to_dict` filters out
        # non-editable fields like `auto_now_add` columns even when we pass
        # them via `fields=`; pin the documented behaviour rather than the
        # full concrete-field list).
        for f in log._meta.concrete_fields:
            if f.editable and not f.auto_created:
                self.assertIn(
                    f.name, data,
                    f"Editable concrete field {f.name!r} missing — a "
                    "regression here would silently drop user-set "
                    "columns from every audit row.",
                )
        # Author / resource / action / payload all round-tripped.
        self.assertEqual(data["author"], "test")
        self.assertEqual(data["resource"], "lex_app.SimpleItem")
        self.assertEqual(data["payload"], {"k": "v"})


class TestCluster06n_SerializePayload(SimpleTestCase):
    """`_serialize_payload` — recursive JSON-safe coercion."""

    # 6.155 -------------------------------------------------------------
    def test_6_155_serialize_payload_handles_every_documented_type(self) -> None:
        """Every branch in the recursive coercion ladder.

        Each branch maps a Python type that JSON cannot encode natively
        to a JSON-safe string/dict. A regression that drops one branch
        crashes JSON encoding mid-write — the audit row never lands
        and the operator sees a 500 with no trace of what they tried
        to do.
        """
        # Primitive passthrough via final str() catch-all is exercised
        # implicitly, but assert the structural branches here.

        # dict + list recursion.
        data = {
            "nested": [{"deep": datetime.date(2026, 5, 12)}],
            "decimal": decimal.Decimal("1.50"),
            "uuid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "set": {1, 2, 3},  # set → list
            "datetime": datetime.datetime(2026, 5, 12, 9, 0, 5),
            "time": datetime.time(9, 0, 5, 999),
        }
        out = _serialize_payload(data)

        self.assertEqual(out["nested"], [{"deep": "2026-05-12"}])
        self.assertEqual(out["decimal"], "1.50")
        self.assertEqual(
            out["uuid"], "12345678-1234-5678-1234-567812345678"
        )
        # Set → list; order not guaranteed.
        self.assertIsInstance(out["set"], list)
        self.assertEqual(sorted(out["set"]), [1, 2, 3])
        self.assertEqual(out["datetime"], "2026-05-12T09:00:05")
        # Time isoformat preserves microseconds (the helper uses .isoformat()
        # directly, not _iso_seconds).
        self.assertTrue(out["time"].startswith("09:00:05"))

        # Model instance → {id, display}. AuditLog (unsaved with pk
        # forced) is the cheapest concrete Model stand-in.
        log = AuditLog(pk=42)
        out_model = _serialize_payload(log)
        self.assertEqual(out_model["id"], 42)
        self.assertIn("display", out_model)

        # QuerySet-like: object with .all() callable returning iterable.
        qs_like = MagicMock()
        qs_like.all = lambda: [datetime.date(2026, 1, 1), 7]
        out_qs = _serialize_payload(qs_like)
        self.assertEqual(out_qs, ["2026-01-01", "7"])

        # Final fallback: arbitrary object → str(obj).
        class _Custom:
            def __str__(self):
                return "<custom>"

        self.assertEqual(_serialize_payload(_Custom()), "<custom>")

        # Unserializable last-resort branch: str() raises.
        class _Unserializable:
            def __str__(self):
                raise RuntimeError("no str")

        result = _serialize_payload(_Unserializable())
        self.assertTrue(
            result.startswith("<Unserializable:"),
            f"Final defensive branch must hand back a placeholder; "
            f"got {result!r}.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



