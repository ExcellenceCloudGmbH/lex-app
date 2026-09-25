"""
Tests for ``parse_as_of_datetime`` — the ``?as_of=`` anchor parser.

Verifies:
    • An offset-aware anchor is normalized to UTC unchanged (the form clients
      should send: it carries its own zone, so the server never guesses)
    • A NAIVE anchor is interpreted in the configured timezone, with the offset
      in force on that value's own date (DST-correct, not a fixed shift)
    • A naive anchor logs a warning — the signal that a client still needs fixing
    • An aware anchor logs nothing
    • Blank, None and unparseable input yield None

Background: the naive branch previously assumed UTC, which made ``as_of`` the
only datetime input in the API not following DRF's convention
(``DateTimeField.enforce_timezone`` uses ``get_current_timezone()``). A Berlin
client sending local wall-clock was evaluated 1-2h in the future, so stepping
back less than that offset never reached the pre-edit state (customer report
2026-07-14).
"""

from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase, override_settings

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

    def test_naive_anchor_uses_the_configured_zone_in_summer(self):
        # CEST (+02): a client's 14:30 wall clock is 12:30Z.
        self.assertEqual(
            parse_as_of_datetime("2026-07-15T14:30:00"),
            datetime(2026, 7, 15, 12, 30, tzinfo=dt_timezone.utc),
        )

    def test_naive_anchor_uses_the_configured_zone_in_winter(self):
        # CET (+01): the same wall clock is 13:30Z. The offset comes from the
        # value's own date, so this is not a fixed shift.
        self.assertEqual(
            parse_as_of_datetime("2026-01-15T14:30:00"),
            datetime(2026, 1, 15, 13, 30, tzinfo=dt_timezone.utc),
        )

    def test_naive_anchor_is_no_longer_read_as_utc(self):
        # The regression this parser caused: reading the digits as UTC placed the
        # anchor 2h after the moment the user meant.
        self.assertNotEqual(
            parse_as_of_datetime("2026-07-15T14:30:00"),
            datetime(2026, 7, 15, 14, 30, tzinfo=dt_timezone.utc),
        )

    @override_settings(USE_TZ=True, TIME_ZONE="UTC")
    def test_naive_anchor_under_utc_configuration_is_unchanged(self):
        # The old hardcoded behaviour is just the TIME_ZONE="UTC" case of the
        # new rule — no special-casing needed.
        self.assertEqual(
            parse_as_of_datetime("2026-07-15T14:30:00"),
            datetime(2026, 7, 15, 14, 30, tzinfo=dt_timezone.utc),
        )

    def test_naive_anchor_logs_a_warning(self):
        with self.assertLogs("lex.api.utils.temporal", level="WARNING") as captured:
            parse_as_of_datetime("2026-07-15T14:30:00")
        self.assertTrue(
            any("naive datetime" in line for line in captured.output),
            f"expected a naive-anchor warning, got {captured.output}",
        )

    def test_aware_anchor_logs_nothing(self):
        with self.assertNoLogs("lex.api.utils.temporal", level="WARNING"):
            parse_as_of_datetime("2026-07-15T12:30:00Z")

    def test_blank_and_unparseable_input_yields_none(self):
        for raw in (None, "", "   ", "not-a-date"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_as_of_datetime(raw))

    @override_settings(USE_TZ=False, TIME_ZONE="Europe/Berlin")
    def test_use_tz_disabled_returns_naive_utc_for_orm_compatibility(self):
        parsed = parse_as_of_datetime("2026-07-15T14:30:00+02:00")
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, datetime(2026, 7, 15, 12, 30))

    def test_dst_offset_comes_from_the_values_own_date(self):
        # Guards against anyone replacing the zone lookup with a fixed offset:
        # the same wall clock resolves to different instants in CEST and CET.
        summer = parse_as_of_datetime("2026-07-15T14:30:00")
        winter = parse_as_of_datetime("2026-01-15T14:30:00")
        self.assertEqual(summer.hour, 12, "14:30 CEST is 12:30Z")
        self.assertEqual(winter.hour, 13, "14:30 CET is 13:30Z")
        self.assertNotEqual(
            summer.hour, winter.hour,
            "a fixed offset would resolve both wall clocks to the same hour",
        )
