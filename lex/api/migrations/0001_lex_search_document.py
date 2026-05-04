"""
Initial migration for the global-search index.

Creates :class:`lex.api.models.LexSearchDocument` plus:

* A ``tsv`` ``tsvector`` generated column over ``title || ' ' || body`` so
  Postgres maintains the FTS document automatically — no triggers, no
  application code to keep in sync.
* GIN index on ``tsv`` for ``websearch_to_tsquery`` lookups.
* GIN index on ``(title || ' ' || body)`` with ``gin_trgm_ops`` for
  substring / typo-tolerant matches via ``pg_trgm``.

The extensions and Postgres-specific DDL are guarded so the migration
no-ops on non-Postgres backends (sqlite test runs, dev environments).
"""

from django.db import migrations, models


PG_FORWARD_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE lex_search_document
    ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            coalesce(title, '') || ' ' || coalesce(body, '')
        )
    ) STORED;

CREATE INDEX lex_search_doc_tsv_gin
    ON lex_search_document USING GIN (tsv);

CREATE INDEX lex_search_doc_trgm_gin
    ON lex_search_document USING GIN (
        (coalesce(title, '') || ' ' || coalesce(body, '')) gin_trgm_ops
    );
"""

PG_REVERSE_SQL = """
DROP INDEX IF EXISTS lex_search_doc_trgm_gin;
DROP INDEX IF EXISTS lex_search_doc_tsv_gin;
ALTER TABLE lex_search_document DROP COLUMN IF EXISTS tsv;
"""


def _apply_pg_ddl(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(PG_FORWARD_SQL)


def _revert_pg_ddl(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(PG_REVERSE_SQL)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="LexSearchDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("container_id", models.CharField(db_index=True, max_length=255)),
                ("object_id", models.CharField(max_length=64)),
                ("model_label", models.CharField(blank=True, default="", max_length=255)),
                ("title", models.TextField(blank=True, default="")),
                ("body", models.TextField(blank=True, default="")),
                ("url", models.CharField(blank=True, default="", max_length=512)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "content_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="+",
                        to="contenttypes.contenttype",
                    ),
                ),
            ],
            options={
                "db_table": "lex_search_document",
            },
        ),
        migrations.AddConstraint(
            model_name="lexsearchdocument",
            constraint=models.UniqueConstraint(
                fields=("container_id", "object_id"),
                name="lex_search_document_container_object_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="lexsearchdocument",
            index=models.Index(fields=["container_id"], name="lex_search_doc_container_idx"),
        ),
        migrations.RunPython(_apply_pg_ddl, _revert_pg_ddl),
    ]
