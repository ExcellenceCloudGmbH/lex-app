"""
Tests for lex.api.views.model_entries.One.OneModelEntry
=======================================================

OneModelEntry is the single-record CRUD backbone of the LEX API.  It handles
create, update (standard + bitemporal + calculation-trigger), and destroy via
a complex permission/transaction/state-machine workflow.

Tests are organised by the *behaviour* they verify, not by method.
"""

import copy
import os
import sys
import types
import traceback
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch, call

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response

from lex.api.views.model_entries.One import OneModelEntry


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_view(*, kwargs=None, request=None):
    """Create a OneModelEntry with pre-wired kwargs, request and mocks."""
    view = OneModelEntry()
    view.kwargs = kwargs or {}
    view.request = request or MagicMock()
    view.format_kwarg = None  # required by DRF internals
    return view


def _mock_model_container(*, model_class=None, pk_name="pk"):
    mc = MagicMock()
    mc.model_class = model_class or MagicMock
    mc.pk_name = pk_name
    return mc


def _make_request(*, data=None, user=None, query_params=None):
    req = MagicMock()
    req.data = data or {}
    req.user = user or MagicMock()
    req.query_params = query_params or {}
    return req


# ═══════════════════════════════════════════════════════════════════════════
# 1. _prepare_update_request
# ═══════════════════════════════════════════════════════════════════════════
class PrepareUpdateRequestTests(SimpleTestCase):
    """Unit-level tests for the payload-sanitisation helper."""

    def test_calculate_key_is_stripped(self):
        """The ``calculate`` flag must never reach the serializer."""
        view = _build_view()
        request = _make_request(data={"name": "Alice", "calculate": "true"})
        result = view._prepare_update_request(request)
        self.assertNotIn("calculate", result._data)
        self.assertEqual(result._data["name"], "Alice")

    def test_reset_is_calculated_injected(self):
        """When reset_is_calculated=True the NOT_CALCULATED sentinel is added."""
        from lex.core.models.CalculationModel import CalculationModel

        view = _build_view()
        request = _make_request(data={"x": 1, "calculate": "true"})
        result = view._prepare_update_request(request, reset_is_calculated=True)
        self.assertEqual(result._data["is_calculated"], CalculationModel.NOT_CALCULATED)
        self.assertNotIn("calculate", result._data)

    def test_no_reset_leaves_is_calculated_absent(self):
        view = _build_view()
        request = _make_request(data={"x": 1})
        result = view._prepare_update_request(request, reset_is_calculated=False)
        self.assertNotIn("is_calculated", result._data)

    def test_full_data_also_set(self):
        """Both _data and _full_data must be updated so DRF sees the cleaned payload."""
        view = _build_view()
        request = _make_request(data={"a": "b", "calculate": "false"})
        result = view._prepare_update_request(request)
        self.assertEqual(result._data, result._full_data)

    def test_request_without_data_attribute(self):
        """If request has no .data (edge-case), should default to empty dict."""
        view = _build_view()
        request = MagicMock(spec=[])  # no attributes at all
        result = view._prepare_update_request(request)
        self.assertEqual(result._data, {})


# ═══════════════════════════════════════════════════════════════════════════
# 2. _reset_instance_is_calculated
# ═══════════════════════════════════════════════════════════════════════════
class ResetIsCalculatedTests(SimpleTestCase):
    """Tests for the state-reset helper that fires after non-calculate updates."""

    @patch.object(OneModelEntry, "get_object")
    def test_non_calculation_model_returns_response_unchanged(self, mock_get_obj):
        """For plain models the response must pass through unmodified."""
        mock_get_obj.return_value = MagicMock(spec=[])  # not a CalculationModel
        view = _build_view()
        original_resp = Response({"foo": "bar"})
        result = view._reset_instance_is_calculated(original_resp)
        self.assertIs(result, original_resp)

    @patch.object(OneModelEntry, "get_object")
    def test_calculation_model_already_not_calculated(self, mock_get_obj):
        """If already NOT_CALCULATED, save(skip_hooks=True) should NOT be called."""
        from lex.core.models.CalculationModel import CalculationModel

        instance = MagicMock(spec=CalculationModel)
        instance.is_calculated = CalculationModel.NOT_CALCULATED
        mock_get_obj.return_value = instance
        view = _build_view()
        resp = Response({"is_calculated": 99})
        view._reset_instance_is_calculated(resp)
        instance.save.assert_not_called()

    @patch.object(OneModelEntry, "get_object")
    def test_calculation_model_resets_and_saves(self, mock_get_obj):
        """An IN_PROGRESS instance must be reset to NOT_CALCULATED and saved."""
        from lex.core.models.CalculationModel import CalculationModel

        instance = MagicMock(spec=CalculationModel)
        instance.is_calculated = CalculationModel.IN_PROGRESS
        mock_get_obj.return_value = instance
        view = _build_view()
        resp = Response({"is_calculated": CalculationModel.IN_PROGRESS})
        result = view._reset_instance_is_calculated(resp)
        instance.save.assert_called_once_with(skip_hooks=True)
        self.assertEqual(result.data["is_calculated"], CalculationModel.NOT_CALCULATED)

    @patch.object(OneModelEntry, "get_object")
    def test_response_data_not_dict_leaves_data_alone(self, mock_get_obj):
        """If response.data is a list or None, only the instance is reset."""
        from lex.core.models.CalculationModel import CalculationModel

        instance = MagicMock(spec=CalculationModel)
        instance.is_calculated = CalculationModel.IN_PROGRESS
        mock_get_obj.return_value = instance
        view = _build_view()
        resp = Response([1, 2, 3])
        result = view._reset_instance_is_calculated(resp)
        self.assertEqual(result.data, [1, 2, 3])  # list unchanged


# ═══════════════════════════════════════════════════════════════════════════
# 3. create() — permission enforcement
# ═══════════════════════════════════════════════════════════════════════════
class CreatePermissionTests(SimpleTestCase):
    """Verify both new-style and legacy permission paths in create()."""

    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch("lex.api.views.model_entries.One.should_use_atomic_model_operations", return_value=False)
    @patch("lex.api.views.model_entries.One.CreateModelMixin.create")
    def test_permission_create_denied_returns_400(self, mock_create, mock_atomic, mock_ctx):
        """New-style permission_create returning False → HTTP 400."""
        model_cls = MagicMock()
        instance = MagicMock()
        instance.permission_create = MagicMock(return_value=False)
        model_cls.return_value = instance
        model_cls.__name__ = "Investment"

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request()
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        with patch("lex.core.models.LexModel.UserContext") as MockUC:
            MockUC.from_request.return_value = MagicMock()
            resp = view.create(request)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not authorized", resp.data["message"])
        mock_create.assert_not_called()

    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch("lex.api.views.model_entries.One.should_use_atomic_model_operations", return_value=False)
    @patch("lex.api.views.model_entries.One.CreateModelMixin.create")
    def test_legacy_can_create_denied_returns_400(self, mock_create, mock_atomic, mock_ctx):
        """Legacy can_create returning False → HTTP 400."""
        model_cls = MagicMock()
        instance = MagicMock(spec=[])  # no permission_create
        instance.can_create = MagicMock(return_value=False)
        model_cls.return_value = instance
        model_cls.__name__ = "Portfolio"

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request()
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)
        resp = view.create(request)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not authorized", resp.data["message"])

    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch("lex.api.views.model_entries.One.should_use_atomic_model_operations", return_value=False)
    @patch("lex.api.views.model_entries.One.CreateModelMixin.create")
    def test_permission_check_exception_allows_creation(self, mock_create, mock_atomic, mock_ctx):
        """If the permission check itself throws, creation proceeds (pass)."""
        model_cls = MagicMock()
        instance = MagicMock()
        instance.permission_create = MagicMock(side_effect=RuntimeError("boom"))
        model_cls.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request()
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_create.return_value = Response({"id": 1}, status=201)
        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("lex.core.models.LexModel.UserContext") as MockUC:
            MockUC.from_request.side_effect = RuntimeError("boom")
            resp = view.create(request)

        # Creation went ahead despite the exception
        mock_create.assert_called_once()

    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch("lex.api.views.model_entries.One.should_use_atomic_model_operations", return_value=True)
    @patch("lex.api.views.model_entries.One.transaction.atomic")
    @patch("lex.api.views.model_entries.One.CreateModelMixin.create")
    def test_create_uses_atomic_when_enabled(self, mock_create, mock_atomic, mock_should, mock_ctx):
        """When should_use_atomic_model_operations is True, transaction.atomic is used."""
        model_cls = MagicMock()
        instance = MagicMock(spec=[])
        instance.can_create = MagicMock(return_value=True)
        model_cls.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request()
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_create.return_value = Response({"id": 1}, status=201)
        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        view.create(request)
        mock_atomic.assert_called_once()

    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch("lex.api.views.model_entries.One.should_use_atomic_model_operations", return_value=False)
    @patch("lex.api.views.model_entries.One.CreateModelMixin.create", side_effect=ValueError("DB error"))
    def test_create_wraps_exception_in_api_exception(self, mock_create, mock_atomic, mock_ctx):
        """Any exception during creation is wrapped in an APIException."""
        model_cls = MagicMock()
        instance = MagicMock(spec=[])
        instance.can_create = MagicMock(return_value=True)
        model_cls.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request()
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with self.assertRaises(APIException) as cm:
            view.create(request)
        self.assertIn("DB error", str(cm.exception.detail))


# ═══════════════════════════════════════════════════════════════════════════
# 4. update() — meta-history guard
# ═══════════════════════════════════════════════════════════════════════════
class UpdateMetaHistoryGuardTests(SimpleTestCase):
    """Models with meta_history_id must never be modified."""

    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_meta_history_raises_permission_denied(self, mock_get_obj, mock_ctx, mock_log_ctx):
        model_cls = MagicMock()
        model_cls.meta_history_id = True  # has meta_history_id
        model_cls.valid_from = True
        model_cls.history_id = True

        instance = MagicMock()
        mock_get_obj.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"name": "hack"})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with self.assertRaises(PermissionDenied) as cm:
            view.update(request)
        self.assertIn("Meta-History", str(cm.exception.detail))


# ═══════════════════════════════════════════════════════════════════════════
# 5. update() — bitemporal path
# ═══════════════════════════════════════════════════════════════════════════
class UpdateBitemporalPathTests(SimpleTestCase):
    """Verify the dedicated bitemporal code path for historical models."""

    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update")
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_bitemporal_update_calls_update_mixin(self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update):
        """Historical model → uses UpdateModelMixin.update directly, no calculation logic."""
        from lex.core.models.CalculationModel import CalculationModel

        model_cls = MagicMock(spec=[])
        model_cls.valid_from = True
        model_cls.history_id = True
        # NOT a meta model
        del model_cls.meta_history_id

        instance = MagicMock()  # not a CalculationModel
        mock_get_obj.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"value": 42})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = Response({"id": 1})

        resp = view.update(request)
        mock_update.assert_called_once()
        self.assertEqual(resp.status_code, 200)

    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update", side_effect=ValueError("integrity"))
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_bitemporal_error_wrapped_with_prefix(self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update):
        """Errors in the bitemporal path are wrapped with 'Bitemporal update failed:'."""
        model_cls = MagicMock(spec=[])
        model_cls.valid_from = True
        model_cls.history_id = True

        instance = MagicMock()
        mock_get_obj.return_value = instance

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"value": 42})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with self.assertRaises(APIException) as cm:
            view.update(request)
        self.assertIn("Bitemporal update failed", str(cm.exception.detail))


# ═══════════════════════════════════════════════════════════════════════════
# 6. update() — calculation trigger flow
# ═══════════════════════════════════════════════════════════════════════════
class UpdateCalculationTriggerTests(SimpleTestCase):
    """
    When update() receives ``calculate=true`` on a CalculationModel, it must:
    1. Untrack the instance
    2. Set is_calculated = IN_PROGRESS and save
    3. Register in ActiveCalculationStateStore
    4. Send WebSocket notification
    5. Store an empty cache message
    6. Perform the actual update
    """

    @patch("lex.api.views.model_entries.One.CacheManager")
    @patch("lex.api.views.model_entries.One.WebSocketNotifier")
    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update")
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_full_calculation_trigger_flow(
        self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update, mock_ws, mock_cache
    ):
        from lex.core.models.CalculationModel import CalculationModel

        # Build a mock that isinstance() recognises as CalculationModel
        instance = MagicMock(spec=CalculationModel)
        instance._meta = MagicMock()
        instance._meta.model_name = "investment"
        instance._meta.label_lower = "myapp.investment"
        instance.pk = 7
        instance.is_calculated = CalculationModel.NOT_CALCULATED
        instance.__str__ = lambda self: "Investment #7"
        mock_get_obj.return_value = instance

        model_cls = MagicMock(spec=[])  # no valid_from/history_id/meta_history_id

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"value": 100, "calculate": "true"})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "calc-42"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = Response({"id": 7})

        mock_cache.build_cache_key.return_value = "key-7"

        with patch("lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore") as mock_store:
            resp = view.update(request)

        # 1. untrack called
        instance.untrack.assert_called_once()
        # 2. is_calculated set to IN_PROGRESS and saved
        self.assertEqual(instance.is_calculated, CalculationModel.IN_PROGRESS)
        instance.save.assert_called_with(skip_hooks=True)
        # 3. ActiveCalculationStateStore registered
        mock_store.mark_in_progress.assert_called_once()
        call_kwargs = mock_store.mark_in_progress.call_args[1]
        self.assertEqual(call_kwargs["record_id"], "investment_7")
        self.assertEqual(call_kwargs["calculation_id"], "calc-42")
        # 4. WebSocket notification sent
        mock_ws.send_calculation_update.assert_called_once_with(
            calculation_id="calc-42",
            calculation_record="investment_7",
        )
        # 5. Cache message stored
        mock_cache.build_cache_key.assert_called_once_with("investment_7", "calc-42")
        mock_cache.store_message.assert_called_once_with("key-7", "")

    @patch("lex.api.views.model_entries.One.CacheManager")
    @patch("lex.api.views.model_entries.One.WebSocketNotifier")
    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update")
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_non_calculate_request_resets_is_calculated(
        self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update, mock_ws, mock_cache
    ):
        """A normal update on a CalculationModel (no calculate=true) resets is_calculated."""
        from lex.core.models.CalculationModel import CalculationModel

        instance = MagicMock(spec=CalculationModel)
        instance._meta = MagicMock()
        instance._meta.model_name = "investment"
        instance.pk = 3
        instance.is_calculated = CalculationModel.IN_PROGRESS
        mock_get_obj.return_value = instance

        model_cls = MagicMock(spec=[])

        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"value": 100})  # no calculate
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = Response({"id": 3})

        resp = view.update(request)
        # WebSocket/cache NOT called (not a calculate request)
        mock_ws.send_calculation_update.assert_not_called()
        mock_cache.store_message.assert_not_called()

    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update")
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_finally_resets_calculate_requested(self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update):
        """_calculate_requested must be False after update, even on success."""
        instance = MagicMock()  # not a CalculationModel
        mock_get_obj.return_value = instance

        model_cls = MagicMock(spec=[])
        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"x": 1})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = Response({"id": 1})

        view.update(request)
        self.assertFalse(view._calculate_requested)


# ═══════════════════════════════════════════════════════════════════════════
# 7. update() — CalculationModelException handling
# ═══════════════════════════════════════════════════════════════════════════
class UpdateCalculationExceptionTests(SimpleTestCase):
    """CalculationModelException → persist_error_state + APIException."""

    @patch("lex.api.views.model_entries.One.CalculationModel.persist_error_state")
    @patch("lex.api.views.model_entries.One.resolve_exception_traceback", return_value="tb-text")
    @patch("lex.api.views.model_entries.One.resolve_exception_detail", return_value="calc failed")
    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update")
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_calc_exception_persists_error_state(
        self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update,
        mock_detail, mock_tb, mock_persist
    ):
        from lex.core.models.CalculationModel import CalculationModel, CalculationModelException

        instance = MagicMock(spec=CalculationModel)
        instance._meta = MagicMock()
        instance._meta.model_name = "portfolio"
        instance.pk = 5
        instance.is_calculated = CalculationModel.NOT_CALCULATED
        mock_get_obj.return_value = instance

        calc_exc = CalculationModelException("division by zero")
        calc_exc.calc_obj = MagicMock()
        mock_update.side_effect = calc_exc

        model_cls = MagicMock(spec=[])
        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"value": 99, "calculate": "true"})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore"):
            with self.assertRaises(APIException) as cm:
                view.update(request)

        mock_persist.assert_called_once_with(calc_exc.calc_obj)
        self.assertIn("calc failed", str(cm.exception.detail))


# ═══════════════════════════════════════════════════════════════════════════
# 8. update() — generic exception wrapping
# ═══════════════════════════════════════════════════════════════════════════
class UpdateGenericExceptionTests(SimpleTestCase):

    @patch("lex.api.views.model_entries.One.UpdateModelMixin.update", side_effect=TypeError("bad field"))
    @patch("lex.api.views.model_entries.One.model_logging_context")
    @patch("lex.api.views.model_entries.One.OperationContext")
    @patch.object(OneModelEntry, "get_object")
    def test_generic_exception_wrapped_in_api_exception(self, mock_get_obj, mock_ctx, mock_log_ctx, mock_update):
        instance = MagicMock()
        mock_get_obj.return_value = instance

        model_cls = MagicMock(spec=[])
        mc = _mock_model_container(model_class=model_cls)
        request = _make_request(data={"x": 1})
        view = _build_view(kwargs={"model_container": mc, "calculationId": "c1"}, request=request)

        mock_ctx.return_value.__enter__ = MagicMock(return_value="ctx-1")
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_log_ctx.return_value.__enter__ = MagicMock()
        mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with self.assertRaises(APIException) as cm:
            view.update(request)
        self.assertIn("bad field", str(cm.exception.detail))


if __name__ == "__main__":
    unittest.main()
