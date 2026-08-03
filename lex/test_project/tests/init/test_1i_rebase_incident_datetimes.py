"""Cluster 1i — the ``rebase_incident_datetimes`` maintenance command.

Intent: after the TIME_ZONE incident (f622c9c/rc212 flipped the Postgres
connection zone Berlin→UTC), user-entered datetimes written in the incident
window were stored one offset too late. This command re-anchors exactly those
values and nothing else. A regression here either leaves corrupted data
uncorrected or, worse, shifts already-correct app-stamped timestamps — so these
scenarios pin the three guarantees: it corrects in-window user datetimes, never
touches managed ``created_at``, and never touches pre-incident rows.

Cluster 1i — scenarios 1.195–1.198. Type: E.
Covers: lex/lex_app/management/commands/rebase_incident_datetimes.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1i_rebase_incident_datetimes.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, IncidentDatetimeItem

pytestmark = pytest.mark.init

CUTOFF = "2026-06-26T00:00:00+00:00"     # this instance's rc212 upgrade (window start)
UNTIL = "2026-08-01T00:00:00+00:00"      # this instance's aware-UTC fix (window end)
IN_WINDOW = datetime(2026, 7, 20, 10, 0, 0, tzinfo=dt_timezone.utc)  # created in [cutoff, until)
PRE_WINDOW = datetime(2026, 6, 1, 10, 0, 0, tzinfo=dt_timezone.utc)  # created < cutoff (pre-upgrade)
POST_FIX = datetime(2026, 8, 15, 10, 0, 0, tzinfo=dt_timezone.utc)   # created >= until (post-fix)
# A Berlin user meant 11:00 (=09:00Z) but the incident stored the wall-clock as UTC:
CORRUPTED = datetime(2026, 7, 20, 11, 0, 0, tzinfo=dt_timezone.utc)   # 11:00Z (wrong, summer)
CORRECTED = datetime(2026, 7, 20, 9, 0, 0, tzinfo=dt_timezone.utc)    # 09:00Z (Berlin summer −2h)
# A WINTER value proves the correction is DST-aware (−1h, not a fixed −2h):
WINTER_CORRUPTED = datetime(2026, 1, 15, 11, 0, 0, tzinfo=dt_timezone.utc)  # 11:00Z (winter)
WINTER_CORRECTED = datetime(2026, 1, 15, 10, 0, 0, tzinfo=dt_timezone.utc)  # 10:00Z (Berlin winter −1h)
# Wall-clock digits in the fall-back overlap (25 Oct 2026 02:30 occurs twice):
DST_OVERLAP = datetime(2026, 10, 25, 2, 30, 0, tzinfo=dt_timezone.utc)


class TestCluster01i_RebaseIncidentDatetimes(E2ETestCase):
    """Cluster 1i: surgical re-anchoring of incident-corrupted datetimes."""

    e2e_models = ALL_MODELS

    def _seed(self, created_at, event_at=CORRUPTED, name="probe"):
        """Create a row, then stamp created_at/event_at directly (bypass hooks)."""
        item = IncidentDatetimeItem.objects.create(name=name)
        IncidentDatetimeItem.objects.filter(pk=item.pk).update(
            created_at=created_at, edited_at=created_at, event_at=event_at,
        )
        item.refresh_from_db()
        return item

    def _run(self, apply):
        args = ["rebase_incident_datetimes", "--models", "lex_app.IncidentDatetimeItem",
                "--source-zone", "Europe/Berlin", "--cutoff", CUTOFF, "--until", UNTIL]
        if apply:
            args.append("--apply")
        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_1_195_apply_reanchors_in_window_value_and_spares_created_at(self) -> None:
        """
        Scenario 1.195: --apply corrects an in-window user datetime by the
        source-zone offset and leaves the managed created_at untouched.
        Given: a row created in the incident window whose event_at holds the
               mis-anchored 11:00Z (the user meant 11:00 Berlin = 09:00Z)
        When: the command runs with --apply
        Then: event_at becomes 09:00Z; created_at is unchanged
        """
        item = self._seed(IN_WINDOW)
        self._run(apply=True)
        item.refresh_from_db()
        self.assertEqual(
            item.event_at, CORRECTED,
            f"event_at should be re-anchored to 09:00Z, got {item.event_at}.",
        )
        self.assertEqual(
            item.created_at, IN_WINDOW,
            "created_at is app-stamped and was always correct — it must NOT be "
            f"touched, but it changed to {item.created_at}.",
        )

    def test_1_196_dry_run_writes_nothing(self) -> None:
        """
        Scenario 1.196: without --apply the command reports but writes nothing.
        Given: the same in-window corrupted row
        When: the command runs in dry-run mode
        Then: event_at is unchanged, and the output says nothing was written
        """
        item = self._seed(IN_WINDOW)
        output = self._run(apply=False)
        item.refresh_from_db()
        self.assertEqual(
            item.event_at, CORRUPTED,
            "Dry-run must not write — event_at should still be the corrupted "
            f"11:00Z, got {item.event_at}.",
        )
        self.assertIn(
            "DRY-RUN", output,
            "Dry-run must clearly announce that nothing was written.",
        )

    def test_1_197_pre_upgrade_rows_are_left_untouched(self) -> None:
        """
        Scenario 1.197: a row created before THIS instance's upgrade (< cutoff)
        was written under the correct Berlin anchoring and must not be shifted.
        This is the late-upgrader safety: an instance that upgraded weeks after
        the global release must not have its correct pre-upgrade rows corrupted.
        Given: a row whose created_at predates the cutoff
        When: the command runs with --apply
        Then: event_at is unchanged
        """
        item = self._seed(PRE_WINDOW)
        self._run(apply=True)
        item.refresh_from_db()
        self.assertEqual(
            item.event_at, CORRUPTED,
            "A pre-upgrade row must be left untouched, but event_at moved to "
            f"{item.event_at} — the cutoff over-corrected correct data.",
        )

    def test_1_198_post_fix_rows_are_left_untouched(self) -> None:
        """
        Scenario 1.198: a row created on/after this instance's aware-UTC fix
        (>= until) was written correctly and must not be re-shifted.
        Guards against re-running the migration after the fix is live: the
        window is [cutoff, until), so post-fix correct rows stay untouched.
        Given: a row whose created_at is on/after --until
        When: the command runs with --apply
        Then: event_at is unchanged
        """
        item = self._seed(POST_FIX)
        self._run(apply=True)
        item.refresh_from_db()
        self.assertEqual(
            item.event_at, CORRUPTED,
            "A post-fix row must be left untouched, but event_at moved to "
            f"{item.event_at} — the window's upper bound was not honored.",
        )

    def test_1_199_correction_is_dst_aware_winter_shifts_one_hour(self) -> None:
        """
        Scenario 1.199: the correction uses each value's OWN date, so a winter
        value shifts −1h (not the summer −2h) — proving it is not a fixed offset.
        Given: an in-window row whose event_at is a WINTER 11:00Z
        When: the command runs with --apply
        Then: event_at becomes 10:00Z (Berlin winter = UTC+1), not 09:00Z
        """
        item = self._seed(IN_WINDOW, event_at=WINTER_CORRUPTED)
        self._run(apply=True)
        item.refresh_from_db()
        self.assertEqual(
            item.event_at, WINTER_CORRECTED,
            f"Winter value should shift −1h to 10:00Z (DST-aware), got "
            f"{item.event_at} — the correction used a fixed offset.",
        )

    def test_1_200_dst_transition_values_are_flagged(self) -> None:
        """
        Scenario 1.200: a value whose wall-clock falls in a DST-transition window
        (the fall-back overlap) is reported for manual review — its instant is
        ambiguous and cannot be recovered with certainty.
        Given: an in-window row whose event_at is 25 Oct 2026 02:30 (overlap)
        When: the command runs (dry-run)
        Then: the output flags it as a DST-transition value to verify
        """
        self._seed(IN_WINDOW, event_at=DST_OVERLAP)
        output = self._run(apply=False)
        self.assertIn(
            "DST-transition", output,
            "A value in the fall-back overlap must be flagged for review; "
            f"output was:\n{output}",
        )
