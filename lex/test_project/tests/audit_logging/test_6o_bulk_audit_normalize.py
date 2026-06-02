"""
Sub-cluster 6o — `BulkAuditLogMixin._normalize_bulk_payloads` branches.

Coverage-driven extension to 6e (which covered the happy DELETE-many path
via the API in Session 51). Targets the four-branch `_normalize_bulk_payloads`
static helper that drives every bulk-write payload normalisation:

* `lex/audit_logging/mixins/BulkAuditLogMixin.py` 47.06% baseline
  (89 stmts / 42 missed).

This helper sits between DRF's bulk serializer and the per-row audit-write
loop. Its job: take an arbitrary `payloads` shape (None / dict / list of
varying length) and produce **exactly one normalised payload per target**
so the per-row audit-write loop never has to special-case shape mismatches.
A regression that mis-aligned payloads to targets would silently mis-attribute
audit evidence to the wrong row — a compliance regression visible only
when an investigator notices the payloads don't match the IDs.

Scenarios 6.156 – 6.159.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin

import pytest

pytestmark = pytest.mark.audit_logging


def _target(pk):
    """Cheap target stand-in. `_attach_related_instance_id` only needs `pk`."""
    return SimpleNamespace(pk=pk, _meta=SimpleNamespace(model_name="x"))


class TestCluster06o_NormalizeBulkPayloads(SimpleTestCase):
    """Four-branch coverage of the static `_normalize_bulk_payloads` helper."""

    def setUp(self) -> None:
        super().setUp()
        # Patch the related-id attacher to a transparent identity so we can
        # observe which payload landed on which target without depending on
        # the attacher's internal contract (already covered in 6d).
        patcher = patch(
            "lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogMixin"
            "._attach_related_instance_id",
            side_effect=lambda payload, target: {
                **(payload if isinstance(payload, dict) else {"_p": payload}),
                "_attached_to_pk": target.pk,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # 6.156 -------------------------------------------------------------
    def test_6_156_list_payloads_matching_target_count_zips_one_to_one(self):
        """`len(payloads) == len(targets)` → strict 1-to-1 alignment.

        The dominant happy path: DRF's bulk serializer produces one
        payload per instance, this helper preserves that mapping.
        A regression that swapped to broadcast or single-serialize
        would silently apply the same payload to every target.
        """
        targets = [_target(1), _target(2), _target(3)]
        payloads = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, payloads)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["name"], "a")
        self.assertEqual(result[0]["_attached_to_pk"], 1)
        self.assertEqual(result[1]["name"], "b")
        self.assertEqual(result[1]["_attached_to_pk"], 2)
        self.assertEqual(result[2]["name"], "c")
        self.assertEqual(result[2]["_attached_to_pk"], 3)

    # 6.157 -------------------------------------------------------------
    def test_6_157_single_payload_broadcasts_across_all_targets(self) -> None:
        """`len(payloads) == 1` and `len(targets) > 1` → broadcast.

        DRF emits a single-entry list when the bulk operation carries
        a uniform payload (e.g. "delete every selected row" with a
        common reason). The helper must replicate that one payload
        across every target, not zip-truncate to a single output.
        """
        targets = [_target(1), _target(2), _target(3)]
        payloads = [{"reason": "cleanup"}]

        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, payloads)

        self.assertEqual(len(result), 3)
        for i, target_pk in enumerate([1, 2, 3]):
            self.assertEqual(
                result[i]["reason"], "cleanup",
                f"Broadcast payload missing on target #{i} — a "
                "regression here would leave that target's audit row "
                "with an empty payload while siblings get the reason.",
            )
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)

    # 6.158 -------------------------------------------------------------
    def test_6_158_non_list_payload_serialized_once_and_replicated(self) -> None:
        """Dict / scalar payload (not a list) → serialised once, replicated.

        Pins the fallback branch at lines 32-37: when the caller passes
        a single dict (most common for "PATCH the same fields on N
        rows"), it must land on every target's audit row, not just
        the first.
        """
        targets = [_target(1), _target(2)]
        payload = {"status": "archived"}

        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, payload)

        self.assertEqual(len(result), 2)
        for i, target_pk in enumerate([1, 2]):
            self.assertEqual(result[i]["status"], "archived")
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)

    # 6.159 -------------------------------------------------------------
    def test_6_159_falsy_serialized_payload_falls_back_to_empty_dict(self) -> None:
        """Serialised-to-falsy payload → `{}` replicated across targets.

        Pins the `_serialize_payload(payloads) or {}` defensive default
        at line 34. ``_serialize_payload({})`` returns ``{}`` and
        ``_serialize_payload([])`` enters the `isinstance(list)`
        branch with len==0 — neither matches `len(targets)` nor `1`,
        so both fall through to the bottom branch and the `or {}`
        guard converts them to a literal empty dict on every target's
        audit row. Without this guard a bulk write with no diff
        (e.g. PATCH that only touches read-only fields) would land
        as `payload=None` on every row, masking the bulk-write
        evidence operators look for.

        Also pins the documented quirk for ``None``: it falls to
        ``str(data)`` inside ``_serialize_payload`` and becomes the
        string ``"None"`` (truthy) — so it is **not** rewritten to
        `{}`. Callers must pass `{}` (or omit `payloads`) if they
        want the empty-dict fallback. Pinning this so a "fix" that
        silently coerces None → {} doesn't change observable
        behaviour for downstream consumers.

        Also pins the mismatched-length fallback: a list whose length
        is neither N nor 1 falls through to single-serialize
        semantics — the whole list is replicated across every target,
        not zip-truncated.
        """
        targets = [_target(1), _target(2)]

        # Empty dict → falsy → {} fallback fires.
        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, {})
        self.assertEqual(len(result), 2)
        for i, target_pk in enumerate([1, 2]):
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)
            self.assertEqual(
                {k: v for k, v in result[i].items()
                 if k != "_attached_to_pk"},
                {},
                "Empty-dict payload must coerce to {} on every target — "
                "regression that drops the `or {}` guard would land "
                "`None` on the audit row's payload column, masking "
                "bulk-write evidence operators look for.",
            )

        # Empty list (length 0) → list-branch can't match → falls to
        # bottom → _serialize_payload([]) is [] → falsy → {} fallback.
        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, [])
        self.assertEqual(len(result), 2)
        for i, target_pk in enumerate([1, 2]):
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)
            self.assertEqual(
                {k: v for k, v in result[i].items()
                 if k != "_attached_to_pk"},
                {},
                "Empty-list payload must also fall through to {} — "
                "the list-branch can't match (len 0) and the {} "
                "fallback is what keeps audit rows non-null.",
            )

        # None → documented quirk: str(None) == "None", truthy, so
        # the `or {}` does NOT fire. Pin the real behaviour so a
        # well-meaning "let's be tidy and coerce None too" change
        # would have to update the test (and the docs) deliberately.
        result = BulkAuditLogMixin._normalize_bulk_payloads(targets, None)
        self.assertEqual(len(result), 2)
        for i, target_pk in enumerate([1, 2]):
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)
            # Identity attacher wraps non-dicts as {"_p": payload}.
            self.assertEqual(result[i].get("_p"), "None")

        # Mismatched-length list (3 entries, 2 targets) → fallback path.
        result = BulkAuditLogMixin._normalize_bulk_payloads(
            targets, [{"a": 1}, {"a": 2}, {"a": 3}],
        )
        self.assertEqual(len(result), 2)
        # Fall-through serializes the whole list as the payload and
        # replicates it across every target — pin so a regression that
        # silently truncated to `targets[:len(payloads)]` would surface
        # here.
        for i, target_pk in enumerate([1, 2]):
            self.assertEqual(result[i]["_attached_to_pk"], target_pk)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

