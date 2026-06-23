"""Cluster 3f — lean ``_initial_state`` opt-in must not weaken any hook.

Intent (from docs/features/data-pipeline/lifecycle hooks.md and
docs/reference/LexModel Internals.md):

    django-lifecycle keeps a per-instance ``_initial_state`` snapshot — a full
    copy of every field captured at ``__init__`` and re-captured after each
    successful save — so that change-detection (``has_changed`` /
    ``initial_value``) and the conditional-hook forms (legacy ``when=`` /
    ``when_any=`` and modern ``condition=`` objects) can compare a field's
    current value against its value at the start of the save.

    On a large calculate-all that snapshot is a second full-field copy pinned
    per live row — the bulk of the v1->v2 per-instance memory regression that
    remains after the pre-validation buffer is freed (Cluster 3e). LexModel now
    offers an opt-in ``lex_lean_initial_state`` that narrows the snapshot to
    only the fields whose initial value is actually consulted: ``edited_at``
    (the framework's own ``update_edited_at`` depends on
    ``has_changed('edited_at')``), every field named in this class's hook
    ``when=`` / ``when_any=`` / ``condition=`` clauses, and anything listed in
    ``lex_initial_state_extra_fields`` for imperative ``has_changed`` callers.

    The customer-observable contract this sub-cluster pins:
      - opting in is a pure memory optimisation: every conditional hook fires
        exactly when it did before (proven field-for-field against an
        identical full-snapshot control model), and ``edited_at`` auto-stamping
        plus explicit-override handling are unchanged;
      - change-detection stays byte-for-byte correct for tracked fields, and an
        untracked field cleanly reports "unchanged" (the documented trade-off);
      - the snapshot really is smaller (the whole point); and
      - the post-save re-baseline and ``refresh_from_db`` re-baseline both keep
        producing the narrowed snapshot, so the optimisation survives an
        instance's full lifecycle.

Cluster 3f — scenarios 3.11–3.32. Type: E.
Covers: lex/core/models/LexModel.py.
Run: python -m lex pytest lex/test_project/tests/validation_hooks/test_3f_lean_initial_state.py -v
"""

from __future__ import annotations

import unittest

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    FullConditionalItem,
    LeanConditionalItem,
    LeanExtraFieldItem,
)

pytestmark = pytest.mark.validation_hooks


class TestCluster03f_LeanInitialState(E2ETestCase):
    """Cluster 3f: the lean ``_initial_state`` opt-in preserves every hook."""

    e2e_models = ALL_MODELS

    # Field set the lean snapshot is expected to retain for the conditional
    # models: edited_at (framework dependency) + every field named in a hook
    # when=/when_any=/condition= clause. ``name`` and ``note`` are referenced
    # by no change-detection clause, so they must be dropped.
    _TRACKED = {"edited_at", "status", "amount", "a", "b"}
    _UNTRACKED = {"name", "note"}

    def setUp(self) -> None:
        super().setUp()
        # hook_log is class-level and shared across a test session; reset the
        # per-class logs so each scenario observes only its own mutations.
        LeanConditionalItem.hook_log = []
        FullConditionalItem.hook_log = []
        LeanExtraFieldItem.hook_log = []

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _snapshot_keys(instance) -> set:
        """Field keys retained in an instance's current ``_initial_state``."""
        return set(instance._initial_state.initial_state.keys())

    def _create_reset(self, model, **kwargs):
        """Create a row and return it with a freshly re-baselined snapshot.

        The create path runs through ``save()``; its ``transaction.on_commit``
        re-baseline fires immediately under ``TransactionTestCase`` autocommit,
        so the returned instance's snapshot reflects the persisted values and
        ``hook_log`` is cleared ready for the update under test.
        """
        item = model.objects.create(**kwargs)
        model.hook_log = []
        return item

    # -- 3.11 ----------------------------------------------------------
    def test_3_11_default_on_explicit_opt_out_keeps_full_snapshot(self) -> None:
        """
        Scenario 3.11: lean is the default; explicit opt-out keeps the full
        snapshot.

        Given the framework default is ``lex_lean_initial_state = True``,
        And a model that explicitly sets ``lex_lean_initial_state = False``,
        When that opt-out model is instantiated,
        Then its ``_initial_state`` still holds the untracked fields (``name``)
        exactly as stock django-lifecycle would — opting out restores the full
        snapshot.
        """
        from lex.core.models.LexModel import LexModel

        self.assertTrue(
            LexModel.lex_lean_initial_state,
            "Lean snapshot must be the framework default (opt-out, not opt-in)",
        )

        item = FullConditionalItem(name="ctl", status="draft", amount=1)
        keys = self._snapshot_keys(item)
        self.assertIn(
            "name", keys,
            "An explicit opt-out (lex_lean_initial_state=False) must keep the "
            "full snapshot, including untracked fields like 'name'",
        )
        self.assertTrue(
            self._TRACKED.issubset(keys),
            "Flag-off model must also retain every change-detected field",
        )

    # -- 3.12 ----------------------------------------------------------
    def test_3_12_lean_snapshot_keeps_only_tracked_fields(self) -> None:
        """
        Scenario 3.12: the lean snapshot keeps exactly the tracked fields.

        Given a model with ``lex_lean_initial_state = True``,
        When it is instantiated,
        Then its ``_initial_state`` keys are a subset of the tracked set and
        contain every field named in a hook clause plus ``edited_at``.
        """
        item = LeanConditionalItem(name="x", status="draft", amount=1, a=1, b=2)
        keys = self._snapshot_keys(item)
        self.assertEqual(
            keys, self._TRACKED,
            "Lean snapshot must hold exactly the change-detected fields "
            f"({self._TRACKED}), got {keys}",
        )

    # -- 3.13 ----------------------------------------------------------
    def test_3_13_lean_snapshot_drops_untracked_fields(self) -> None:
        """
        Scenario 3.13: untracked fields are excluded from the lean snapshot.

        Given a lean model with fields no hook consults (``name``, ``note``),
        When it is instantiated,
        Then those fields are absent from ``_initial_state`` — that absence is
        the memory saving.
        """
        item = LeanConditionalItem(name="x", status="draft", note="n")
        keys = self._snapshot_keys(item)
        for field in self._UNTRACKED:
            self.assertNotIn(
                field, keys,
                f"Untracked field {field!r} must not be pinned in the lean "
                "snapshot",
            )

    # -- 3.14 ----------------------------------------------------------
    def test_3_14_edited_at_autostamped_on_lean_update(self) -> None:
        """
        Scenario 3.14: ``edited_at`` is still auto-stamped on a lean update.

        Given a saved lean record with no explicit ``edited_at``,
        When a field is changed and saved,
        Then ``update_edited_at`` stamps a fresh ``edited_at`` — proving the
        framework's own ``has_changed('edited_at')`` dependency survives.
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        self.assertIsNone(item.edited_at, "Fresh create leaves edited_at unset")

        item.status = "paid"
        item.save()
        item.refresh_from_db()
        self.assertIsNotNone(
            item.edited_at,
            "Lean update must still auto-stamp edited_at via the framework hook",
        )

    # -- 3.15 ----------------------------------------------------------
    def test_3_15_explicit_edited_at_override_respected_on_lean(self) -> None:
        """
        Scenario 3.15: an explicit ``edited_at`` is respected on a lean update.

        Given a saved lean record,
        When the caller sets ``edited_at`` explicitly and saves,
        Then the framework does NOT overwrite it — this is the exact behaviour
        ``has_changed('edited_at')`` drives, and ``edited_at`` is always tracked
        in lean mode so it keeps working.
        """
        from django.utils import timezone

        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        explicit = timezone.now() - timezone.timedelta(days=30)
        item.status = "paid"
        item.edited_at = explicit
        item.save()
        item.refresh_from_db()
        self.assertEqual(
            item.edited_at, explicit,
            "Explicit edited_at must be preserved (not auto-overwritten) under "
            "the lean snapshot",
        )

    # -- 3.16 ----------------------------------------------------------
    def test_3_16_legacy_when_fires_on_change(self) -> None:
        """
        Scenario 3.16: legacy ``when='status'`` fires when status changes (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        item.save()
        self.assertIn(
            "legacy_when_status", LeanConditionalItem.hook_log,
            "Legacy when='status' hook must fire on a status change under lean",
        )

    # -- 3.17 ----------------------------------------------------------
    def test_3_17_legacy_when_silent_without_change(self) -> None:
        """
        Scenario 3.17: legacy ``when='status'`` stays silent when status is
        unchanged (lean) — change-detection must not produce false positives.
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.amount = 99  # change a different tracked field
        item.save()
        self.assertNotIn(
            "legacy_when_status", LeanConditionalItem.hook_log,
            "Legacy when='status' hook must NOT fire when status is unchanged",
        )

    # -- 3.18 ----------------------------------------------------------
    def test_3_18_legacy_when_any_fires(self) -> None:
        """
        Scenario 3.18: legacy ``when_any=['a','b']`` fires when ``b`` changes
        (lean) — both members of the group are tracked.
        """
        item = self._create_reset(LeanConditionalItem, name="x", a=0, b=0)
        item.b = 5
        item.save()
        self.assertIn(
            "legacy_when_any_ab", LeanConditionalItem.hook_log,
            "Legacy when_any=['a','b'] hook must fire when 'b' changes",
        )

    # -- 3.19 ----------------------------------------------------------
    def test_3_19_condition_has_changed_fires(self) -> None:
        """
        Scenario 3.19: ``condition=WhenFieldHasChanged('amount')`` fires on a
        lean amount change.
        """
        item = self._create_reset(LeanConditionalItem, name="x", amount=1)
        item.amount = 2
        item.save()
        self.assertIn(
            "cond_amount_changed", LeanConditionalItem.hook_log,
            "WhenFieldHasChanged('amount') must fire on a lean amount change",
        )

    # -- 3.20 ----------------------------------------------------------
    def test_3_20_condition_value_was_fires(self) -> None:
        """
        Scenario 3.20: ``condition=WhenFieldValueWas('status','draft')`` fires
        when the pre-save status was ``draft`` (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        item.save()
        self.assertIn(
            "cond_status_was_draft", LeanConditionalItem.hook_log,
            "WhenFieldValueWas('status','draft') must fire when the prior "
            "status was draft",
        )

    # -- 3.21 ----------------------------------------------------------
    def test_3_21_condition_changes_to_fires(self) -> None:
        """
        Scenario 3.21: ``condition=WhenFieldValueChangesTo('status','paid')``
        fires when status transitions to ``paid`` (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        item.save()
        self.assertIn(
            "cond_status_changes_to_paid", LeanConditionalItem.hook_log,
            "WhenFieldValueChangesTo('status','paid') must fire on the "
            "draft->paid transition",
        )

    # -- 3.22 ----------------------------------------------------------
    def test_3_22_chained_condition_fires_when_both_true(self) -> None:
        """
        Scenario 3.22: a chained ``WhenFieldHasChanged('amount') &
        WhenFieldValueIs('status','paid')`` fires only when BOTH limbs hold —
        here amount changed and status is now paid (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft", amount=1)
        item.status = "paid"
        item.amount = 2
        item.save()
        self.assertIn(
            "cond_amount_changed_and_paid", LeanConditionalItem.hook_log,
            "Chained condition must fire when amount changed AND status is paid",
        )

    # -- 3.23 ----------------------------------------------------------
    def test_3_23_chained_condition_silent_when_one_false(self) -> None:
        """
        Scenario 3.23: the chained condition stays silent when only one limb
        holds — amount changed but status is still ``draft`` (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft", amount=1)
        item.amount = 2  # amount changed, but status stays draft
        item.save()
        self.assertNotIn(
            "cond_amount_changed_and_paid", LeanConditionalItem.hook_log,
            "Chained condition must NOT fire when status is not paid, even "
            "though amount changed",
        )

    # -- 3.24 ----------------------------------------------------------
    def test_3_24_lean_full_parity_identical_mutation(self) -> None:
        """
        Scenario 3.24: lean and full models fire the *same* hooks for the same
        mutation — the core "optimisation does not change behaviour" proof.

        Given identical lean and full records,
        When each undergoes the same status+amount mutation,
        Then their ``hook_log`` sets are identical.
        """
        lean = self._create_reset(LeanConditionalItem, name="x", status="draft", amount=1)
        full = self._create_reset(FullConditionalItem, name="x", status="draft", amount=1)

        for item in (lean, full):
            item.status = "paid"
            item.amount = 2
            item.save()

        self.assertEqual(
            set(LeanConditionalItem.hook_log),
            set(FullConditionalItem.hook_log),
            "Lean and full snapshots must fire an identical set of hooks for "
            "an identical mutation",
        )
        # And it must be the full expected set — not "both empty".
        self.assertEqual(
            set(LeanConditionalItem.hook_log),
            {
                "legacy_when_status",
                "cond_amount_changed",
                "cond_status_was_draft",
                "cond_status_changes_to_paid",
                "cond_amount_changed_and_paid",
            },
            "Every status/amount-conditioned hook must fire for this mutation",
        )

    # -- 3.25 ----------------------------------------------------------
    def test_3_25_has_changed_true_for_tracked_field(self) -> None:
        """
        Scenario 3.25: ``has_changed`` is True for a changed tracked field (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        self.assertTrue(
            item.has_changed("status"),
            "has_changed must report True for a changed tracked field",
        )

    # -- 3.26 ----------------------------------------------------------
    def test_3_26_has_changed_false_for_untracked_field(self) -> None:
        """
        Scenario 3.26: ``has_changed`` reports False for an untracked field even
        when it changed (lean) — the documented trade-off of opting in.

        A model must therefore only opt in when it does not consult
        ``has_changed`` on an undeclared field; declared hooks are always safe.
        """
        item = self._create_reset(LeanConditionalItem, name="orig", status="draft")
        item.name = "changed"
        self.assertFalse(
            item.has_changed("name"),
            "Untracked field reports unchanged under lean — the opt-in "
            "trade-off; declare it in lex_initial_state_extra_fields if needed",
        )

    # -- 3.27 ----------------------------------------------------------
    def test_3_27_initial_value_for_tracked_field(self) -> None:
        """
        Scenario 3.27: ``initial_value`` returns the pre-change value for a
        tracked field (lean).
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        self.assertEqual(
            item.initial_value("status"), "draft",
            "initial_value must return the snapshot value for a tracked field",
        )

    # -- 3.28 ----------------------------------------------------------
    def test_3_28_extra_fields_escape_hatch(self) -> None:
        """
        Scenario 3.28: ``lex_initial_state_extra_fields`` keeps an imperatively
        queried field tracked.

        Given a lean model that calls ``self.has_changed('note')`` in a hook
        body and declares ``note`` in ``lex_initial_state_extra_fields``,
        When ``note`` changes and the row is saved,
        Then the imperative check sees the change and logs it.
        """
        item = self._create_reset(LeanExtraFieldItem, name="x", note="a")
        # 'note' is in the lean snapshot only because of the escape hatch.
        self.assertIn(
            "note", self._snapshot_keys(item),
            "Extra-field escape hatch must keep 'note' in the lean snapshot",
        )
        item.note = "b"
        item.save()
        self.assertIn(
            "note_changed", LeanExtraFieldItem.hook_log,
            "Imperative has_changed('note') must work via the escape hatch",
        )

    # -- 3.29 ----------------------------------------------------------
    def test_3_29_post_save_rebaseline_is_lean(self) -> None:
        """
        Scenario 3.29: the post-save re-baseline re-narrows the snapshot.

        Given a lean record updated once (status draft->paid),
        When a second, unrelated update changes only ``amount``,
        Then the status hooks do NOT fire again — the on-commit re-baseline
        replaced the stale 'draft' snapshot with the new 'paid' one rather than
        leaving change-detection anchored to the original create.
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft", amount=1)
        item.status = "paid"
        item.save()  # first update; on-commit re-baselines to status='paid'
        LeanConditionalItem.hook_log = []

        item.amount = 2  # only amount changes this time
        item.save()
        log = LeanConditionalItem.hook_log
        self.assertNotIn(
            "legacy_when_status", log,
            "After re-baseline, an amount-only update must not re-fire the "
            "status hook",
        )
        self.assertNotIn(
            "cond_status_changes_to_paid", log,
            "After re-baseline, status changes_to_paid must not re-fire when "
            "status is unchanged",
        )
        self.assertIn(
            "cond_amount_changed", log,
            "The amount hook must still fire on the second update",
        )

    # -- 3.30 ----------------------------------------------------------
    def test_3_30_refresh_from_db_rebaselines_lean(self) -> None:
        """
        Scenario 3.30: ``refresh_from_db`` re-baselines the lean snapshot.

        Given a saved lean record mutated in memory,
        When ``refresh_from_db`` reloads it,
        Then the lean snapshot is rebuilt from the DB row and ``has_changed``
        reports False again.
        """
        item = self._create_reset(LeanConditionalItem, name="x", status="draft")
        item.status = "paid"
        self.assertTrue(item.has_changed("status"), "Precondition: in-memory change seen")

        item.refresh_from_db()
        self.assertEqual(item.status, "draft", "refresh_from_db restores the DB value")
        self.assertFalse(
            item.has_changed("status"),
            "refresh_from_db must rebuild the lean snapshot so has_changed resets",
        )
        # And the rebuilt snapshot is still lean, not full.
        self.assertEqual(
            self._snapshot_keys(item), self._TRACKED,
            "refresh_from_db must rebuild a *lean* snapshot, not a full one",
        )

    # -- 3.31 ----------------------------------------------------------
    def test_3_31_create_path_hooks_unaffected(self) -> None:
        """
        Scenario 3.31: create-path stamping hooks are unaffected by lean mode.

        Given a lean model created fresh,
        Then ``created_at`` and ``created_by`` are stamped — these BEFORE_CREATE
        hooks carry no change-detection, so narrowing the snapshot cannot affect
        them.
        """
        item = LeanConditionalItem.objects.create(name="x", status="draft")
        item.refresh_from_db()
        self.assertIsNotNone(item.created_at, "Lean create must still stamp created_at")
        self.assertIsNotNone(item.created_by, "Lean create must still stamp created_by")

    # -- 3.32 ----------------------------------------------------------
    def test_3_32_lean_snapshot_is_smaller(self) -> None:
        """
        Scenario 3.32: the lean snapshot is strictly smaller than the full one —
        the entire purpose of the opt-in.

        Given a lean and a full model built from identical kwargs,
        Then the lean ``_initial_state`` holds strictly fewer keys, and every
        key it drops is one no hook consults.
        """
        kwargs = dict(name="x", status="draft", amount=1, a=1, b=2, note="n")
        lean = LeanConditionalItem(**kwargs)
        full = FullConditionalItem(**kwargs)

        lean_keys = self._snapshot_keys(lean)
        full_keys = self._snapshot_keys(full)
        self.assertLess(
            len(lean_keys), len(full_keys),
            "Lean snapshot must retain fewer keys than the full snapshot",
        )
        dropped = full_keys - lean_keys
        self.assertTrue(
            self._UNTRACKED.issubset(dropped),
            "Dropped keys must include the untracked fields no hook consults",
        )
        self.assertTrue(
            self._TRACKED.issubset(lean_keys),
            "Lean snapshot must still retain every change-detected field",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
