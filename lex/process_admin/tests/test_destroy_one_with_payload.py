"""
Tests for lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin
======================================================================

This mixin overrides DRF's default destroy() to:
1. Check delete permissions (new-style ``permission_delete`` or legacy ``can_delete``)
2. Unwrap historical instances so permission checks work on the real LexModel
3. Return the serialized deleted instance in the response (HTTP 200, not 204)

Also tests ``_unwrap_historical_instance()`` — the helper that resolves
HistoricalXxx → LexModel for permission checking.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    DestroyOneWithPayloadMixin,
    _unwrap_historical_instance,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_lex_instance(*, can_delete=True, use_new_style=True, pk=1):
    """Create an instance with permission_delete or can_delete."""
    instance = MagicMock()
    instance.pk = pk
    instance.__class__ = type("Investment", (), {})
    instance.__class__.__name__ = "Investment"

    if use_new_style:
        instance.permission_delete = MagicMock(return_value=can_delete)
    else:
        # Remove permission_delete so hasattr returns False
        del instance.permission_delete
        instance.can_delete = MagicMock(return_value=can_delete)

    return instance


def _make_historical_instance(*, has_history_object=False, has_instance_fk=False):
    """Create a mock historical instance that might need unwrapping."""
    hist = MagicMock(spec=[])
    hist.__class__ = type("HistoricalInvestment", (), {})
    hist.__class__.__name__ = "HistoricalInvestment"
    hist.pk = 99

    if has_history_object:
        # Meta-level: has history_object pointing to Level 1
        level1 = MagicMock()
        if has_instance_fk:
            main = MagicMock()
            main.permission_delete = MagicMock(return_value=True)
            level1.instance = main
        else:
            level1.instance = None
        hist.history_object = level1
    else:
        hist.history_object = None

    return hist


class _FakeDestroyView(DestroyOneWithPayloadMixin):
    """Minimal view that mixes in the destroy mixin."""

    def __init__(self, *, instance, request, serializer_data=None):
        self._instance = instance
        self.request = request
        self._serializer_data = serializer_data or {"id": instance.pk}

    def get_object(self):
        return self._instance

    def get_serializer(self, obj):
        ser = MagicMock()
        ser.data = self._serializer_data
        return ser


class _FakeParent:
    """Stand-in parent that provides a no-op destroy for super() chain."""
    def destroy(self, *args, **kwargs):
        pass


class _DestroyViewWithParent(DestroyOneWithPayloadMixin, _FakeParent):
    """View with a real parent destroy so super().destroy() works."""

    def __init__(self, *, instance, request, serializer_data=None):
        self._instance = instance
        self.request = request
        self._serializer_data = serializer_data or {"id": instance.pk}

    def get_object(self):
        return self._instance

    def get_serializer(self, obj):
        ser = MagicMock()
        ser.data = self._serializer_data
        return ser


# ═══════════════════════════════════════════════════════════════════════════
# 1. _unwrap_historical_instance
# ═══════════════════════════════════════════════════════════════════════════
class UnwrapHistoricalInstanceTests(SimpleTestCase):
    """Test the helper that resolves HistoricalXxx → LexModel."""

    def test_lex_model_with_permission_delete_not_unwrapped(self):
        """An instance with permission_delete needs no unwrapping."""
        instance = MagicMock()
        instance.permission_delete = MagicMock()
        target, original = _unwrap_historical_instance(instance)
        self.assertIs(target, instance)
        self.assertIsNone(original)

    def test_lex_model_with_can_delete_not_unwrapped(self):
        """An instance with can_delete needs no unwrapping."""
        instance = MagicMock()
        del instance.permission_delete
        instance.can_delete = MagicMock()
        target, original = _unwrap_historical_instance(instance)
        self.assertIs(target, instance)
        self.assertIsNone(original)

    def test_historical_unwrapped_via_history_object_and_instance(self):
        """HistoricalXxx → history_object → instance → real LexModel."""
        main_model = MagicMock()
        main_model.permission_delete = MagicMock(return_value=True)

        level1 = MagicMock()
        level1.instance = main_model

        hist = MagicMock(spec=[])  # no permission_delete or can_delete
        hist.history_object = level1

        target, original = _unwrap_historical_instance(hist)
        self.assertIs(target, main_model)
        self.assertIs(original, hist)

    def test_unwrap_fails_returns_original(self):
        """If unwrapping fails completely, returns the original instance."""
        hist = MagicMock(spec=[])
        hist.history_object = None  # no unwrap path
        # Also ensure no 'instance' attr
        del hist.instance
        # Ensure no instance_type
        del hist.instance_type

        target, original = _unwrap_historical_instance(hist)
        self.assertIs(target, hist)
        self.assertIsNone(original)

    def test_unwrap_via_instance_fk_directly(self):
        """Level 1 historical with direct .instance FK → unwraps to main."""
        main = MagicMock()
        main.permission_delete = MagicMock()

        hist = MagicMock(spec=[])
        hist.history_object = None
        hist.instance = main

        target, original = _unwrap_historical_instance(hist)
        self.assertIs(target, main)
        self.assertIs(original, hist)


# ═══════════════════════════════════════════════════════════════════════════
# 2. destroy() — new-style permission_delete
# ═══════════════════════════════════════════════════════════════════════════
class DestroyPermissionDeleteTests(SimpleTestCase):
    """Test permission_delete (new system) enforcement in destroy()."""

    @patch("lex.core.models.LexModel.UserContext")
    def test_permission_delete_granted(self, MockUC):
        """When permission_delete returns True, the record is deleted."""
        instance = _make_lex_instance(can_delete=True, use_new_style=True)
        request = MagicMock()
        MockUC.from_request.return_value = MagicMock()

        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], instance.pk)

    @patch("lex.core.models.LexModel.UserContext")
    def test_permission_delete_denied(self, MockUC):
        """When permission_delete returns False, HTTP 400 with message."""
        instance = _make_lex_instance(can_delete=False, use_new_style=True)
        request = MagicMock()
        MockUC.from_request.return_value = MagicMock()

        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("not authorized", resp.data["message"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. destroy() — legacy can_delete
# ═══════════════════════════════════════════════════════════════════════════
class DestroyCanDeleteTests(SimpleTestCase):
    """Test legacy can_delete fallback."""

    def test_can_delete_granted(self):
        instance = _make_lex_instance(can_delete=True, use_new_style=False)
        request = MagicMock()

        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 200)

    def test_can_delete_denied(self):
        instance = _make_lex_instance(can_delete=False, use_new_style=False)
        request = MagicMock()

        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("not authorized", resp.data["message"])


# ═══════════════════════════════════════════════════════════════════════════
# 4. destroy() — permission check error → deny by default
# ═══════════════════════════════════════════════════════════════════════════
class DestroyPermissionErrorTests(SimpleTestCase):
    """If permission check itself raises, deny by default."""

    @patch("lex.core.models.LexModel.UserContext")
    def test_permission_exception_denies(self, MockUC):
        """Exception during permission check → HTTP 400 (principle of least privilege)."""
        instance = MagicMock()
        instance.permission_delete = MagicMock(side_effect=RuntimeError("kaboom"))
        instance.__class__.__name__ = "Investment"
        instance.pk = 5
        MockUC.from_request.return_value = MagicMock()

        request = MagicMock()
        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("not authorized", resp.data["message"])


# ═══════════════════════════════════════════════════════════════════════════
# 5. destroy() — no permission method at all → deny
# ═══════════════════════════════════════════════════════════════════════════
class DestroyNoPermissionMethodTests(SimpleTestCase):
    """Instance without permission_delete or can_delete → denied."""

    def test_no_methods_denies(self):
        instance = MagicMock(spec=[])  # no permission methods
        instance.pk = 1
        instance.__class__ = type("Bare", (), {})
        instance.__class__.__name__ = "Bare"

        request = MagicMock()
        view = _DestroyViewWithParent(instance=instance, request=request)
        resp = view.destroy()

        self.assertEqual(resp.status_code, 400)


# ═══════════════════════════════════════════════════════════════════════════
# 6. destroy() — returns serialized data (not 204 No Content)
# ═══════════════════════════════════════════════════════════════════════════
class DestroyResponseContractTests(SimpleTestCase):
    """Verify the response includes the serialized deleted instance."""

    def test_returns_http_200_not_204(self):
        """The mixin's contract: return 200 with payload, not 204 No Content."""
        instance = _make_lex_instance(can_delete=True, use_new_style=False)
        request = MagicMock()
        view = _DestroyViewWithParent(
            instance=instance, request=request,
            serializer_data={"id": 1, "name": "Investment A"},
        )
        resp = view.destroy()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Investment A")


if __name__ == "__main__":
    unittest.main()
