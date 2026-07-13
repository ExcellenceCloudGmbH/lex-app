"""Cluster 12g — serialized datetimes must be timezone-unambiguous (BUG-025).

Intent: a REST client can only render a timestamp correctly if the serialized
value identifies its timezone. On deployment targets where ``USE_TZ=False``
(``default``, ``GCP`` — see the coupling comment in ``lex_app/settings.py``),
``edited_at`` / ``created_at`` / ``CalculationLog.timestamp`` are stored as
NAIVE UTC and DRF serializes them as bare ISO strings with no ``Z``/offset.
Browsers parse a naive ISO string as LOCAL time, so every timestamp renders
shifted by the viewer's UTC offset — the customer-reported "edited at 13:43
but shows 11:43" (Berlin, UTC+2). Targets with ``USE_TZ=True`` serialize
``…+02:00`` and render correctly, which is why only some instances show it.

These tests assert the CORRECT contract — every serialized datetime carries an
explicit UTC offset — and are ``xfail(strict=True)`` until BUG-025 is fixed
(e.g. render naive-UTC values with an explicit ``Z``, or revisit the
``USE_TZ`` coupling). When the fix lands, the markers drop and these become
live regression gates.

Cluster 12g — scenarios 12.36–12.38. Type: E.
Covers: lex/core/models/LexModel.py (lex_datetime_now / edited_at, created_at),
        lex/audit_logging/serializers/CalculationLogSerializer.py (timestamp),
        lex/lex_app/settings.py (USE_TZ / TIME_ZONE coupling).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12g_datetime_tz_ambiguity.py -v
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from rest_framework import status

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.serializers.CalculationLogSerializer import (
    CalculationLogDefaultSerializer,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, WIDE, WideItem

pytestmark = pytest.mark.serializers

# ISO-8601 timezone designator: trailing 'Z' or an explicit ±HH:MM offset.
_TZ_DESIGNATOR = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


class TestCluster12g_DatetimeTimezoneAmbiguity(E2ETestCase):
    """Cluster 12g: serialized datetimes carry an explicit timezone."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [CalculationLog, AuditLog]

    def assert_tz_designator(self, rendered, field: str) -> None:
        """Assert a serialized datetime string ends in 'Z' or ±HH:MM."""
        self.assertIsInstance(
            rendered, str,
            f"{field} must serialize as an ISO-8601 string, got {type(rendered)}.",
        )
        self.assertRegex(
            rendered, _TZ_DESIGNATOR,
            f"{field}={rendered!r} carries no timezone designator — clients "
            f"parse a naive ISO string as LOCAL time, so on USE_TZ=False "
            f"targets (naive-UTC storage) it renders shifted by the viewer's "
            f"UTC offset (BUG-025).",
        )

    def _saved_item(self) -> WideItem:
        item = WideItem.objects.create(name="tz-probe", amount=Decimal("1"))
        item.name = "tz-probe-edited"
        item.save()
        return item

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-025: naive-UTC edited_at serialized without a timezone designator",
    )
    def test_12_36_edited_at_serializes_with_utc_offset(self) -> None:
        """
        Scenario 12.36: edited_at in an API detail response is unambiguous.
        Given: a saved-and-edited LexModel record
        When: GET the record through the REST detail endpoint
        Then: the serialized edited_at ends in 'Z' or an explicit ±HH:MM offset
        """
        item = self._saved_item()
        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Detail GET failed: {resp.status_code}",
        )
        self.assert_tz_designator(resp.data.get("edited_at"), "edited_at")

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-025: naive-UTC created_at serialized without a timezone designator",
    )
    def test_12_37_created_at_serializes_with_utc_offset(self) -> None:
        """
        Scenario 12.37: created_at in an API detail response is unambiguous.
        Given: a saved LexModel record
        When: GET the record through the REST detail endpoint
        Then: the serialized created_at ends in 'Z' or an explicit ±HH:MM offset
        """
        item = self._saved_item()
        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Detail GET failed: {resp.status_code}",
        )
        self.assert_tz_designator(resp.data.get("created_at"), "created_at")

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-025: naive-UTC CalculationLog.timestamp serialized without a timezone designator",
    )
    def test_12_38_calculation_log_timestamp_serializes_with_utc_offset(self) -> None:
        """
        Scenario 12.38: CalculationLog.timestamp is unambiguous when serialized.
        Given: a persisted CalculationLog row (timestamp = auto_now_add)
        When: rendered through CalculationLogDefaultSerializer (the API's
              representation of calculation-log records)
        Then: the serialized timestamp ends in 'Z' or an explicit ±HH:MM offset
        """
        row = CalculationLog.objects.create(calculationId="tz-probe")
        rendered = CalculationLogDefaultSerializer(row).data["timestamp"]
        self.assert_tz_designator(rendered, "CalculationLog.timestamp")
