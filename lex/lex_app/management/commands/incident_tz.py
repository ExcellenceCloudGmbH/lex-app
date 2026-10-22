"""``lex incident_tz scan``: which datetime values did the TIME_ZONE incident store wrong?

A read-only report for one database: the exposure periods dated from the
database's own audit log, every datetime column, and a verdict (A–E) for each
value that was not written by a clock. The logic lives in
``lex/lex_app/incident_tz/scan.py``, a single file that also runs on its own
against an instance that has not been upgraded.

Run::

    lex incident_tz scan --out ./scan-report
    lex incident_tz scan --window 2026-07-06T08:04Z/2026-10-21T09:00Z   # state the period
"""

from __future__ import annotations

import datetime as dt
import os

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, models, transaction

from lex.lex_app.incident_tz import scan as tzscan


def declared_datetime_columns() -> set:
    """``(table, column)`` for every DateTimeField the loaded models declare."""
    declared = set()
    for model in apps.get_models(include_auto_created=True):
        for model_field in model._meta.local_fields:
            if isinstance(model_field, models.DateTimeField):
                declared.add((model._meta.db_table, model_field.column))
    return declared


class Command(BaseCommand):
    help = (
        "TIME_ZONE incident (rc212 – v2.1.3). 'scan' reports, read-only, which "
        "datetime values this database stored wrong."
    )

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest="action", required=True)
        scan = actions.add_parser("scan", help="Read-only report; writes nothing to the database.")
        scan.add_argument("--out", help="Folder for scan.json and scan.md "
                                        "(default: incident_tz_scan/<database>-<time>).")
        scan.add_argument("--zone", default=os.getenv("LEX_TIME_ZONE", tzscan.DEFAULT_ZONE),
                          help="Zone users entered dates and times in.")
        scan.add_argument("--schema", action="append", dest="schemas",
                          help="Schema to scan (repeatable; default: public).")
        scan.add_argument("--table", action="append", dest="tables",
                          help="Only classify these tables (repeatable).")
        scan.add_argument("--window", action="append", default=[],
                          help="START/END of an exposure period in ISO 8601 (repeatable); "
                               "replaces the periods read from the audit log.")
        scan.add_argument("--database", default="default", help="Django database alias.")
        scan.add_argument("--print", action="store_true", dest="print_markdown",
                          help="Also print the Markdown report.")

    def handle(self, *args, **options):
        if options["action"] == "scan":
            self._scan(options)

    def _scan(self, options):
        alias = options["database"]
        connection = connections[alias]
        if connection.vendor != "postgresql":
            raise CommandError(
                "The scan reads PostgreSQL time zones; this database is "
                f"{connection.vendor}."
            )
        try:
            windows = [tzscan.parse_window(text) for text in options["window"]]
        except tzscan.ScanError as exc:
            raise CommandError(str(exc)) from exc
        name = connection.settings_dict.get("NAME") or alias
        with transaction.atomic(using=alias):
            with connection.cursor() as cursor:
                # First statement of the transaction: nothing after it can write.
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                report = tzscan.scan(
                    cursor,
                    zone=options["zone"],
                    schemas=tuple(options["schemas"] or tzscan.DEFAULT_SCHEMAS),
                    windows=windows,
                    tables=options["tables"],
                    expected_columns=declared_datetime_columns(),
                    database=name,
                )
        stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = options["out"] or os.path.join("incident_tz_scan", f"{name}-{stamp}")
        json_path, md_path = tzscan.write_report(report, out_dir)

        periods = report["exposure"]["periods"]
        self.stdout.write(
            f"Exposure: {len(periods)} period(s), source {report['exposure']['source']}."
        )
        for layer in ("main", "history", "meta_history"):
            counts = report["totals"][layer]
            self.stdout.write(
                f"  {layer:<13} " + "  ".join(f"{k}={v}" for k, v in counts.items())
            )
        self.stdout.write(f"Wrote {json_path} and {md_path}")
        if options["print_markdown"]:
            self.stdout.write(tzscan.render_markdown(report))
