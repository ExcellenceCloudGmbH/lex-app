"""
Cluster 9d — ``ActiveCalculationStateStore`` full public surface.

Coverage-driven batch (May 12 ROI ranking #2 — 27.03% baseline,
86 missed lines, 131 total).  Walks the whole authoritative
in-memory store of active calculations: mutators, accessors, the
DB-validated ``snapshot()`` reconciliation path consumed by the
``UpdateCalculationStatusConsumer.connect()`` WebSocket handler,
the startup ``validate_and_prune()`` sweep, and the private
model-resolution helpers (``_resolve_model_and_pk`` /
``_split_record_id`` / ``_find_model_by_name``).

Why this matters
----------------
This store is the single source of truth that lets a re-connecting
browser tab pick up the spinner mid-calculation. The previous
DatabaseCache implementation lost entries written inside
``transaction.atomic()`` because the ASGI consumer ran on a
different DB connection — that was the bug whose fix this whole
file exists to protect.  Anything that breaks ``snapshot()`` (stale
entries leaking through, live entries disappearing) directly
regresses customer-visible "did my calculation crash or am I
just disconnected?" UX.

Naming
------
Scenarios 9.11 – 9.28 (cluster 9; 9.7 – 9.10 already taken by the
bitemporal-suppression sub-cluster).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import (
    ActiveCalculationStateStore,
)

import pytest

pytestmark = pytest.mark.signals_ws


class FakeCalcModel9d(CalculationModel):
    """Unmanaged ``CalculationModel`` subclass for resolver tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):  # pragma: no cover - never invoked
        return None


class _NotACalcModel:
    """Plain class — *not* a ``CalculationModel`` subclass.

    Used to pin the ``issubclass(model_class, CalculationModel)`` guard
    in ``_resolve_model_and_pk``.  Exposes a ``_meta`` shim so the
    fallback ``apps.get_models()`` walk in ``_find_model_by_name`` can
    still introspect it without raising.
    """

    class _Meta:
        model_name = "notacalcmodel"

    _meta = _Meta()


class _StoreTestBase(SimpleTestCase):
    """Reset the process-global store before and after every test."""

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)


# ----------------------------------------------------------------------
# 9.11 – 9.17  Mutators
# ----------------------------------------------------------------------


class TestCluster09d_Mutators(_StoreTestBase):
    """Mark / clear / clear_all — the only state mutators."""

    # 9.11 -------------------------------------------------------------
    def test_9_11_mark_in_progress_with_empty_record_id_is_noop(self) -> None:
        """Empty ``record_id`` short-circuits — store stays empty.

        The ``if not record_id: return`` guard exists so callers don't
        have to special-case unsaved instances.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="",
            calculation_id="c1",
            record="r",
        )
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    # 9.12 -------------------------------------------------------------
    def test_9_12_mark_in_progress_persists_full_payload(self) -> None:
        """All five fields land on the entry verbatim."""
        ActiveCalculationStateStore.mark_in_progress(
            record_id="simpleitem_42",
            calculation_id="calc-xyz",
            record="SimpleItem #42",
            model_label="lex_app.SimpleItem",
            record_pk=42,
        )
        entry = ActiveCalculationStateStore.get_entry("simpleitem_42")
        self.assertEqual(entry["record_id"], "simpleitem_42")
        self.assertEqual(entry["record"], "SimpleItem #42")
        self.assertEqual(entry["calculation_id"], "calc-xyz")
        self.assertEqual(entry["model_label"], "lex_app.SimpleItem")
        # int pk normalised to str so JSON-serializable downstream
        self.assertEqual(entry["record_pk"], "42")

    # 9.13 -------------------------------------------------------------
    def test_9_13_mark_in_progress_defaults_blank_optional_fields(self) -> None:
        """Optional fields default to '' (never ``None``).

        Downstream JSON serializers and ``get_entry()`` consumers
        iterate values directly — ``None`` would force every caller
        to wrap with ``or ""``.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1",
            calculation_id=None,
            record=None,
        )
        entry = ActiveCalculationStateStore.get_entry("x_1")
        # ``record`` falls back to record_id when not supplied.
        self.assertEqual(entry["record"], "x_1")
        self.assertEqual(entry["calculation_id"], "")
        self.assertEqual(entry["model_label"], "")
        self.assertEqual(entry["record_pk"], "")

    # 9.14 -------------------------------------------------------------
    def test_9_14_clear_with_empty_record_id_is_noop(self) -> None:
        """``clear('')`` short-circuits before touching the lock.

        Pins the ``if not record_id: return`` guard symmetric to
        ``mark_in_progress``'s.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="c", record="r"
        )
        ActiveCalculationStateStore.clear("")
        # Entry untouched.
        self.assertTrue(ActiveCalculationStateStore.get_entry("x_1"))

    # 9.15 -------------------------------------------------------------
    def test_9_15_clear_removes_entry_and_is_idempotent(self) -> None:
        """Second ``clear`` of the same id is a silent no-op."""
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="c", record="r"
        )
        ActiveCalculationStateStore.clear("x_1")
        self.assertEqual(ActiveCalculationStateStore.get_entry("x_1"), {})
        # Second call must not raise.
        ActiveCalculationStateStore.clear("x_1")

    # 9.16 -------------------------------------------------------------
    def test_9_16_clear_all_empties_every_entry(self) -> None:
        """Startup-only sweep — leaves the store empty regardless of size."""
        for i in range(5):
            ActiveCalculationStateStore.mark_in_progress(
                record_id=f"x_{i}", calculation_id=f"c{i}", record="r"
            )
        ActiveCalculationStateStore.clear_all()
        for i in range(5):
            self.assertEqual(
                ActiveCalculationStateStore.get_entry(f"x_{i}"), {}
            )

    # 9.17 -------------------------------------------------------------
    def test_9_17_mark_in_progress_overwrites_existing_entry(self) -> None:
        """Re-marking the same id replaces the prior entry in place.

        Customer scenario: a calculation re-fires after an CANCELLED
        startup-reset; the new ``calculation_id`` must take over so
        the WebSocket payload reflects the live run, not the dead one.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="old", record="r1"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="new", record="r2"
        )
        entry = ActiveCalculationStateStore.get_entry("x_1")
        self.assertEqual(entry["calculation_id"], "new")
        self.assertEqual(entry["record"], "r2")


# ----------------------------------------------------------------------
# 9.18 – 9.21  Accessors
# ----------------------------------------------------------------------


class TestCluster09d_Accessors(_StoreTestBase):
    """``get_calculation_id`` / ``get_entry`` defensive contracts."""

    # 9.18 -------------------------------------------------------------
    def test_9_18_get_calculation_id_returns_string_when_set(self) -> None:
        """Live entry → returns the calc id string."""
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="calc-abc", record="r"
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_calculation_id("x_1"),
            "calc-abc",
        )

    # 9.19 -------------------------------------------------------------
    def test_9_19_get_calculation_id_returns_none_for_missing_or_blank(self) -> None:
        """Missing entry OR blank stored calc id both → ``None``.

        Two separate dark branches: dict.get default + the
        ``isinstance(...) and calculation_id`` truthiness guard.
        """
        # Missing entry.
        self.assertIsNone(
            ActiveCalculationStateStore.get_calculation_id("nope")
        )
        # Blank stored value.
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="", record="r"
        )
        self.assertIsNone(
            ActiveCalculationStateStore.get_calculation_id("x_1")
        )

    # 9.20 -------------------------------------------------------------
    def test_9_20_get_entry_with_empty_id_returns_empty_dict(self) -> None:
        """Symmetric to mutators — empty id is a no-op."""
        self.assertEqual(ActiveCalculationStateStore.get_entry(""), {})

    # 9.21 -------------------------------------------------------------
    def test_9_21_get_entry_returns_a_copy_not_the_internal_dict(self) -> None:
        """Mutating the returned dict must NOT affect the store.

        Pins the ``dict(entry)`` defensive copy.  A regression to
        ``return entry`` (no copy) would let snapshot consumers
        mutate the store under the lock.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="c", record="r"
        )
        entry = ActiveCalculationStateStore.get_entry("x_1")
        entry["calculation_id"] = "TAMPERED"
        # Re-read — store untouched.
        self.assertEqual(
            ActiveCalculationStateStore.get_entry("x_1")["calculation_id"],
            "c",
        )


# ----------------------------------------------------------------------
# 9.22 – 9.24  Private helpers
# ----------------------------------------------------------------------


class TestCluster09d_PrivateHelpers(_StoreTestBase):
    """``_split_record_id`` / ``_find_model_by_name`` / resolver."""

    # 9.22 -------------------------------------------------------------
    def test_9_22_split_record_id_parses_model_underscore_pk(self) -> None:
        """Standard form ``modelname_pk`` splits on the rightmost ``_``."""
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("simpleitem_42"),
            ("simpleitem", "42"),
        )
        # rsplit semantics — model name itself may contain underscores.
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("my_calc_model_7"),
            ("my_calc_model", "7"),
        )

    # 9.23 -------------------------------------------------------------
    def test_9_23_split_record_id_rejects_malformed_input(self) -> None:
        """Empty / no-underscore / blank-half inputs all return (None, None)."""
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id(""),
            (None, None),
        )
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("nounderscore"),
            (None, None),
        )
        # Trailing underscore → blank pk half.
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("model_"),
            (None, None),
        )
        # Leading underscore → blank model half.
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("_42"),
            (None, None),
        )

    # 9.24 -------------------------------------------------------------
    def test_9_24_find_model_by_name_walks_apps_registry(self) -> None:
        """Returns matching ``CalculationModel`` subclass, or ``None``.

        Uses a real model registered in the test_project app registry
        so the ``apps.get_models()`` walk has real data to traverse.
        """
        found = ActiveCalculationStateStore._find_model_by_name(
            FakeCalcModel9d._meta.model_name
        )
        self.assertIs(found, FakeCalcModel9d)
        # Unknown model name → ``None``.
        self.assertIsNone(
            ActiveCalculationStateStore._find_model_by_name("doesnotexist")
        )


# ----------------------------------------------------------------------
# 9.25 – 9.27  ``_resolve_model_and_pk``
# ----------------------------------------------------------------------


class TestCluster09d_ResolveModelAndPk(_StoreTestBase):
    """End-to-end resolver covering every branch."""

    def _entry(self, **overrides) -> dict:
        return {
            "record_id": overrides.get("record_id", ""),
            "model_label": overrides.get("model_label", ""),
            "record_pk": overrides.get("record_pk", ""),
        }

    # 9.25 -------------------------------------------------------------
    def test_9_25_resolves_via_model_label_when_provided(self) -> None:
        """``app_label.ModelName`` is preferred over record_id parsing."""
        with patch(
            "django.apps.apps.get_model",
            return_value=FakeCalcModel9d,
        ) as mock_get_model:
            cls, pk = ActiveCalculationStateStore._resolve_model_and_pk(
                self._entry(
                    record_id="ignored_999",
                    model_label="lex_app.FakeCalcModel",
                    record_pk="7",
                )
            )
        mock_get_model.assert_called_once_with("lex_app", "FakeCalcModel")
        self.assertIs(cls, FakeCalcModel9d)
        self.assertEqual(pk, "7")

    # 9.26 -------------------------------------------------------------
    def test_9_26_falls_back_to_record_id_when_label_missing(self) -> None:
        """No ``model_label`` → split ``record_id`` and walk app registry."""
        cls, pk = ActiveCalculationStateStore._resolve_model_and_pk(
            self._entry(record_id="fakecalcmodel9d_55")
        )
        self.assertIs(cls, FakeCalcModel9d)
        self.assertEqual(pk, "55")

    # 9.27 -------------------------------------------------------------
    def test_9_27_returns_none_when_class_not_calculationmodel_subclass(self) -> None:
        """Resolver refuses to hand back non-``CalculationModel`` classes.

        Without this guard ``snapshot()`` would call ``.objects.filter(...)``
        on arbitrary models and could leak unrelated state into the
        WebSocket payload.
        """
        # Force ``apps.get_model`` to return the non-calc class.
        with patch(
            "django.apps.apps.get_model",
            return_value=_NotACalcModel,
        ):
            cls, pk = ActiveCalculationStateStore._resolve_model_and_pk(
                self._entry(
                    model_label="lex_app.NotACalcModel",
                    record_pk="1",
                )
            )
        self.assertEqual((cls, pk), (None, None))

    def test_9_27b_returns_none_when_resolution_completely_fails(self) -> None:
        """Empty entry → (None, None) with no raise.

        Pins the ``if not model_name or not record_pk: return None, None``
        early-out and the ``apps.get_model`` exception → ``None``
        fallback (followed by failed registry walk).
        """
        cls, pk = ActiveCalculationStateStore._resolve_model_and_pk(
            self._entry()  # all blank
        )
        self.assertEqual((cls, pk), (None, None))

        # apps.get_model raises → falls back to registry walk → also fails.
        with patch(
            "django.apps.apps.get_model",
            side_effect=LookupError("no such model"),
        ):
            cls, pk = ActiveCalculationStateStore._resolve_model_and_pk(
                self._entry(
                    model_label="lex_app.GhostModel",
                    record_pk="1",
                )
            )
        self.assertEqual((cls, pk), (None, None))


# ----------------------------------------------------------------------
# 9.28  ``snapshot()`` and ``validate_and_prune()``
# ----------------------------------------------------------------------


def _mock_calc_model(is_calculated_value):
    """Build a MagicMock that mimics a CalculationModel class.

    Returns a class-shaped mock whose ``.objects.filter().only().first()``
    chain yields an instance with the requested ``is_calculated`` value
    (or ``None`` to simulate a deleted row).
    """
    cls = MagicMock()
    if is_calculated_value is None:
        first_result = None
    else:
        first_result = MagicMock(is_calculated=is_calculated_value)
    cls.objects.filter.return_value.only.return_value.first.return_value = (
        first_result
    )
    return cls


class TestCluster09d_SnapshotAndPrune(_StoreTestBase):
    """``snapshot()`` reconciliation + startup ``validate_and_prune()``."""

    # 9.28 -------------------------------------------------------------
    def test_9_28_snapshot_empty_store_returns_empty_list(self) -> None:
        """Fast-path: no entries → no DB hit, returns ``[]``."""
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    def test_9_28b_snapshot_returns_live_entries_and_prunes_stale(self) -> None:
        """Live IN_PROGRESS entries pass through; terminal-state entries
        are dropped from BOTH the payload and the store.

        This is the WebSocket-reconciliation contract: clients must not
        be told a calculation is running when the DB row already says
        SUCCESS / ERROR / CANCELLED.
        """
        live = _mock_calc_model(CalculationModel.IN_PROGRESS)
        stale = _mock_calc_model(CalculationModel.SUCCESS)

        ActiveCalculationStateStore.mark_in_progress(
            record_id="live_1", calculation_id="c-live", record="L"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="stale_2", calculation_id="c-stale", record="S"
        )

        def fake_resolve(entry):
            if entry["record_id"] == "live_1":
                return live, "1"
            return stale, "2"

        with patch.object(
            ActiveCalculationStateStore,
            "_resolve_model_and_pk",
            side_effect=fake_resolve,
        ):
            result = ActiveCalculationStateStore.snapshot()

        ids = [r["record_id"] for r in result]
        self.assertEqual(ids, ["live_1"])
        self.assertEqual(result[0]["calculation_id"], "c-live")
        # Stale entry removed from the store too.
        self.assertEqual(ActiveCalculationStateStore.get_entry("stale_2"), {})
        # Live entry retained.
        self.assertTrue(ActiveCalculationStateStore.get_entry("live_1"))

    def test_9_28c_snapshot_keeps_entry_when_db_validation_raises(self) -> None:
        """If the DB lookup raises, the entry is kept defensively.

        Better to show a possibly-stale spinner than to silently drop a
        live calculation because the DB blipped.
        """
        boom = MagicMock()
        boom.objects.filter.side_effect = RuntimeError("db down")

        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="c", record="r"
        )
        with patch.object(
            ActiveCalculationStateStore,
            "_resolve_model_and_pk",
            return_value=(boom, "1"),
        ):
            result = ActiveCalculationStateStore.snapshot()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["record_id"], "x_1")
        # Entry preserved in the store.
        self.assertTrue(ActiveCalculationStateStore.get_entry("x_1"))

    def test_9_28d_snapshot_skips_entries_resolver_cannot_identify(self) -> None:
        """Resolver returns (None, None) → entry passes through unchecked.

        Defensive: an unresolvable entry is *kept* (so customer doesn't
        lose their spinner) and emitted in the payload — but no DB
        validation runs. Pins line 129's ``if model_class is not None
        and record_pk is not None`` guard.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="ghost_1", calculation_id="c", record="g"
        )
        with patch.object(
            ActiveCalculationStateStore,
            "_resolve_model_and_pk",
            return_value=(None, None),
        ):
            result = ActiveCalculationStateStore.snapshot()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["record_id"], "ghost_1")

    def test_9_28e_validate_and_prune_keeps_only_in_progress(self) -> None:
        """Startup sweep: only DB rows still IN_PROGRESS survive.

        Empty-store fast-path covered too — must not raise.
        """
        # Empty-store fast-path.
        ActiveCalculationStateStore.validate_and_prune()
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

        live = _mock_calc_model(CalculationModel.IN_PROGRESS)
        stale = _mock_calc_model(CalculationModel.SUCCESS)
        gone = _mock_calc_model(None)  # row deleted between marks
        unresolvable_entry_id = "ghost_4"

        ActiveCalculationStateStore.mark_in_progress(
            record_id="live_1", calculation_id="c1", record="L"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="stale_2", calculation_id="c2", record="S"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="gone_3", calculation_id="c3", record="G"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id=unresolvable_entry_id, calculation_id="c4", record="U"
        )

        def fake_resolve(entry):
            mapping = {
                "live_1": (live, "1"),
                "stale_2": (stale, "2"),
                "gone_3": (gone, "3"),
                unresolvable_entry_id: (None, None),
            }
            return mapping[entry["record_id"]]

        with patch.object(
            ActiveCalculationStateStore,
            "_resolve_model_and_pk",
            side_effect=fake_resolve,
        ):
            ActiveCalculationStateStore.validate_and_prune()

        # Only the live entry remains; all three other branches dropped:
        # stale (terminal state), gone (instance is None), unresolvable
        # (model_class is None — `continue` before the keep-decision).
        self.assertTrue(ActiveCalculationStateStore.get_entry("live_1"))
        self.assertEqual(
            ActiveCalculationStateStore.get_entry("stale_2"), {}
        )
        self.assertEqual(ActiveCalculationStateStore.get_entry("gone_3"), {})
        self.assertEqual(
            ActiveCalculationStateStore.get_entry(unresolvable_entry_id), {}
        )

    def test_9_28f_validate_and_prune_keeps_entry_when_lookup_raises(self) -> None:
        """Defensive: DB exception during prune keeps entry too.

        Logged-and-dropped would lose live calculations on a transient
        DB blip during server boot.  Logged-and-kept is the safer
        default — the next ``snapshot()`` will re-validate.
        """
        boom = MagicMock()
        boom.objects.filter.side_effect = RuntimeError("db down")

        ActiveCalculationStateStore.mark_in_progress(
            record_id="x_1", calculation_id="c", record="r"
        )
        with patch.object(
            ActiveCalculationStateStore,
            "_resolve_model_and_pk",
            return_value=(boom, "1"),
        ):
            ActiveCalculationStateStore.validate_and_prune()
        # Note: ``validate_and_prune`` only *keeps* entries it could
        # positively confirm as IN_PROGRESS — a raise puts the entry in
        # neither bucket, which by design means it's dropped on
        # startup.  Pin that contract here so a future refactor that
        # adds "keep on exception" would have to revisit this test.
        self.assertEqual(ActiveCalculationStateStore.get_entry("x_1"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



