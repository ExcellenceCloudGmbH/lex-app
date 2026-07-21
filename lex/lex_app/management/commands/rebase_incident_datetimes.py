"""Re-anchor user-entered datetimes mis-stored during the TIME_ZONE incident.

Background
----------
Commit ``f622c9c`` (PR #635, first in rc212) flipped the PostgreSQL *connection*
timezone from ``Europe/Berlin`` to ``UTC`` on the ``default`` and ``GCP``
deployment targets (``USE_TZ=False`` there). Because a naive datetime is
anchored in the connection zone on write, every **user-entered / project-supplied**
datetime saved while an instance ran that code was stored one offset too late: a
Berlin user who meant ``11:00`` (=09:00Z) got ``11:00Z``.

The window is PER INSTANCE
--------------------------
An instance became affected the moment *it* deployed ≥rc212 — instances upgrade
on different days, so **there is no global cutoff**. It stopped being affected the
moment *it* deployed the aware-UTC fix (``USE_TZ=True``), after which writes are
correct again. So the corrupted set on a given instance is::

    created_at ∈ [ --cutoff , --until )

where ``--cutoff`` is that instance's ≥rc212 upgrade instant and ``--until`` is
its aware-UTC fix deployment (default: now — correct when migrating at the same
maintenance window as the fix). ``created_at`` is a reliable discriminator: it is
app-stamped and was never corrupted.

Direction of error (why --cutoff is required)
---------------------------------------------
* A cutoff set **too early** re-anchors correct *pre-upgrade* rows — it **corrupts
  good data**, the dangerous direction that is hard to undo.
* A cutoff set **too late** merely leaves some rows uncorrected — recoverable, just
  re-run with an earlier cutoff.

So there is deliberately **no default cutoff**; the operator must supply their
instance's real upgrade instant (from deployment/release history). When unsure,
prefer a *later* cutoff and use the dry-run to eyeball the before→after samples.

What this command does NOT touch
--------------------------------
Framework-managed timestamps (``created_at``, ``edited_at`` and the bitemporal
history windows) were written by ``lex_datetime_now()``, whose digits and the
connection zone both derive from ``settings.TIME_ZONE`` in lockstep — so their
stored instants were always correct. They are excluded, and history/audit tables
are skipped entirely. Rows outside the window are left untouched.

The correction
--------------
For an affected column the stored instant's UTC wall-clock digits are the
*intended local wall-clock*, re-interpreted in the source zone::

    col := (col AT TIME ZONE 'UTC') AT TIME ZONE <source-zone>

DST-correct via the IANA zone. Example (Berlin summer): ``11:00Z`` → digits
``11:00`` → interpret as Berlin → ``09:00Z``.

Safety
------
* **Dry-run by default** — prints what it *would* change; ``--apply`` writes.
* **Ambiguous rows** (``created_at`` before the window but ``edited_at`` inside it)
  can't be classified automatically — they are *reported*, never auto-corrected.
* **PostgreSQL only** — the incident only affected the Postgres targets.
* **Not idempotent** — running twice double-shifts. Run exactly once per instance.

Run::

    # this instance upgraded to rc212 on 2026-07-10; migrate at fix-deploy time
    lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00           # dry-run
    lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00 --apply   # write
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.utils import timezone as dj_timezone

# Written by lex_datetime_now(); tracked TIME_ZONE in lockstep with anchoring, so
# their stored instants were always correct. Never rebase these.
MANAGED_FIELD_NAMES = {"created_at", "edited_at"}

# Framework / third-party apps whose datetimes are app-stamped or irrelevant.
EXCLUDED_APP_LABELS = {
    "auth", "contenttypes", "sessions", "admin", "messages", "staticfiles",
    "oauth2_authcodeflow", "simple_history", "django_celery_beat",
    "django_celery_results", "legacy_data", "audit_logging",
}


class Command(BaseCommand):
    help = (
        "Re-anchor user-entered datetimes mis-stored during the TIME_ZONE "
        "incident (dry-run by default; pass --apply to write)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cutoff", required=True,
            help="REQUIRED. ISO-8601 instant this instance upgraded to >=rc212 "
                 "(its connection zone became UTC). Rows created before this are "
                 "correct and left alone. No default — too-early corrupts good data.",
        )
        parser.add_argument(
            "--until", default=None,
            help="ISO-8601 instant this instance deployed the aware-UTC fix "
                 "(USE_TZ=True); rows created on/after it are already correct. "
                 "Default: now (correct when migrating at the fix's maintenance window).",
        )
        parser.add_argument(
            "--source-zone", default="Europe/Berlin",
            help="IANA zone the users' wall-clocks were intended in "
                 "(default: Europe/Berlin).",
        )
        parser.add_argument(
            "--models", nargs="*", default=None, metavar="app_label.Model",
            help="Limit to these model labels. Default: all customer models.",
        )
        parser.add_argument(
            "--apply", action="store_true", default=False,
            help="Write the correction. Without it the command is a dry-run.",
        )

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            raise CommandError(
                "Only the PostgreSQL targets were affected by the incident and "
                "the correction uses 'AT TIME ZONE'. Current backend: "
                f"{connection.vendor}."
            )

        source_zone = opts["source_zone"]
        try:
            ZoneInfo(source_zone)
        except Exception as exc:  # noqa: BLE001 - surface any zone error to the operator
            raise CommandError(f"Invalid --source-zone {source_zone!r}: {exc}")

        cutoff = self._parse_dt(opts["cutoff"], "--cutoff")
        until = self._parse_dt(opts["until"], "--until") if opts["until"] else dj_timezone.now()
        if until <= cutoff:
            raise CommandError(
                f"--until ({until.isoformat()}) must be after --cutoff "
                f"({cutoff.isoformat()}); the window [cutoff, until) is empty."
            )

        apply = opts["apply"]
        model_filter = set(opts["models"]) if opts["models"] else None

        targets = self._discover(model_filter)
        if not targets:
            self.stdout.write("No customer models with re-anchorable datetime fields found.")
            return

        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"rebase_incident_datetimes [{mode}]  source_zone={source_zone}  "
            f"window=[{cutoff.isoformat()}, {until.isoformat()})"
        ))

        total_fixed = total_ambiguous = 0
        for model, fields in targets:
            created_col = model._meta.get_field("created_at").column
            edited_col = model._meta.get_field("edited_at").column
            table = model._meta.db_table
            for field in fields:
                col = field.column
                in_window, ambiguous = self._counts(table, col, created_col, edited_col, cutoff, until)
                total_fixed += in_window
                total_ambiguous += ambiguous
                if in_window or ambiguous:
                    self.stdout.write(
                        f"  {model._meta.label}.{field.name}: {in_window} in-window, "
                        f"{ambiguous} ambiguous (created<cutoff, edited in window)"
                    )
                    for before, after in self._samples(table, col, created_col, source_zone, cutoff, until):
                        self.stdout.write(f"      {before}  ->  {after}")
                if apply and in_window:
                    self._apply(table, col, created_col, source_zone, cutoff, until)

        self.stdout.write("")
        summary = (
            f"{total_fixed} row-field value(s) in the incident window; "
            f"{total_ambiguous} ambiguous flagged for manual review."
        )
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Applied. {summary}"))
        else:
            self.stdout.write(self.style.WARNING(
                f"DRY-RUN — nothing written. {summary} Re-run with --apply to write. "
                "NOTE: not idempotent — run exactly once per instance."
            ))

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _parse_dt(raw: str, flag: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise CommandError(f"Invalid {flag} {raw!r} (expected ISO-8601): {exc}")
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)

    def _discover(self, model_filter):
        """Return [(model, [DateTimeField, ...]), ...] for customer models."""
        out = []
        for model in apps.get_models():
            meta = model._meta
            if meta.abstract or meta.proxy or model.__name__.startswith("Historical"):
                continue
            if meta.app_label in EXCLUDED_APP_LABELS:
                continue
            if model_filter and meta.label not in model_filter:
                continue
            names = {f.name for f in meta.get_fields() if hasattr(f, "attname")}
            # created_at is both the era discriminator and the LexModel marker.
            if "created_at" not in names or "edited_at" not in names:
                continue
            fields = [
                f for f in meta.get_fields()
                if isinstance(f, models.DateTimeField)
                and f.name not in MANAGED_FIELD_NAMES
                and not getattr(f, "auto_now", False)
                and not getattr(f, "auto_now_add", False)
            ]
            if fields:
                out.append((model, fields))
        return out

    def _counts(self, table, col, created_col, edited_col, cutoff, until):
        with connection.cursor() as cur:
            cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NOT NULL '
                f'AND "{created_col}" >= %s AND "{created_col}" < %s',
                [cutoff, until],
            )
            in_window = cur.fetchone()[0]
            cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NOT NULL '
                f'AND "{created_col}" < %s AND "{edited_col}" >= %s AND "{edited_col}" < %s',
                [cutoff, cutoff, until],
            )
            ambiguous = cur.fetchone()[0]
        return in_window, ambiguous

    def _samples(self, table, col, created_col, source_zone, cutoff, until, limit=3):
        with connection.cursor() as cur:
            cur.execute(
                f'SELECT "{col}", ("{col}" AT TIME ZONE \'UTC\') AT TIME ZONE %s '
                f'FROM "{table}" WHERE "{col}" IS NOT NULL '
                f'AND "{created_col}" >= %s AND "{created_col}" < %s '
                f'ORDER BY "{created_col}" LIMIT {int(limit)}',
                [source_zone, cutoff, until],
            )
            return cur.fetchall()

    def _apply(self, table, col, created_col, source_zone, cutoff, until):
        with connection.cursor() as cur:
            cur.execute(
                f'UPDATE "{table}" SET "{col}" = ("{col}" AT TIME ZONE \'UTC\') '
                f'AT TIME ZONE %s WHERE "{col}" IS NOT NULL '
                f'AND "{created_col}" >= %s AND "{created_col}" < %s',
                [source_zone, cutoff, until],
            )
            return cur.rowcount
