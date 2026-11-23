"""lex.ui: the guard on direct database access.

Intent: lex-app's API applies hidden fields and row rules; the ORM does not. So a handler body
may query the database only when the handler declared what it requires -- otherwise the first
query is refused with a message naming the handler. With a declared requirement, the user's
Keycloak grants are checked (model-wide grants only, as everywhere in lex-app) and ORM writes
record the signed-in user as created_by / edited_by. A regression is a dashboard that shows rows
the grid would hide, or audit fields naming nobody.

Cluster 1cd -- scenarios 1.750-1.752. Type: I.
Covers: lex/lex_app/reflex/ui/guard.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cd_ui_guard.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.test import TestCase

from lex.lex_app.reflex.ui import guard

pytestmark = pytest.mark.init


class TestCluster01cd_Guard(TestCase):
    """1.750-1.752."""

    # -- 1.750 ---------------------------------------------------------
    def test_1_750_requires_values_and_the_grant_check(self) -> None:
        """
        Scenario 1.750: what requires= means.
        Given: Keycloak grants: a model-wide read on auth.Group, and an edit scoped to one record.
        When:  requirements are normalised and checked against them.
        Then:  nothing declared is None; one value or a list is a tuple; ui.read(Group) holds;
               the record-scoped edit does not open the model; a list needs all; ui.signed_in
               always holds; a named resource works like a model.
        """
        grants = [{"rsname": "auth.Group", "scopes": ["read"]},
                  {"rsname": "auth.Group", "scopes": ["edit"], "resource_set_id": "7"}]
        self.assertIsNone(guard.normalise(None), "nothing declared")
        self.assertEqual(guard.normalise(guard.read(Group)), (guard.Requirement(Group, "read"),))
        self.assertEqual(guard.read(Group).resource, "auth.Group")
        self.assertTrue(guard.granted(guard.normalise(guard.read(Group)), grants), "a model-wide grant holds")
        self.assertFalse(guard.granted(guard.normalise(guard.edit(Group)), grants), "a record-scoped grant does not")
        self.assertFalse(guard.granted(guard.normalise([guard.read(Group), guard.delete(Group)]), grants), "a list needs all")
        self.assertTrue(guard.granted(guard.normalise(guard.signed_in), []), "signed_in needs no grant")
        self.assertTrue(guard.granted(guard.normalise(guard.permission("auth.Group", "read")), grants))
        self.assertEqual({guard.create(Group).scope, guard.delete(Group).scope}, {"create", "delete"})
        with self.assertRaises(TypeError, msg="anything else is refused"):
            guard.normalise("auth.Group:read")

    # -- 1.751 ---------------------------------------------------------
    def test_1_751_queries_are_refused_inside_the_block_and_work_after_it(self) -> None:
        """
        Scenario 1.751: an unguarded handler cannot query the database.
        Given: a model with a row.
        When:  it is queried inside block_queries, then after it.
        Then:  inside, the query raises UnguardedQuery naming the handler and the two ways out
               (self.api, requires=); after the block, queries work again.
        """
        Group.objects.create(name="alpha")
        with guard.block_queries("Tickets.load_totals"):
            with self.assertRaises(guard.UnguardedQuery) as raised:
                Group.objects.count()
        text = str(raised.exception)
        for expected in ("Tickets.load_totals", "self.api", "requires="):
            self.assertIn(expected, text, f"the error must mention {expected}")
        self.assertEqual(Group.objects.count(), 1, "queries work again after the block")

    # -- 1.752 ---------------------------------------------------------
    def test_1_752_the_acting_user_is_set_for_audit_fields(self) -> None:
        """
        Scenario 1.752: ORM writes in a handler name the signed-in user.
        Given: a signed-in user with a username and an e-mail.
        When:  code runs inside acting_user.
        Then:  lex-app's operation context carries a request whose user is authenticated and
               reads as the username (the e-mail when there is none); it is restored afterwards.
        """
        from lex.api.utils import operation_context

        before = operation_context.get()
        with guard.acting_user(SimpleNamespace(username="anna", email="anna@example.test")):
            request = operation_context.get()["request_obj"]
            self.assertTrue(request.user.is_authenticated)
            self.assertEqual(str(request.user), "anna")
        with guard.acting_user(SimpleNamespace(username="", email="anna@example.test")):
            self.assertEqual(str(operation_context.get()["request_obj"].user), "anna@example.test")
        self.assertEqual(operation_context.get(), before, "the context is restored")
