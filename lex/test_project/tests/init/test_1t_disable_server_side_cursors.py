"""Server-side cursors stay disabled on every PostgreSQL database alias.

Intent: the framework runs behind a transaction-pooling proxy
(cloud-sql-proxy / pgbouncer) in deployed environments. PostgreSQL
server-side (named) cursors are incompatible with transaction pooling — the
``DECLARE _django_curs_...`` and a later ``FETCH`` can be routed to different
backend connections, raising ``psycopg2.OperationalError: cursor
"_django_curs_..." does not exist``. Any ``.iterator()`` call (permission
filter backend, list views, exports) hits this and fails *deterministically*,
so a reload never recovers. Django only honours ``DISABLE_SERVER_SIDE_CURSORS``
when it is set **inside the per-database config dict** (read from
``connection.settings_dict``); a module-level ``DISABLE_SERVER_SIDE_CURSORS``
in ``settings.py`` is silently ignored. A regression that moves the flag back
to module level re-enables server-side cursors in production and breaks every
iterator query behind the pooler — these tests pin the flag in the place Django
actually reads it.

Cluster 1t — scenarios 1.169–1.170. Type: U.
Covers: lex/lex_app/settings.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1t_disable_server_side_cursors.py -v
"""

from __future__ import annotations

import unittest

import pytest
from django.conf import settings
from django.db import connections
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


def _postgres_aliases() -> dict:
    """All configured DATABASES aliases whose engine is PostgreSQL."""
    return {
        alias: cfg
        for alias, cfg in settings.DATABASES.items()
        if "postgresql" in cfg.get("ENGINE", "")
    }


class TestCluster01t_DisableServerSideCursors(SimpleTestCase):
    """Cluster 1t: DISABLE_SERVER_SIDE_CURSORS is effective for Postgres."""

    # -- 1.169 ---------------------------------------------------------
    def test_1_169_flag_set_in_every_postgres_db_config(self) -> None:
        """
        Scenario 1.169: every PostgreSQL alias carries the flag in its config dict.
        Given: the resolved ``settings.DATABASES`` mapping
        When: each PostgreSQL alias's config dict is inspected
        Then: it contains ``DISABLE_SERVER_SIDE_CURSORS: True`` — the only place
              Django reads it. A module-level constant (the old bug) would leave
              this key absent and server-side cursors enabled behind the pooler.
        """
        postgres_aliases = _postgres_aliases()
        self.assertTrue(
            postgres_aliases,
            "Expected at least one PostgreSQL alias in DATABASES to validate; "
            f"got engines: {[c.get('ENGINE') for c in settings.DATABASES.values()]!r}",
        )
        for alias, cfg in postgres_aliases.items():
            self.assertIs(
                cfg.get("DISABLE_SERVER_SIDE_CURSORS"),
                True,
                f"DATABASES[{alias!r}] must set DISABLE_SERVER_SIDE_CURSORS=True "
                "inside its config dict (server-side cursors break behind a "
                "transaction-pooling proxy); a module-level setting is ignored.",
            )

    # -- 1.170 ---------------------------------------------------------
    def test_1_170_default_connection_honours_flag(self) -> None:
        """
        Scenario 1.170: the live default connection actually honours the flag.
        Given: the active ``connections['default']`` wrapper
        When: its ``settings_dict`` is read (Django's authoritative source)
        Then: on PostgreSQL the flag is True — proving Django picked it up from
              the per-database config and not from an ignored module global
              (Django defaults this key to False when it is absent, so the old
              module-level placement would surface here as False). On SQLite the
              flag is irrelevant (no server-side cursors), so we only assert the
              engine to keep the test meaningful in a local SQLite run.
        """
        settings_dict = connections["default"].settings_dict
        engine = settings_dict.get("ENGINE", "")

        if "postgresql" in engine:
            self.assertIs(
                settings_dict.get("DISABLE_SERVER_SIDE_CURSORS"),
                True,
                "The live default PostgreSQL connection must report "
                "DISABLE_SERVER_SIDE_CURSORS=True; a False here means Django "
                "fell back to its default and the flag is in the wrong place.",
            )
        else:
            self.assertIn(
                "sqlite",
                engine,
                "Default connection is neither PostgreSQL nor SQLite; the "
                "server-side-cursor contract is unverified for this engine.",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
