"""``lex create_api_key`` — issue a scoped API key from the command line.

Intent
------
Operators create machine access without the admin: the command issues a
``LexAPIKey`` that acts as an existing user, with the given scopes, path
prefixes and expiry. The raw key goes to stdout once (for a pipe into a
secret store); only its hash is stored. An unknown user is an error and
creates nothing.

Cluster 1aw — scenarios 1.396–1.397. Type: U.
Covers: lex/authentication/management/commands/create_api_key.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1aw_create_api_key.py -v
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from lex.authentication.models import LexAPIKey

pytestmark = pytest.mark.init


class TestCluster01aw_CreateApiKey(TestCase):
    """Cluster 1aw: the create_api_key command."""

    # -- 1.396 ---------------------------------------------------------
    def test_1_396_issues_a_scoped_key_for_the_user(self) -> None:
        """
        Scenario 1.396: the command issues a working key with the given limits.
        Given: a user "cluster1aw_sync"
        When: create_api_key runs with scopes, two prefixes and a 30-day expiry
        Then: stdout is exactly the raw key; the stored key acts as that user,
              is named after them, carries the scopes and prefixes, expires in
              about 30 days, and only its hash is stored.
        """
        user = User.objects.create_user(username="cluster1aw_sync")
        out, err = StringIO(), StringIO()
        call_command(
            "create_api_key", "--user", "cluster1aw_sync", "--scopes", "read, list,,edit",
            "--prefix", "api/myproject/", "--prefix", "api/other/", "--expires-in-days", "30",
            stdout=out, stderr=err,
        )
        raw = out.getvalue().strip()
        key = LexAPIKey.objects.get_from_key(raw)
        self.assertEqual(key.user, user)
        self.assertEqual(key.name, "cluster1aw_sync")
        self.assertEqual(key.scopes, ["read", "list", "edit"])
        self.assertEqual(key.allowed_path_prefixes, ["api/myproject/", "api/other/"])
        self.assertAlmostEqual((key.expiry_date - timezone.now()).days, 29, delta=1)
        self.assertNotIn(raw, key.hashed_key)
        self.assertIn("only once", err.getvalue())

    # -- 1.397 ---------------------------------------------------------
    def test_1_397_unknown_user_is_an_error(self) -> None:
        """
        Scenario 1.397: no key for a user that does not exist.
        Given: no user "cluster1aw_ghost"
        When: create_api_key runs for it
        Then: CommandError naming the user, and no key is stored.
        """
        with self.assertRaisesMessage(CommandError, "cluster1aw_ghost"):
            call_command("create_api_key", "--user", "cluster1aw_ghost", stdout=StringIO(), stderr=StringIO())
        self.assertFalse(LexAPIKey.objects.exists())
