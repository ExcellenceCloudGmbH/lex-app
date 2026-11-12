---
date: 2026-10-12
clusters: [1]
tests_added: 8
suite_tally: "1bq 8 pass; full cluster suite 1681 pass / 37 fail / 27 skip / 6 xfail / 5 collection errors - every failure in the categories earlier runs had (24 cluster-15 rows when run after cluster 6, 6 Reflex CLI with no reflex package locally, 5 stress timing, the gate self-test); strict wheel check on a wheel built from this branch passes; an instance migrated by the old lex init upgrades cleanly"
---

# Batch 1bq — `lex init` writes migrations for the project only

On its first run, the wheel check added in #808 (`.github/scripts/check_wheel_migrations.py`) found
that `lex init` wrote a migration into two installed packages' folders in site-packages on every
install. [Batch 1bq](../../clusters/01-init/batches.md) records the fix: `lex init` makes migrations
for the project's own app; lex ships the api-key fork's missing widening through MIGRATION_MODULES,
under the name `lex init` generated; oauth2_authcodeflow gets the default_auto_field its migrations
were made with.

Making migrations for the project alone was not enough. On a database built purely from migrations,
an API key could not be created (1.678 failed with "value too long for type character varying(100)"),
because the widening only ever came from the file `lex init` wrote.

Verified beyond the suite: the strict wheel check on a wheel built from this branch passes with no
drift at all; and an instance migrated by the old `lex init`, with both files in site-packages,
upgrades to that wheel with nothing to apply, nothing to generate, and an API key created.

The wheel check's KNOWN_THIRD_PARTY_DRIFT is empty now, so any package that drifts fails it.

**Merged with lex-app-v2 (2026-11-12):** renumbered 1av → 1bq (1.386 – 1.392 → 1.674 – 1.680), as 1av
was taken there. lex-app-v2 had moved to exc-djangorestframework-api-key 1.0.1 (which ships the widening
under the same migration name) and django-oauth2-codeflow 3.0 (whose app config pins `AutoField`), so the
merge dropped `lex/lex_app/vendored` and its settings; the batch's scenarios now hold that lex overrides
neither package.
