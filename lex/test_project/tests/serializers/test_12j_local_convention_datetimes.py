"""Cluster 12j — datetime rendering & as_of parsing under a local naive convention.

Intent: under ``USE_TZ=False`` the meaning of every stored naive datetime is
``TIME_ZONE`` (the PostgreSQL connection timezone). v2.0.0rc212 (f622c9c,
#635) switched that convention from Europe/Berlin to UTC to satisfy
django_celery_beat's naive==UTC assumption — silently reinterpreting all
pre-existing data (customer ticket 2026-07-16: ``output_date`` values shifted
by 1h, equality filters broken). With beat being retired, instances can
restore the original convention via ``LEX_TIME_ZONE=Europe/Berlin``. That is
only sound if the whole chain honors the convention:

- serialized datetimes must carry the *real, DST-aware* offset (a static 'Z'
  would mislabel Berlin wall-clock as UTC and re-create the display shift);
- ``as_of`` query values must normalize to the *same* naive convention the
  ORM compares against, or every time-travel query shifts by the UTC offset.

Cluster 12j — scenarios 12.46–12.48. Type: U.
Covers: lex/api/serializers/base_serializers.py (LexAwareDateTimeField),
        lex/audit_logging/serializers/CalculationLogSerializer.py (mapping),
        lex/api/utils/temporal.py (parse_as_of_datetime convention),
        lex/lex_app/settings.py (LEX_TIME_ZONE override, conditional 'Z').
Run: python -m lex pytest lex/test_project/tests/serializers/test_12j_local_convention_datetimes.py -v
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.test import SimpleTestCase, override_settings

from lex.api.serializers.base_serializers import LexAwareDateTimeField, LexSerializer
from lex.api.utils.temporal import parse_as_of_datetime

pytestmark = pytest.mark.serializers


class TestCluster12j_LocalConventionDatetimes(SimpleTestCase):
    """Cluster 12j: the naive convention is honored end to end."""

    def test_12_46_naive_values_get_the_dst_aware_offset(self):
        """
        Scenario 12.46: under a Berlin naive convention, serialized datetimes
        carry the real offset — +01:00 in winter, +02:00 in summer.
        Given: USE_TZ=False and TIME_ZONE=Europe/Berlin (LEX_TIME_ZONE mode)
        When: LexAwareDateTimeField renders naive December and June values
        Then: the ISO strings end in +01:00 / +02:00 respectively — the
              instant a browser reconstructs equals the wall time the author
              wrote (a static 'Z' would shift it by the offset)
        """
        field = LexAwareDateTimeField()
        with override_settings(USE_TZ=False, TIME_ZONE="Europe/Berlin"):
            winter = field.to_representation(datetime(2026, 12, 31, 0, 0, 0))
            summer = field.to_representation(datetime(2026, 6, 30, 12, 0, 0))

        self.assertEqual(
            winter, "2026-12-31T00:00:00+01:00",
            "December Berlin wall-clock must carry the winter (+01:00) offset.",
        )
        self.assertEqual(
            summer, "2026-06-30T12:00:00+02:00",
            "June Berlin wall-clock must carry the summer (+02:00) offset — "
            "the offset is per-value, never a static suffix.",
        )

    def test_12_47_utc_convention_keeps_the_existing_z_contract(self):
        """
        Scenario 12.47: under the UTC convention (today's default) nothing
        changes — values keep the established 'Z' rendering (12g contract),
        and the field is wired into the framework serializer mappings.
        Given: USE_TZ=False and TIME_ZONE=UTC (the current default target)
        When: LexAwareDateTimeField renders a naive value
        Then: it falls through to the default path (settings' DATETIME_FORMAT
              applies → trailing 'Z'), and both LexSerializer and the
              calculation-log serializer map DateTimeField to this class
        """
        from django.db import models

        from lex.audit_logging.serializers.CalculationLogSerializer import (
            CalculationLogDefaultSerializer,
        )

        field = LexAwareDateTimeField()
        with override_settings(USE_TZ=False, TIME_ZONE="UTC"):
            rendered = field.to_representation(datetime(2026, 12, 31, 0, 0, 0))
        self.assertTrue(
            str(rendered).endswith("Z"),
            f"UTC-convention rendering must keep the 12g 'Z' contract, got {rendered!r}.",
        )

        for serializer_cls in (LexSerializer, CalculationLogDefaultSerializer):
            self.assertIs(
                serializer_cls.serializer_field_mapping[models.DateTimeField],
                LexAwareDateTimeField,
                f"{serializer_cls.__name__} must render datetimes via "
                f"LexAwareDateTimeField or non-UTC instances mislabel values.",
            )

    def test_12_48_as_of_parses_into_the_instance_convention(self):
        """
        Scenario 12.48: an as_of query value is normalized to the SAME naive
        convention the ORM compares against.
        Given: a UTC-instant query value 2026-12-30T23:00:00Z
        When: parsed under the Berlin convention and under the UTC convention
        Then: Berlin yields naive 2026-12-31 00:00 (matches Berlin-wall
              storage); UTC yields naive 2026-12-30 23:00 (today's behavior,
              unchanged) — so time-travel never shifts by the UTC offset
        """
        with override_settings(USE_TZ=False, TIME_ZONE="Europe/Berlin"):
            berlin = parse_as_of_datetime("2026-12-30T23:00:00Z")
        with override_settings(USE_TZ=False, TIME_ZONE="UTC"):
            utc = parse_as_of_datetime("2026-12-30T23:00:00Z")

        self.assertEqual(
            berlin, datetime(2026, 12, 31, 0, 0, 0),
            "Under the Berlin convention the parsed anchor must be Berlin "
            "wall-clock, matching how the rows themselves read.",
        )
        self.assertIsNone(berlin.tzinfo, "USE_TZ=False comparisons need naive values.")
        self.assertEqual(
            utc, datetime(2026, 12, 30, 23, 0, 0),
            "Under the UTC convention behavior is unchanged (5m/12g contract).",
        )
