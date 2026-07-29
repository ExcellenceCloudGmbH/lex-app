"""Backend ground-truth for the datetime write/read convention.

Scenario: USE_TZ=False, TIME_ZONE=UTC (current default/GCP). The frontend
sends a NAIVE local wall-clock string (no 'Z', no offset — proven in the
frontend test datetimeConventionRoundTrip.test.ts). This pins what the
backend does with it, and contrasts the aware-input path.

Drop in lex/test_project/tests/serializers/ and run:
  python -m lex pytest lex/test_project/tests/serializers/test_backend_datetime_convention.py -v
"""
from __future__ import annotations

from datetime import datetime

import pytest
from django.test import SimpleTestCase, override_settings
from rest_framework import serializers

pytestmark = pytest.mark.serializers


class TestDatetimeConvention(SimpleTestCase):
    """USE_TZ=False + TIME_ZONE=UTC: what the backend stores and serves."""

    @override_settings(USE_TZ=False, TIME_ZONE="UTC")
    def test_naive_input_is_stored_verbatim_no_conversion(self):
        """The frontend's naive '11:00' is NOT converted — stored as-is.

        This is the write half of the round-trip mismatch: the wall-clock the
        user typed becomes the literal stored value (which the DB then anchors
        as 11:00 UTC), with no timezone conversion at the DRF layer.
        """
        field = serializers.DateTimeField()
        parsed = field.run_validation("2026-07-20T11:00:00")
        self.assertEqual(parsed, datetime(2026, 7, 20, 11, 0, 0))
        self.assertIsNone(parsed.tzinfo, "USE_TZ=False → naive stored value.")

    @override_settings(
        USE_TZ=False,
        TIME_ZONE="UTC",
        REST_FRAMEWORK={"DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S.%fZ"},
    )
    def test_read_serializes_with_Z_so_the_browser_shifts_it(self):
        """The read half: the stored naive value is served with a 'Z'.

        A browser then renders 2026-07-20T11:00:00Z as 13:00 Berlin — the
        +2h summer shift the user reported. Write said 'local', read says 'UTC'.
        """
        field = serializers.DateTimeField()
        rendered = field.to_representation(datetime(2026, 7, 20, 11, 0, 0))
        self.assertTrue(str(rendered).endswith("Z"),
                        f"Expected a UTC 'Z' suffix, got {rendered!r}.")
        self.assertIn("11:00:00", str(rendered))

    @override_settings(USE_TZ=False, TIME_ZONE="UTC")
    def test_aware_input_IS_converted_to_utc_naive(self):
        """Contrast: had the frontend sent an offset, DRF WOULD convert it.

        A correct Berlin instant (11:00 Berlin = 09:00 UTC) sent as
        2026-07-20T09:00:00Z is converted to naive UTC 09:00 — which reads
        back as 11:00 Berlin. This is the path that makes the round-trip
        correct, and the one the frontend does NOT currently take.
        """
        field = serializers.DateTimeField()
        parsed = field.run_validation("2026-07-20T09:00:00Z")
        self.assertEqual(parsed, datetime(2026, 7, 20, 9, 0, 0))
        self.assertIsNone(parsed.tzinfo)

        # And a wall-clock-labelled-as-Z (the wrong 'Z') stores 11:00 UTC →
        # displays 13:00, same broken result as the naive send.
        wrong = field.run_validation("2026-07-20T11:00:00Z")
        self.assertEqual(wrong, datetime(2026, 7, 20, 11, 0, 0))
