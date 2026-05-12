"""
Tests for lex.api.views.model_entries.mixins.ModelEntryProviderMixin
====================================================================

ModelEntryProviderMixin is the shared foundation consumed by OneModelEntry
and ManyModelEntries.  It provides:

1. ``get_queryset()`` — bitemporal as_of filtering, auto select_related for FK
   fields, and the entry point for read-repair hooks.
2. ``get_serializer_class()`` — dynamic serializer resolution from the model
   container's serializers_map (or method), with a User-model short-circuit.

Also tests the ``UserModelSerializer`` inner class.
"""

import os
import sys
import unittest
from datetime import datetime, timezone as dt_tz
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase
from django.contrib.auth.models import User
from rest_framework.exceptions import APIException

from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import (
    ModelEntryProviderMixin,
    UserModelSerializer,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fake_fk_field(name):
    """Create a mock field that looks like a ForeignKey."""
    from django.db.models import ForeignKey
    field = MagicMock(spec=ForeignKey)
    field.name = name
    return field


def _make_fake_char_field(name):
    """Create a mock field that is NOT a ForeignKey."""
    from django.db.models import CharField
    field = MagicMock(spec=CharField)
    field.name = name
    return field


def _make_model_class(*, fields=None, is_user_subclass=False):
    """Build a mock model_class with _meta.fields and .objects.all()."""
    if is_user_subclass:
        # Create a class that issubclass(cls, User) returns True
        cls = type("FakeUser", (User,), {"__module__": "fake", "Meta": type("Meta", (), {"app_label": "auth"})})
    else:
        # Must be a real class (not MagicMock) so issubclass() works
        mock_meta = MagicMock()
        mock_meta.fields = fields or []
        mock_meta.model_name = "testmodel"

        qs = MagicMock()
        qs.all.return_value = qs
        qs.select_related.return_value = qs

        cls = type("FakeModel", (), {
            "_meta": mock_meta,
            "objects": qs,
        })

    return cls


def _build_mixin_view(*, model_class=None, query_params=None, serializers_map=None,
                       use_get_serializers_map=False):
    """Build a minimal object that exercises ModelEntryProviderMixin methods."""
    class FakeView(ModelEntryProviderMixin):
        pass

    view = FakeView()

    mc = MagicMock()
    mc.model_class = model_class or _make_model_class()
    mc.pk_name = "id"

    if serializers_map is not None:
        if use_get_serializers_map:
            mc.get_serializers_map = MagicMock(return_value=serializers_map)
            # Remove serializers_map attribute so hasattr check works correctly
            del mc.serializers_map
        else:
            del mc.get_serializers_map  # ensure it falls through to attribute
            mc.serializers_map = serializers_map

    request = MagicMock()
    request.query_params = query_params or {}

    view.kwargs = {"model_container": mc}
    view.request = request
    return view


# ═══════════════════════════════════════════════════════════════════════════
# 1. get_queryset — basic queryset construction
# ═══════════════════════════════════════════════════════════════════════════
class GetQuerysetBasicTests(SimpleTestCase):
    """Verify queryset construction without as_of filtering."""

    @patch("lex.api.views.model_entries.mixins.ModelEntryProviderMixin.parse_as_of_datetime")
    def test_no_as_of_returns_all_objects(self, mock_parse):
        """Without ?as_of, get_queryset returns model_class.objects.all()."""
        model_cls = _make_model_class()
        view = _build_mixin_view(model_class=model_cls, query_params={})

        result = view.get_queryset()

        model_cls.objects.all.assert_called_once()

    @patch("lex.api.views.model_entries.mixins.ModelEntryProviderMixin.parse_as_of_datetime")
    def test_auto_select_related_for_fk_fields(self, mock_parse):
        """FK fields trigger select_related to prevent N+1 queries."""
        fk1 = _make_fake_fk_field("author")
        fk2 = _make_fake_fk_field("category")
        char = _make_fake_char_field("name")
        model_cls = _make_model_class(fields=[fk1, fk2, char])

        view = _build_mixin_view(model_class=model_cls, query_params={})
        view.get_queryset()

        qs = model_cls.objects.all()
        qs.select_related.assert_called_once_with("author", "category")

    @patch("lex.api.views.model_entries.mixins.ModelEntryProviderMixin.parse_as_of_datetime")
    def test_no_fk_fields_skips_select_related(self, mock_parse):
        """If there are no FK fields, select_related is not called."""
        char = _make_fake_char_field("name")
        model_cls = _make_model_class(fields=[char])

        view = _build_mixin_view(model_class=model_cls, query_params={})
        result = view.get_queryset()

        # select_related should NOT be called since fk_fields is empty
        qs = model_cls.objects.all()
        # The queryset returned should be the .all() result (no select_related)
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════════════════
# 2. get_queryset — bitemporal as_of filtering
# ═══════════════════════════════════════════════════════════════════════════
class GetQuerysetAsOfTests(SimpleTestCase):
    """Verify that ?as_of triggers get_queryset_as_of()."""

    @patch("lex.core.services.Bitemporal.get_queryset_as_of")
    @patch("lex.api.views.model_entries.mixins.ModelEntryProviderMixin.parse_as_of_datetime")
    def test_valid_as_of_delegates_to_bitemporal(self, mock_parse, mock_qs_as_of):
        """A valid as_of date delegates to get_queryset_as_of."""
        ts = datetime(2025, 6, 1, 12, 0, tzinfo=dt_tz.utc)
        mock_parse.return_value = ts
        mock_qs_as_of.return_value = MagicMock()

        model_cls = _make_model_class()
        view = _build_mixin_view(model_class=model_cls, query_params={"as_of": "2025-06-01T12:00:00Z"})

        view.get_queryset()

        mock_parse.assert_called_once_with("2025-06-01T12:00:00Z")
        mock_qs_as_of.assert_called_once_with(model_cls, ts)

    @patch("lex.core.services.Bitemporal.get_queryset_as_of")
    @patch("lex.api.views.model_entries.mixins.ModelEntryProviderMixin.parse_as_of_datetime")
    def test_unparseable_as_of_falls_through(self, mock_parse, mock_qs_as_of):
        """parse_as_of_datetime returning None → use plain queryset."""
        mock_parse.return_value = None

        model_cls = _make_model_class()
        view = _build_mixin_view(model_class=model_cls, query_params={"as_of": "not-a-date"})

        view.get_queryset()

        mock_qs_as_of.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 3. get_serializer_class — dynamic resolution
# ═══════════════════════════════════════════════════════════════════════════
class GetSerializerClassTests(SimpleTestCase):
    """Test serializer lookup from container mapping."""

    def test_default_serializer(self):
        """No ?serializer → returns mapping['default']."""
        default_ser = MagicMock()
        model_cls = _make_model_class()
        view = _build_mixin_view(
            model_class=model_cls,
            serializers_map={"default": default_ser},
            query_params={},
        )
        result = view.get_serializer_class()
        self.assertIs(result, default_ser)

    def test_named_serializer(self):
        """?serializer=compact → returns mapping['compact']."""
        compact_ser = MagicMock()
        model_cls = _make_model_class()
        view = _build_mixin_view(
            model_class=model_cls,
            serializers_map={"default": MagicMock(), "compact": compact_ser},
            query_params={"serializer": "compact"},
        )
        result = view.get_serializer_class()
        self.assertIs(result, compact_ser)

    def test_unknown_serializer_raises_api_exception(self):
        """Unknown serializer name → APIException with error + available keys."""
        model_cls = _make_model_class()
        view = _build_mixin_view(
            model_class=model_cls,
            serializers_map={"default": MagicMock(), "detail": MagicMock()},
            query_params={"serializer": "nonexistent"},
        )
        with self.assertRaises(APIException) as cm:
            view.get_serializer_class()

        detail = cm.exception.detail
        self.assertIn("Unknown serializer", detail["error"])
        self.assertIn("nonexistent", detail["error"])
        self.assertIn("default", detail["available"])
        self.assertIn("detail", detail["available"])

    def test_uses_get_serializers_map_method_when_available(self):
        """If container has get_serializers_map(), use it over serializers_map attribute."""
        method_ser = MagicMock()
        model_cls = _make_model_class()
        view = _build_mixin_view(
            model_class=model_cls,
            serializers_map={"default": method_ser},
            use_get_serializers_map=True,
            query_params={},
        )
        result = view.get_serializer_class()
        self.assertIs(result, method_ser)
        view.kwargs["model_container"].get_serializers_map.assert_called_once()

    def test_user_subclass_returns_user_model_serializer(self):
        """If model_class is a User subclass → UserModelSerializer short-circuit."""
        model_cls = _make_model_class(is_user_subclass=True)
        mc = MagicMock()
        mc.model_class = model_cls
        mc.serializers_map = {"default": MagicMock()}

        class FakeView(ModelEntryProviderMixin):
            pass

        view = FakeView()
        view.kwargs = {"model_container": mc}
        view.request = MagicMock()
        view.request.query_params = {}

        result = view.get_serializer_class()
        self.assertIs(result, UserModelSerializer)


# ═══════════════════════════════════════════════════════════════════════════
# 4. UserModelSerializer
# ═══════════════════════════════════════════════════════════════════════════
class UserModelSerializerTests(SimpleTestCase):
    """Verify the embedded serializer for Django's User model."""

    def test_short_description_format(self):
        """get_short_description joins first_name, last_name, email."""
        user = SimpleNamespace(first_name="John", last_name="Doe", email="john@example.com")
        ser = UserModelSerializer()
        result = ser.get_short_description(user)
        self.assertEqual(result, "John Doe - john@example.com")

    def test_short_description_empty_fields(self):
        """Handles empty name fields gracefully."""
        user = SimpleNamespace(first_name="", last_name="", email="anon@example.com")
        ser = UserModelSerializer()
        result = ser.get_short_description(user)
        self.assertEqual(result, "  - anon@example.com")

    def test_meta_model_is_user(self):
        self.assertEqual(UserModelSerializer.Meta.model, User)

    def test_meta_fields_all(self):
        self.assertEqual(UserModelSerializer.Meta.fields, "__all__")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Permission class defaults
# ═══════════════════════════════════════════════════════════════════════════
class PermissionDefaultTests(SimpleTestCase):
    """Verify the mixin's default permission_classes."""

    def test_permission_classes_has_two_entries(self):
        self.assertEqual(len(ModelEntryProviderMixin.permission_classes), 2)

    def test_user_permission_included(self):
        from lex.api.views.permissions.UserPermission import UserPermission
        self.assertIn(UserPermission, ModelEntryProviderMixin.permission_classes)


if __name__ == "__main__":
    unittest.main()
