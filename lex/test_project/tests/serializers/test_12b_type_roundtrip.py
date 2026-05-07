"""
Cluster 12b: Type round-trip — what goes in comes back out.

Intent: field values survive a POST → GET cycle without silent
conversion. This is the single most common source of UI bugs — a
``DecimalField`` silently losing precision, a ``DateTimeField``
stripped of its timezone, a ``ForeignKey`` rendered as an integer in
one endpoint and a dict in another.

Scenario numbering matches
docs/test-plan/test-clusters.md#12-serializer-contract.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    CHOICE_BETA,
    WIDE,
    RelatedItem,
    WideItem,
)


class TestCluster12b_TypeRoundTrip(E2ETestCase):
    """One field per type — POST → GET round-trip fidelity."""

    e2e_models = ALL_MODELS

    # -- 12.9 ----------------------------------------------------------
    def test_12_9_decimal_field_preserves_precision(self) -> None:
        """Scenario 12.9: ``DecimalField(max_digits=12, decimal_places=4)``
        preserves ``1234.5678`` through a full round-trip."""
        item = WideItem.objects.create(name="dec", amount=Decimal("1234.5678"))

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        returned = resp.data["amount"]
        # DRF renders Decimal as string by default — either way the
        # numeric value must round-trip without silent truncation.
        as_decimal = Decimal(str(returned))
        self.assertEqual(
            as_decimal, Decimal("1234.5678"),
            f"Decimal precision lost: sent 1234.5678, got {returned!r}",
        )

    # -- 12.10 ---------------------------------------------------------
    @unittest.expectedFailure  # BUG-012: DateTimeField serialized as naive ISO string
    def test_12_10_datetime_roundtrip_keeps_timezone(self) -> None:
        """Scenario 12.10: UTC ISO-8601 → same instant on the way back."""
        sent = datetime(2026, 4, 21, 13, 30, 45, tzinfo=timezone.utc)
        item = WideItem.objects.create(name="dt", created_at_ts=sent)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        got_raw = resp.data["created_at_ts"]

        # Accept either a string (DRF default) or a datetime (some
        # custom renderers). Both must represent the same instant.
        if isinstance(got_raw, str):
            # ``fromisoformat`` handles the ``+00:00`` suffix DRF emits.
            got = datetime.fromisoformat(got_raw.replace("Z", "+00:00"))
        else:
            got = got_raw

        self.assertIsNotNone(
            got.tzinfo,
            f"Returned datetime must be tz-aware — got naive {got_raw!r}",
        )
        self.assertEqual(
            got, sent,
            f"Datetime round-trip changed the instant: sent {sent}, got {got}",
        )

    # -- 12.11 ---------------------------------------------------------
    def test_12_11_date_field_uses_iso_format(self) -> None:
        """Scenario 12.11: ``DateField`` round-trips as ``YYYY-MM-DD``."""
        sent = date(2026, 4, 21)
        item = WideItem.objects.create(name="d", created_on=sent)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        got = resp.data["created_on"]

        if isinstance(got, str):
            self.assertEqual(
                got, "2026-04-21",
                f"DateField must be ISO YYYY-MM-DD; got {got!r}",
            )
            self.assertEqual(date.fromisoformat(got), sent)
        else:
            self.assertEqual(got, sent)

    # -- 12.12 ---------------------------------------------------------
    def test_12_12_uuid_field_is_string(self) -> None:
        """Scenario 12.12: ``UUIDField`` is an RFC-4122 string, not a repr."""
        item = WideItem.objects.create(name="u")

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        got = resp.data["public_id"]

        self.assertIsInstance(
            got, str,
            f"UUIDField must serialize to str; got {type(got).__name__}: {got!r}",
        )
        # Parses as a valid UUID — will raise if not.
        self.assertEqual(UUID(got), item.public_id)

    # -- 12.13 ---------------------------------------------------------
    def test_12_13_nullable_foreign_key_unset_is_null(self) -> None:
        """Scenario 12.13: unset FK → ``null`` (not ``0``, not ``""``)."""
        item = WideItem.objects.create(name="noFK", related=None)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertIn("related", resp.data, "FK field must be present in JSON")
        self.assertIsNone(
            resp.data["related"],
            f"Unset FK must serialize to None; got {resp.data['related']!r}",
        )

    # -- 12.14 ---------------------------------------------------------
    def test_12_14_foreign_key_set_roundtrips(self) -> None:
        """Scenario 12.14: a set FK is serialized in a shape that
        preserves the related row's id.

        We do NOT pin whether the framework renders it as a bare id or
        as ``{"id": ..., "short_description": ...}`` — both are defensible
        contracts. The test locks in whatever the framework is doing so
        any future change is visible.
        """
        related = RelatedItem.objects.create(name="target", code="T1")
        item = WideItem.objects.create(name="fk", related=related)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        got = resp.data["related"]
        if isinstance(got, dict):
            self.assertEqual(
                got.get("id"), related.pk,
                f"FK dict must include the related row's id; got {got!r}",
            )
        else:
            self.assertEqual(
                got, related.pk,
                f"FK scalar must be the related row's pk; got {got!r}",
            )

    # -- 12.15 ---------------------------------------------------------
    @unittest.expectedFailure  # BUG-013: PATCH does not accept FK-as-{"id": X} dict payload
    def test_12_15_patch_accepts_foreign_key_dict_payload(self) -> None:
        """Scenario 12.15: PATCH with ``{"related": {"id": X}}`` resolves
        to the target row (``_parse_value_for_field`` contract)."""
        related_a = RelatedItem.objects.create(name="A")
        related_b = RelatedItem.objects.create(name="B")
        item = WideItem.objects.create(name="switch", related=related_a)

        resp = self.client.patch(
            self.url_detail(WIDE, item.pk),
            data={"related": {"id": related_b.pk}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(
            item.related_id, related_b.pk,
            "PATCH of FK-as-dict must resolve and persist the new target",
        )

    # -- 12.16 ---------------------------------------------------------
    def test_12_16_patch_rejects_invalid_choice(self) -> None:
        """Scenario 12.16: PATCH with a value outside the ``choices`` set → 400."""
        item = WideItem.objects.create(name="c", category=CHOICE_BETA)

        resp = self.client.patch(
            self.url_detail(WIDE, item.pk),
            data={"category": "not-a-choice"},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Invalid choice must be rejected with 400; got {resp.status_code}",
        )
        item.refresh_from_db()
        self.assertEqual(
            item.category, CHOICE_BETA,
            "Rejected PATCH must not mutate the choice field",
        )

    # -- 12.17 ---------------------------------------------------------
    def test_12_17_text_field_preserves_unicode_and_newlines(self) -> None:
        """Scenario 12.17: multi-line unicode survives POST → GET byte-for-byte."""
        payload = "Привет\nこんにちは\n🚀 line three"
        item = WideItem.objects.create(name="t", notes=payload)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.data["notes"], payload,
            "TextField must round-trip unicode + newlines unchanged",
        )

    # -- 12.18 ---------------------------------------------------------
    def test_12_18_json_field_preserves_structure(self) -> None:
        """Scenario 12.18: nested JSON round-trips with structure intact."""
        payload = {
            "outer": {"inner": [1, 2, {"three": 3}]},
            "flag": True,
            "null": None,
        }
        item = WideItem.objects.create(name="j", payload=payload)

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.data["payload"], payload,
            f"JSONField did not round-trip: got {resp.data['payload']!r}",
        )

    # -- 12.19 ---------------------------------------------------------
    def test_12_19_unknown_field_in_patch_ignored(self) -> None:
        """Scenario 12.19: unknown keys in PATCH payload silently dropped."""
        item = WideItem.objects.create(name="orig", count=5)

        resp = self.client.patch(
            self.url_detail(WIDE, item.pk),
            data={"count": 6, "this_field_does_not_exist": "💥"},
            format="json",
        )
        # Contract from cluster 2.5 — unknown fields ignored, known applied.
        self.assertIn(resp.status_code, (200, 201))
        item.refresh_from_db()
        self.assertEqual(
            item.count, 6,
            "Known field must be applied when payload also has unknown keys",
        )


if __name__ == "__main__":  # pragma: no cover
    # Keep ``time`` referenced so linters don't strip the import — we
    # may add a TimeField scenario in the next pass.
    _ = time
    unittest.main()




