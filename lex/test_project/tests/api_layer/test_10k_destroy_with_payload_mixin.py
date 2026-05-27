"""
Sub-cluster 10k — `DestroyOneWithPayloadMixin` permission branches +
`_unwrap_historical_instance` resolver.

Targets `lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py`
(35.71% baseline, ~80 missed lines). This mixin is the DELETE
endpoint half of the CRUD surface — it differs from DRF's stock
`DestroyModelMixin` in three customer-visible ways:

1. It returns the serialized payload of the deleted instance
   (DRF's default returns 204 with empty body). The frontend uses
   this to repopulate undo-toasts.
2. It bridges the legacy `can_delete(request)` check AND the modern
   `permission_delete(user_context)` check on the same call.
3. When the resolved object is a `HistoricalXxx` row (django-simple-history
   wrapper), it unwraps to the underlying `LexModel` so the
   permission method actually exists. Without unwrapping, every
   delete on a history row would deny by default — silently.

The BUG-008 fix is also pinned: an unauthenticated user must get
**401** (not 403, not 400) so the SPA can route to the login page.

All scenarios are `SimpleTestCase` — DRF's view-state plumbing is
mocked to keep the tests at <10ms each.

Scenarios 10.40 – 10.46.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import status

from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    DestroyOneWithPayloadMixin,
    _unwrap_historical_instance,
)

import pytest

pytestmark = pytest.mark.api_layer


def _build_view(instance, *, user_authenticated=True, serializer_data=None):
    """Build a minimal mixin instance with the DRF view contract.

    Returns a `DestroyOneWithPayloadMixin` subclass that supplies
    just enough of the DRF view surface (`get_object`, `get_serializer`,
    `request`, plus a fake `super().destroy`) for `destroy()` to
    run end-to-end.
    """
    captured = {"super_destroy_called": False}

    class _StubParent:
        def destroy(self, *args, **kwargs):
            captured["super_destroy_called"] = True

    class _View(DestroyOneWithPayloadMixin, _StubParent):
        pass

    view = _View()
    view.request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=user_authenticated),
    )
    view.get_object = MagicMock(return_value=instance)
    view.get_serializer = MagicMock(
        return_value=SimpleNamespace(data=serializer_data or {"id": 1}),
    )
    view._captured = captured
    return view


# ---------------------------------------------------------------------
# 10.40 — _unwrap_historical_instance
# ---------------------------------------------------------------------
class TestCluster10k_UnwrapHistorical(SimpleTestCase):
    """`_unwrap_historical_instance` four-branch resolver.

    A regression here either (a) fails to unwrap and silently
    denies every delete on a history row, or (b) unwraps too
    aggressively and lets a stale historical snapshot stand in
    for the live row during the permission check.
    """

    def test_10_40a_lexmodel_passthrough(self):
        """10.40a: instance with `permission_delete` is passed
        through; `original_instance` is None.

        Dominant happy path: every delete on a live LexModel takes
        this branch. Drift to "unwrap unconditionally" would change
        the type of `target` for every call.
        """
        instance = SimpleNamespace(permission_delete=lambda uc: True)

        target, original = _unwrap_historical_instance(instance)

        self.assertIs(target, instance)
        self.assertIsNone(original)

    def test_10_40b_history_object_then_instance_unwrap(self):
        """10.40b: Level-2 wrapper → Level-1 (`history_object`) →
        Main (`.instance`) chain.

        Mirrors `LexSerializer._unwrap_instance`. Pin the two-hop
        unwrap so a regression that stopped at Level-1 would
        evaluate `permission_delete` on the meta-wrapper (which
        lacks the method → permission check blows up → deny by
        default → silent loss of every history-row delete).
        """
        live = SimpleNamespace(
            permission_delete=lambda uc: True,
            pk=42,
        )
        history_row = SimpleNamespace(instance=live)
        meta_wrapper = SimpleNamespace(history_object=history_row)

        target, original = _unwrap_historical_instance(meta_wrapper)

        self.assertIs(target, live, "two-hop unwrap must reach the live row")
        self.assertIs(original, meta_wrapper)

    def test_10_40c_instance_type_reconstruct_fallback(self):
        """10.40c: when `.instance` is missing, reconstruct from
        `instance_type` + field attnames.

        Older history rows may not carry the FK-style `.instance`
        link; this fallback rebuilds a fresh model object from the
        column values so the permission check has SOMETHING to
        evaluate. Pin so a refactor that dropped the fallback
        would silently deny those legacy rows.
        """

        class _LiveModel:
            permission_delete = lambda self, uc: True

            class _meta:
                fields = [
                    SimpleNamespace(attname="pk"),
                    SimpleNamespace(attname="name"),
                ]

            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        # Historical row carries only the column values, no `.instance`.
        history_row = SimpleNamespace(
            instance_type=_LiveModel,
            pk=7,
            name="alpha",
        )

        target, original = _unwrap_historical_instance(history_row)

        self.assertIsInstance(target, _LiveModel)
        self.assertEqual(target.pk, 7)
        self.assertEqual(target.name, "alpha")
        self.assertIs(original, history_row)


# ---------------------------------------------------------------------
# 10.41 – 10.46 — DestroyOneWithPayloadMixin.destroy
# ---------------------------------------------------------------------
class TestCluster10k_DestroyMixin(SimpleTestCase):
    """`DestroyOneWithPayloadMixin.destroy` permission + payload branches."""

    # Note: `UserContext` is imported lazily inside `destroy()` via
    # `from lex.core.models.LexModel import UserContext`, so the patch
    # target is the source module, NOT the mixin module.
    UC_PATH = "lex.core.models.LexModel.UserContext"

    def test_10_41_modern_permission_delete_allows_returns_payload(self):
        """10.41: `permission_delete(user_context)` returns truthy →
        super().destroy is called and the serialized payload comes
        back with **200 OK** (NOT 204 — DRF's default).

        Customer-observable: the SPA's "Item deleted" toast and
        the undo flow rely on receiving the deleted row's data in
        the response body. Drift to 204+empty would silently break
        the toast.
        """
        instance = MagicMock()
        instance.permission_delete = MagicMock(return_value=True)
        instance.__class__.__name__ = "Invoice"

        view = _build_view(instance, serializer_data={"id": 99, "name": "x"})
        with patch(
            "lex.core.models.LexModel.UserContext"
        ) as MockUC:
            MockUC.from_request.return_value = SimpleNamespace(
                user=None, email=None, is_authenticated=True,
                is_superuser=False, groups=[], keycloak_scopes=set(),
                user_permissions=[], client_roles={},
            )
            resp = view.destroy()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {"id": 99, "name": "x"})
        self.assertTrue(
            view._captured["super_destroy_called"],
            "super().destroy must run only after the permission check passes",
        )

    def test_10_42_legacy_can_delete_path(self):
        """10.42: an instance with `can_delete(request)` (no
        `permission_delete`) takes the legacy branch.

        Back-compat for pre-`UserContext` models. Drift that started
        requiring `permission_delete` everywhere would 500 every
        legacy-model delete (no method → AttributeError → caught →
        default deny → silent regression).
        """
        instance = SimpleNamespace(
            can_delete=MagicMock(return_value=True),
        )
        # NOTE: no `permission_delete` attr.
        view = _build_view(instance)

        resp = view.destroy()

        instance.can_delete.assert_called_once_with(view.request)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(view._captured["super_destroy_called"])

    def test_10_43_no_permission_method_after_unwrap_denies(self):
        """10.43: instance with neither `permission_delete` nor
        `can_delete` → deny by default.

        Principle of least privilege: a model that forgot to declare
        either method must NOT be deletable through the API. Pin the
        deny-by-default so a refactor that flipped it to allow-by-default
        would silently expose every legacy model.
        """
        # Important: the unwrap helper passes through unchanged when
        # neither method exists (the `hasattr` short-circuit). That
        # means destroy() falls into the `else: deny` branch.
        instance = SimpleNamespace()  # no perm method at all
        # The unwrap branch we care about lives further down — patch
        # the helper to make sure target == instance.
        view = _build_view(instance)
        with patch(
            "lex.api.views.model_entries.mixins."
            "DestroyOneWithPayloadMixin._unwrap_historical_instance",
            return_value=(instance, None),
        ):
            resp = view.destroy()

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            view._captured["super_destroy_called"],
            "deny path must short-circuit before super().destroy()",
        )

    def test_10_44_permission_check_exception_denies_safely(self):
        """10.44: an exception inside `permission_delete` is caught
        and demoted to a deny — never a 500.

        Customer impact: a buggy permission_delete must not crash
        the whole DELETE endpoint; it must surface as a clean 403.
        Pin the `except Exception` swallow so a refactor that
        re-raised would surface every transient permission glitch
        as a 500 to end-users.
        """
        instance = MagicMock()
        instance.permission_delete = MagicMock(
            side_effect=RuntimeError("boom"),
        )
        instance.__class__.__name__ = "Invoice"
        view = _build_view(instance)

        with patch(
            "lex.core.models.LexModel.UserContext"
        ) as MockUC:
            MockUC.from_request.return_value = SimpleNamespace(
                user=None, email=None, is_authenticated=True,
                is_superuser=False, groups=[], keycloak_scopes=set(),
                user_permissions=[], client_roles={},
            )
            resp = view.destroy()

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(view._captured["super_destroy_called"])

    def test_10_45_anonymous_deny_returns_401_not_403(self):
        """10.45 (BUG-008): an anonymous user denied at the permission
        check gets **401 UNAUTHORIZED**, not 403.

        Documented BUG-008 fix in the source. The SPA's auth interceptor
        listens for 401 and routes to the login page; if we leak a
        403 to anonymous traffic, the user lands on a generic
        "Forbidden" toast and never sees the login flow.
        """
        instance = SimpleNamespace()  # no perm method → deny path
        view = _build_view(instance, user_authenticated=False)
        with patch(
            "lex.api.views.model_entries.mixins."
            "DestroyOneWithPayloadMixin._unwrap_historical_instance",
            return_value=(instance, None),
        ):
            resp = view.destroy()

        self.assertEqual(
            resp.status_code, status.HTTP_401_UNAUTHORIZED,
            "anonymous denials MUST surface as 401 — the SPA's "
            "auth interceptor depends on it (BUG-008)",
        )

    def test_10_46_unwrap_history_merges_extra_keycloak_scopes(self):
        """10.46: when the resolved object came from a historical
        wrapper, additional keycloak scopes resolved against the
        original resource are merged into the user_context before
        the permission check.

        Without the merge, deleting a history row would always run
        `permission_delete` with the LIVE model's scope set —
        history-specific scopes (e.g. `delete_invoice_history`)
        would never be visible, so any policy gated on them would
        silently deny.
        """
        live = MagicMock()
        live.permission_delete = MagicMock(return_value=True)
        live.__class__.__name__ = "Invoice"

        original_history_row = SimpleNamespace()
        view = _build_view(live)

        with patch(
            "lex.api.views.model_entries.mixins."
            "DestroyOneWithPayloadMixin._unwrap_historical_instance",
            return_value=(live, original_history_row),
        ), patch(
            "lex.core.models.LexModel.UserContext"
        ) as MockUC:
            base_uc = SimpleNamespace(
                user=None, email=None, is_authenticated=True,
                is_superuser=False, groups=[],
                keycloak_scopes={"baseline"},
                user_permissions=["p"], client_roles={},
            )
            MockUC.from_request.return_value = base_uc
            MockUC._resolve_keycloak_scopes.return_value = {"history_extra"}
            # The constructor (called by the merge branch) returns a
            # spy we can inspect.
            merged_holder = {}

            def _ctor(**kw):
                merged_holder["scopes"] = kw["keycloak_scopes"]
                return SimpleNamespace(**kw)

            MockUC.side_effect = _ctor

            resp = view.destroy()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(
            "history_extra", merged_holder["scopes"],
            "history-specific scope must be merged into the "
            "user_context before permission_delete is called",
        )
        self.assertIn(
            "baseline", merged_holder["scopes"],
            "merge must preserve baseline scopes — drift to 'replace' "
            "would strip every existing scope and break standard policies",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()






