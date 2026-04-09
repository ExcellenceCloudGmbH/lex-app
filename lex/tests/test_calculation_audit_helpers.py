"""
Unit tests for pure helper functions in
``lex.audit_logging.utils.calculation_audit``.

**What this tests (customer-visible behaviour)**

``_same_model_instance`` compares two model instances by label + PK.
``_resolve_context_data`` normalises a context argument into a dict.
``_resolve_actor`` extracts the user/actor from the operation context.

**Why it matters**

If ``_same_model_instance`` gives a false positive, nested
calculations short-circuit the audit trail and the root-level
audit record is never created.

**Methodology**

Pure functions tested with mocks — no DB.

Run::

    python manage.py test lex.tests.test_calculation_audit_helpers
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from lex.audit_logging.utils.calculation_audit import (
    _same_model_instance,
    _resolve_context_data,
    _resolve_actor,
)


class TestSameModelInstance(SimpleTestCase):
    """Prove ``_same_model_instance`` compares label + pk correctly."""

    def _make_instance(self, label, pk):
        inst = MagicMock()
        inst._meta.label_lower = label
        inst.pk = pk
        return inst

    def test_same_label_and_pk(self):
        a = self._make_instance("app.invoice", 42)
        b = self._make_instance("app.invoice", 42)
        self.assertTrue(_same_model_instance(a, b))

    def test_different_pk(self):
        a = self._make_instance("app.invoice", 42)
        b = self._make_instance("app.invoice", 99)
        self.assertFalse(_same_model_instance(a, b))

    def test_different_label(self):
        a = self._make_instance("app.invoice", 42)
        b = self._make_instance("app.period", 42)
        self.assertFalse(_same_model_instance(a, b))

    def test_left_none(self):
        b = self._make_instance("app.invoice", 1)
        self.assertFalse(_same_model_instance(None, b))

    def test_right_none(self):
        a = self._make_instance("app.invoice", 1)
        self.assertFalse(_same_model_instance(a, None))

    def test_both_none(self):
        self.assertFalse(_same_model_instance(None, None))

    def test_no_meta_on_left(self):
        a = MagicMock(spec=[])  # no _meta
        b = self._make_instance("app.invoice", 1)
        self.assertFalse(_same_model_instance(a, b))

    def test_no_meta_on_right(self):
        a = self._make_instance("app.invoice", 1)
        b = MagicMock(spec=[])  # no _meta
        self.assertFalse(_same_model_instance(a, b))


class TestResolveContextData(SimpleTestCase):
    """Prove ``_resolve_context_data`` normalises to a dict."""

    def test_dict_passthrough(self):
        d = {"calculation_id": "c1"}
        self.assertIs(_resolve_context_data(d), d)

    def test_none_returns_dict(self):
        result = _resolve_context_data(None)
        self.assertIsInstance(result, dict)

    def test_non_dict_returns_empty(self):
        result = _resolve_context_data("not_a_dict")
        self.assertIsInstance(result, dict)


class TestResolveActor(SimpleTestCase):
    """Prove ``_resolve_actor`` resolves user from multiple sources."""

    def test_explicit_actor(self):
        self.assertEqual(_resolve_actor({"actor": "admin"}), "admin")

    def test_request_obj_dict_with_user(self):
        ctx = {"request_obj": {"user": "jane"}}
        self.assertEqual(_resolve_actor(ctx), "jane")

    def test_request_obj_with_user_attr(self):
        request = MagicMock()
        request.user = "bob"
        ctx = {"request_obj": request}
        self.assertEqual(_resolve_actor(ctx), "bob")

    def test_fallback_to_system(self):
        self.assertEqual(_resolve_actor({}), "system")

    def test_none_context_returns_system(self):
        self.assertEqual(_resolve_actor(None), "system")

    def test_actor_takes_precedence(self):
        request = MagicMock()
        request.user = "bob"
        ctx = {"actor": "admin", "request_obj": request}
        self.assertEqual(_resolve_actor(ctx), "admin")
