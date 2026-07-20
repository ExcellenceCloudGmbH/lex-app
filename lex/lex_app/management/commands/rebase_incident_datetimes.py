"""Re-anchor user-entered datetimes mis-stored during the TIME_ZONE incident.

Background
----------
Commit ``f622c9c`` (PR #635, shipped in rc212 on 2026-06-26) flipped the
PostgreSQL *connection* timezone from ``Europe/Berlin`` to ``UTC`` on the
``default`` and ``GCP`` deployment targets (``USE_TZ=False`` there). Because a
naive datetime is anchored in the connection zone on write, every
**user-entered / project-supplied** datetime saved *after* that flip was stored
one offset too late: a Berlin user who meant ``11:00`` (=09:00Z) got ``11:00Z``.

What this command does NOT touch
--------------------------------
Framework-managed timestamps (``created_at``, ``edited_at``, and the bitemporal
history windows ``valid_from`` / ``valid_to`` / ``sys_from`` / ``sys_to``) were
written by ``lex_datetime_now()``, whose digits and the connection zone both
derive from ``settings.TIME_ZONE`` in lockstep — so their stored instants were
always correct. They are excluded, and the history/audit tables are skipped
entirely. Rows written *before* the incident window are also correct and left
untouched.

The correction
--------------
For an affected column the stored instant's UTC wall-clock digits are the
*intended local wall-clock*, so we re-interpret them in the source zone::

    col := (col AT TIME ZONE 'UTC') AT TIME ZONE <source-zone>

which is DST-correct via the IANA zone. Example (Berlin summer): ``11:00Z`` →
digits ``11:00`` → interpret as Berlin → ``09:00Z``.

Safety
------
* **Dry-run by default** — prints what it *would* change; ``--apply`` writes.
* **Per-instance window** — ``--cutoff`` is that instance's upgrade-to-≥rc212
  instant; rows with ``created_at >= cutoff`` are the affected set. ``created_at``
  is a reliable discriminator (it was never corrupted).
* **Ambiguous rows** (``created_at < cutoff`` but ``edited_at >= cutoff``) can't
  be classified automatically — they are *reported*, never auto-corrected.
* **PostgreSQL only** — the incident only affected the Postgres targets, and the
  correction uses ``AT TIME ZONE``.
* **Not idempotent** — running twice double-shifts. Run exactly once per instance.

Run::

    lex rebase_incident_datetimes --cutoff 2026-06-26T00:00:00+00:00            # dry-run
    lex rebase_incident_datetimes --cutoff 2026-06-26T00:00:00+00:00 --apply    # write
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models

# f622c9c shipped in rc212 on 2026-06-26. Conservative fallback only — operators
# should pass their instance's real upgrade instant via --cutoff.
DEFAULT_CUTOFF = "2026-06-26T00:00:00+00:00"

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
            "--source-zone", default="Europe/Berlin",
            help="IANA zone the users' wall-clocks were intended in "
                 "(default: Europe/Berlin).",
        )
        parser.add_argument(
            "--cutoff", default=None,
            help="ISO-8601 instant of this instance's upgrade to >=rc212. Rows "
                 "with created_at >= cutoff are the incident window. Default: "
                 f"{DEFAULT_CUTOFF}.",
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

        cutoff = self._parse_cutoff(opts["cutoff"] or DEFAULT_CUTOFF)
        apply = opts["apply"]
        model_filter = set(opts["models"]) if opts["models"] else None

        targets = self._discover(model_filter)
        if not targets:
            self.stdout.write("No customer models with re-anchorable datetime fields found.")
            return

        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"rebase_incident_datetimes [{mode}]  source_zone={source_zone}  "
            f"cutoff={cutoff.isoformat()}"
        ))

        total_fixed = total_ambiguous = 0
        for model, fields in targets:
            created_col = model._meta.get_field("created_at").column
            edited_col = model._meta.get_field("edited_at").column
            table = model._meta.db_table
            for field in fields:
                col = field.column
                in_window, ambiguous = self._counts(table, col, created_col, edited_col, cutoff)
                total_fixed += in_window
                total_ambiguous += ambiguous
                if in_window or ambiguous:
                    self.stdout.write(
                        f"  {model._meta.label}.{field.name}: {in_window} in-window, "
                        f"{ambiguous} ambiguous (created<cutoff, edited>=cutoff)"
                    )
                    for before, after in self._samples(table, col, created_col, source_zone, cutoff):
                        self.stdout.write(f"      {before}  ->  {after}")
                if apply and in_window:
                    self._apply(table, col, created_col, source_zone, cutoff)

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
    def _parse_cutoff(raw: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandError(f"Invalid --cutoff {raw!r} (expected ISO-8601): {exc}")
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

    def _counts(self, table, col, created_col, edited_col, cutoff):
        with connection.cursor() as cur:
            cur.execute(
                f'SELECT count(*) FROM "{table}" '
                f'WHERE "{col}" IS NOT NULL AND "{created_col}" >= %s',
                [cutoff],
            )
            in_window = cur.fetchone()[0]
            cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NOT NULL '
                f'AND "{created_col}" < %s AND "{edited_col}" >= %s',
                [cutoff, cutoff],
            )
            ambiguous = cur.fetchone()[0]
        return in_window, ambiguous

    def _samples(self, table, col, created_col, source_zone, cutoff, limit=3):
        with connection.cursor() as cur:
            cur.execute(
                f'SELECT "{col}", ("{col}" AT TIME ZONE \'UTC\') AT TIME ZONE %s '
                f'FROM "{table}" WHERE "{col}" IS NOT NULL AND "{created_col}" >= %s '
                f'ORDER BY "{created_col}" LIMIT {int(limit)}',
                [source_zone, cutoff],
            )
            return cur.fetchall()

    def _apply(self, table, col, created_col, source_zone, cutoff):
        with connection.cursor() as cur:
            cur.execute(
                f'UPDATE "{table}" SET "{col}" = ("{col}" AT TIME ZONE \'UTC\') '
                f'AT TIME ZONE %s WHERE "{col}" IS NOT NULL AND "{created_col}" >= %s',
                [source_zone, cutoff],
            )
            return cur.rowcount
