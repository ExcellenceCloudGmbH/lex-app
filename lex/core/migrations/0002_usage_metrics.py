"""
Fleet metrics, phase 1: the usage recorder's two tables.

Installs ``lex/core/sql/usage_metrics.sql``:

* ``lex_usage_daily``: one row per person or API key, product area, channel and UTC day;
* ``lex_runtime_versions``: one row per lex-app version this instance has served, with
  when it was first and last seen.

Nothing here is a Django model on purpose, for the reason 0001 gives: the framework
registers every concrete model it discovers under ``lex/`` for history tracking and for the
model list. On a non-PostgreSQL backend this migration is a no-op and the recorder stays
inert (``lex.core.services.usage_metrics.supported``).

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)
"""

from pathlib import Path

from django.db import migrations

SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "usage_metrics.sql"
TABLES = ("lex_usage_daily", "lex_runtime_versions")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SQL_PATH.read_text(encoding="utf-8"))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")


class Migration(migrations.Migration):

    dependencies = [("core", "0001_bitemporal_activation")]

    operations = [
        migrations.RunPython(install, uninstall),
    ]
