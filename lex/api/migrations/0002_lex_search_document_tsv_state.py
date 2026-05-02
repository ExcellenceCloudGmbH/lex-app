"""
State-only migration: tells Django the ``tsv`` SearchVectorField exists
on ``LexSearchDocument``. The actual column was created as a Postgres
``GENERATED ... STORED`` column by ``0001_lex_search_document``; this
migration adjusts Django's migration state to match without re-running
any DDL.
"""

from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_lex_search_document"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="lexsearchdocument",
                    name="tsv",
                    field=SearchVectorField(null=True, editable=False),
                ),
            ],
        ),
    ]
