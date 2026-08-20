"""
Tests for ``parse_as_of_datetime`` — the ``?as_of=`` anchor parser.

Verifies:
    • An offset-aware anchor is normalized to UTC unchanged (the only accepted
      form: it carries its own zone, so the server never guesses)
    • An aware anchor's own offset is honoured, so CEST and CET wall clocks
      resolve to different instants
    • A NAIVE anchor is REJECTED with a 400 (``ValidationError``), regardless of
      the configured timezone, and logs which value was rejected
    • Blank, None and unparseable input yield None (as_of is simply absent)

Background: a naive anchor originally assumed UTC, which put a Berlin client's
wall clock 1-2h in the future — stepping back less than that offset never
reached the pre-edit state and the endpoint returned a plausible snapshot of the
wrong version (customer report 2026-07-14). Every first-party client now sends an
absolute instant, so a naive value is a bug rather than a use case: failing
loudly beats guessing.
"""

from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from lex.api.utils.temporal import parse_as_of_datetime


@override_settings(USE_TZ=True, TIME_ZONE="Europe/Berlin")
class AsOfParsingTests(SimpleTestCase):
    def test_aware_z_anchor_is_normalized_to_utc_unchanged(self):
        self.assertEqual(
            parse_as_of_datetime("2026-07-15T12:30:00Z"),
            datetime(2026, 7, 15, 12, 30, tzinfo=dt_timezone.utc),
        )

    def test_aware_offset_anchor_keeps_its_instant(self):
        # 14:30+02:00 and 12:30Z are the same moment; both must land identically.
        self.assertEqual(
            parse_as_of_datetime("2026-07-15T14:30:00+02:00"),
            parse_as_of_datetime("2026-07-15T12:30:00Z"),
        )

    def test_aware_anchor_honours_its_own_offset_across_dst(self):
        # The same wall clock in CEST and CET denotes different instants, and the
        # offset the client sent is what decides — no server-side zone lookup.
        self.assertEqual(parse_as_of_datetime("2026-07-15T14:30:00+02:00").hour, 12)
        self.assertEqual(parse_as_of_datetime("2026-01-15T14:30:00+01:00").hour, 13)

    def test_naive_anchor_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            parse_as_of_datetime("2026-07-15T14:30:00")
        self.assertIn("as_of", caught.exception.detail)

    @override_settings(USE_TZ=True, TIME_ZONE="UTC")
    def test_naive_anchor_is_rejected_under_utc_configuration_too(self):
        # The rejection is unconditional: it is about the value being ambiguous,
        # not about which zone happens to be configured.
        with self.assertRaises(ValidationError):
            parse_as_of_datetime("2026-07-15T14:30:00")

    def test_rejection_logs_the_offending_value(self):
        with self.assertLogs("lex.api.utils.temporal", level="WARNING") as captured:
            with self.assertRaises(ValidationError):
                parse_as_of_datetime("2026-07-15T14:30:00")
        self.assertTrue(
            any("2026-07-15T14:30:00" in line for line in captured.output),
            f"expected the rejected value in the log, got {captured.output}",
        )

    def test_aware_anchor_logs_nothing(self):
        with self.assertNoLogs("lex.api.utils.temporal", level="WARNING"):
            parse_as_of_datetime("2026-07-15T12:30:00Z")

    def test_blank_and_unparseable_input_yields_none(self):
        # Absent or malformed is not the same as ambiguous: as_of is simply not
        # applied, which is the pre-existing behaviour and stays unchanged.
        for raw in (None, "", "   ", "not-a-date"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_as_of_datetime(raw))

    @override_settings(USE_TZ=False, TIME_ZONE="Europe/Berlin")
    def test_use_tz_disabled_returns_naive_utc_for_orm_compatibility(self):
        parsed = parse_as_of_datetime("2026-07-15T14:30:00+02:00")
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, datetime(2026, 7, 15, 12, 30))
