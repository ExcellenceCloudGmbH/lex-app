"""
Tests for ``DestroyOneWithPayloadMixin`` — delete authorization and response.

**What is tested:**

    * ``_unwrap_historical_instance()`` — resolves HistoricalXxx / MetaHistory
      records back to the original LexModel so permission_delete can be checked.
    * ``destroy()`` — permission_delete (new system), can_delete (legacy),
      no-permission-method denial, exception-during-check denial, successful
      deletion returns 200 with serialized data.

**Why this matters:**

    Every row-level delete in the AG Grid views goes through this mixin.
    If ``_unwrap_historical_instance`` fails to resolve to the real model,
    permission_delete is never called and the user either gets a false 400
    (blocking legitimate deletes) or a false 200 (unauthorized deletion).
    If the exception path doesn't deny by default, any transient error in
    the permission system becomes a security bypass.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_destroy_mixin --verbosity=2 --noinput
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    _unwrap_historical_instance,
    DestroyOneWithPayloadMixin,
)


# ─── helpers ──────────────────────────────────────────────────────────
# Use proper dynamic classes — SimpleNamespace can't have __class__ reassigned.

def _make_lex_instance(pk=1, class_name="CashFlow"):
    """LexModel stub with permission_delete and can_delete."""
    cls = type(class_name, (), {
        "permission_delete": lambda self, ctx: True,
        "can_delete": lambda self, req: True,
    })
    inst = cls()
    inst.pk = pk
    return inst


def _make_historical(pk=1, class_name="HistoricalCashFlow", **attrs):
    """Historical record stub — no permission methods, just data attributes."""
    cls = type(class_name, (), {})
    inst = cls()
    inst.pk = pk
    for k, v in attrs.items():
        setattr(inst, k, v)
    return inst


def _make_bare(pk=1, class_name="BareRecord"):
    """Bare record — no permission methods, no unwrap hooks."""
    cls = type(class_name, (), {})
    inst = cls()
    inst.pk = pk
    return inst


# ════════════════════════════════════════════════════════════════════════
#  _unwrap_historical_instance
# ════════════════════════════════════════════════════════════════════════

class TestUnwrapHistoricalInstance(SimpleTestCase):
    """Verify historical record unwrapping for permission checks."""

    def test_lexmodel_returns_self_no_unwrap(self):
        """Instance with permission_delete → no unwrap needed."""
        inst = _make_lex_instance(pk=5)
        target, original = _unwrap_historical_instance(inst)

        self.assertIs(target, inst)
        self.assertIsNone(original)

    def test_can_delete_instance_returns_self(self):
        """Instance with can_delete (legacy) → no unwrap needed."""
        cls = type("OldModel", (), {"can_delete": lambda self, req: True})
        inst = cls()
        inst.pk = 1

        target, original = _unwrap_historical_instance(inst)
        self.assertIs(target, inst)
        self.assertIsNone(original)

    def test_historical_with_instance_fk_unwraps(self):
        """HistoricalXxx with .instance FK → unwraps to the real model."""
        real_model = _make_lex_instance(pk=10)
        hist = _make_historical(pk=99, instance=real_model)

        target, original = _unwrap_historical_instance(hist)

        self.assertIs(target, real_model)
        self.assertIs(original, hist)

    def test_meta_history_unwraps_via_history_object(self):
        """MetaHistory with history_object → Level 2 → Level 1 → Level 0."""
        real_model = _make_lex_instance(pk=10)
        # Level 1 record with .instance FK
        history_record = _make_historical(pk=50, class_name="HistCF", instance=real_model)
        # Level 2 record (meta-history) with .history_object
        meta_record = _make_historical(pk=200, class_name="MetaHistCF", history_object=history_record)

        target, original = _unwrap_historical_instance(meta_record)

        self.assertIs(target, real_model)
        self.assertIs(original, meta_record)

    def test_instance_type_fallback_reconstructs_model(self):
        """When .instance FK is absent, reconstructs via instance_type."""
        ModelClass = type("CashFlow", (), {
            "permission_delete": lambda self, ctx: True,
        })
        from types import SimpleNamespace
        ModelClass._meta = SimpleNamespace(fields=[])

        hist = _make_historical(pk=99, instance_type=ModelClass)

        target, original = _unwrap_historical_instance(hist)

        self.assertIs(original, hist)
        self.assertTrue(hasattr(target, "permission_delete"))

    def test_unwrap_failure_returns_original(self):
        """When all unwrap attempts fail, returns (original, None)."""
        bare = _make_bare(pk=1)

        target, original = _unwrap_historical_instance(bare)

        self.assertIs(target, bare)
        self.assertIsNone(original)

    def test_history_object_none_skips_meta_unwrap(self):
        """history_object=None → meta unwrap is skipped, falls through."""
        real_model = _make_lex_instance(pk=10)
        hist = _make_historical(pk=99, history_object=None, instance=real_model)

        target, original = _unwrap_historical_instance(hist)

        # history_object is falsy, so meta unwrap skipped; but .instance FK works
        self.assertIs(target, real_model)


# ════════════════════════════════════════════════════════════════════════
#  destroy() — permission outcomes
# ════════════════════════════════════════════════════════════════════════

class _FakeParentView:
    """Stand-in for the DRF parent class whose destroy() actually deletes."""

    def destroy(self, *args, **kwargs):
        pass  # no-op — we don't want real DB deletion


class _TestableDestroyView(DestroyOneWithPayloadMixin, _FakeParentView):
    """Concrete view so super().destroy() resolves to _FakeParentView.destroy."""
    pass


class TestDestroyPermission(SimpleTestCase):
    """Verify destroy() authorization paths and response codes."""

    def _call_destroy(self, target_instance, original_instance=None):
        """Exercise destroy() and return the Response."""
        view = _TestableDestroyView()
        view.get_object = MagicMock(return_value=target_instance)
        view.request = MagicMock()
        view.get_serializer = MagicMock(
            return_value=MagicMock(data={"id": getattr(target_instance, "pk", 1)})
        )

        with patch(
            "lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin._unwrap_historical_instance",
            return_value=(target_instance, original_instance),
        ):
            with patch(
                "lex.core.models.LexModel.UserContext.from_request",
                return_value=MagicMock(),
            ):
                return view.destroy()

    def test_permission_delete_allowed_returns_200(self):
        """permission_delete returns True → 200 with serialized data."""
        inst = MagicMock(pk=1)
        inst.__class__ = type("CashFlow", (), {})
        inst.permission_delete.return_value = True

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], 1)

    def test_permission_delete_denied_returns_400(self):
        """permission_delete returns False → 400 with error message."""
        inst = MagicMock(pk=1)
        inst.__class__ = type("Bond", (), {})
        inst.permission_delete.return_value = False

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("not authorized", resp.data["message"].lower())

    def test_legacy_can_delete_denied_returns_400(self):
        """Legacy can_delete returns False → 400."""
        inst = MagicMock(spec=[])
        inst.pk = 1
        inst.__class__ = type("LegacyModel", (), {})
        inst.can_delete = MagicMock(return_value=False)

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 400)

    def test_no_permission_method_denies(self):
        """Instance with neither permission_delete nor can_delete → 400."""
        inst = _make_bare(pk=1, class_name="PlainRecord")

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 400)

    def test_permission_check_exception_denies(self):
        """Exception during permission check → denied (least privilege)."""
        inst = MagicMock(pk=1)
        inst.__class__ = type("ErrorModel", (), {})
        inst.permission_delete.side_effect = RuntimeError("keycloak down")

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 400)

    def test_legacy_can_delete_allowed_returns_200(self):
        """Legacy can_delete returns True → 200."""
        inst = MagicMock(spec=[])
        inst.pk = 5
        inst.__class__ = type("LegacyAllowed", (), {})
        inst.can_delete = MagicMock(return_value=True)

        resp = self._call_destroy(inst)

        self.assertEqual(resp.status_code, 200)
