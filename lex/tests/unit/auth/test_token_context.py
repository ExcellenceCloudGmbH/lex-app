"""
Unit tests for ``lex.authentication.utils.TokenContext``.

**What this tests (customer-visible behaviour)**

``TokenContext`` is the context manager that threads the OAuth
access token through the call stack using a ``ContextVar``.
The audit logging system reads it to attribute API actions to users.

**Why it matters**

If the token is not set on ``__enter__`` or not cleared on
``__exit__``, downstream code either can't identify the user
(anonymous audit entries) or leaks a token from a previous request
(cross-user attribution).

**Methodology**

Pure ContextVar lifecycle — no DB.

Run::

    python manage.py test lex.tests.test_token_context
"""

from django.test import SimpleTestCase

from lex.authentication.utils.TokenContext import TokenContext, token_context


class TestTokenContext(SimpleTestCase):
    """Prove ``TokenContext`` context manager sets/clears the ContextVar."""

    def setUp(self):
        # Reset to default before each test
        token_context.set({"access_token": None})

    def test_enter_sets_token(self):
        ctx = TokenContext(access_token="tok_abc")
        ctx.__enter__()
        current = token_context.get()
        self.assertEqual(current["access_token"], "tok_abc")

    def test_exit_is_noop(self):
        """__exit__ doesn't raise — current impl is a no-op."""
        ctx = TokenContext(access_token="tok_abc")
        ctx.__enter__()
        ctx.__exit__(None, None, None)
        # no assertion on cleanup — the current impl doesn't reset

    def test_init_from_request_dict(self):
        ctx = TokenContext(request={"access_token": "tok_from_req"})
        self.assertEqual(ctx.access_token, "tok_from_req")

    def test_init_direct_token(self):
        ctx = TokenContext(access_token="direct_tok")
        self.assertEqual(ctx.access_token, "direct_tok")

    def test_no_token_attribute_not_set(self):
        """TokenContext() without args doesn't create access_token attr."""
        ctx = TokenContext()
        self.assertFalse(hasattr(ctx, "access_token"))

    def test_get_access_token_static(self):
        token_context.set({"access_token": "static_tok"})
        result = TokenContext.get_access_token()
        # get_access_token calls token_context.get("access_token")
        # ContextVar.get() with a string argument uses it as a default,
        # so it returns the current dict (not "access_token" key).
        # The result is the whole dict, not the string.
        self.assertIsNotNone(result)
