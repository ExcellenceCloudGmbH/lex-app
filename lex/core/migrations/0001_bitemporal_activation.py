"""
In-database activation of future-dated bitemporal records.

Installs ``lex/core/sql/bitemporal_activation.sql``: the applier's heartbeat table and
three pl/pgSQL functions:

    lex_bitemporal_tables()      discovery, from the catalog
    lex_pending_activations()    visibility: every SCHEDULED row, due or not
    lex_apply_due_activations()  the applier, called every minute by pg_cron

The SQL is executed through a raw cursor with no parameters: it is full of ``%I`` /
``%L`` ``format()`` placeholders that psycopg2 would otherwise try to interpolate.

Nothing here is a Django model on purpose. The framework registers every concrete model
it discovers under ``lex/`` for history tracking and for the model list, and an
operational heartbeat belongs in neither (see the SQL file). On a non-PostgreSQL backend
this migration is a no-op: the functions have no equivalent there and the in-process
fallback keeps working (design §8.13).

Design: docs/superpowers/specs/2026-09-16-bitemporal-activation-applier-design.md
"""

from pathlib import Path

from django.db import migrations

SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "bitemporal_activation.sql"

# Drop order: dependants first.
FUNCTIONS = (
    "lex_apply_due_activations",
    "lex_pending_activations",
    "lex_bitemporal_tables",
)
TABLE = "lex_activation_applier_state"


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SQL_PATH.read_text(encoding="utf-8"))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for name in FUNCTIONS:
            cursor.execute(f"DROP FUNCTION IF EXISTS {name}()")
        cursor.execute(f"DROP TABLE IF EXISTS {TABLE}")


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(install, uninstall),
    ]
