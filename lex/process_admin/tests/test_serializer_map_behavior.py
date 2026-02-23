import os
import sys
from pathlib import Path
from unittest import TestCase

import django
from django.apps import apps
from rest_framework import serializers

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.contrib.auth.models import User
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.api.views.model_entries.History import HistoryModelEntry
from lex.api.views.model_info.Fields import Fields
from lex.process_admin.models.ModelContainer import ModelContainer


class _CompactUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username")


class _FormattedUserSerializer(serializers.ModelSerializer):
    formatted_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = "__all__"

    def get_formatted_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class _OldDefaultUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username")


class _NewDefaultUserSerializer(serializers.ModelSerializer):
    override_marker = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "override_marker")

    def get_override_marker(self, _obj):
        return "new-default"


class _DummyProcessAdmin:
    @staticmethod
    def get_fields_in_table_view(_model_class):
        return ["id", "username"]


class SerializerMapBehaviorTests(TestCase):
    def setUp(self):
        self._had_api_serializers = hasattr(User, "api_serializers")
        self._original_api_serializers = getattr(User, "api_serializers", None)

    def tearDown(self):
        if self._had_api_serializers:
            User.api_serializers = self._original_api_serializers
        elif hasattr(User, "api_serializers"):
            delattr(User, "api_serializers")

    def test_custom_serializers_are_additive_to_default_serializer(self):
        User.api_serializers = {"compact": _CompactUserSerializer}

        serializer_map = get_serializer_map_for_model(User, default_fields=["id", "username"])

        self.assertEqual(set(serializer_map.keys()), {"default", "compact"})

    def test_explicit_default_serializer_still_overrides_auto_default(self):
        User.api_serializers = {"default": _CompactUserSerializer}

        serializer_map = get_serializer_map_for_model(User, default_fields=["id"])
        payload = serializer_map["default"](User(id=1, username="alice")).data

        self.assertIn("username", payload)
        self.assertIn("id_field", payload)
        self.assertIn("short_description", payload)
        self.assertIn("lex_reserved_scopes", payload)

    def test_custom_serializer_method_fields_with_all_fields_are_preserved(self):
        User.api_serializers = {"formatted": _FormattedUserSerializer}

        serializer_map = get_serializer_map_for_model(User)
        payload = serializer_map["formatted"](
            User(username="alice", first_name="Alice", last_name="Smith")
        ).data

        self.assertEqual(payload["formatted_name"], "Alice Smith")

    def test_model_container_refreshes_when_custom_serializers_change(self):
        container = ModelContainer(User, _DummyProcessAdmin())
        initial_map = container.get_serializers_map()
        self.assertEqual(set(initial_map.keys()), {"default"})

        User.api_serializers = {"compact": _CompactUserSerializer}
        refreshed_map = container.get_serializers_map()

        self.assertEqual(set(refreshed_map.keys()), {"default", "compact"})

    def test_invalid_custom_serializer_does_not_remove_default_serializer(self):
        User.api_serializers = {"broken": object()}

        serializer_map = get_serializer_map_for_model(User, default_fields=["id"])

        self.assertIn("default", serializer_map)
        self.assertNotIn("broken", serializer_map)

    def test_fields_view_uses_refreshed_default_serializer(self):
        class _Container:
            model_class = User
            serializers_map = {"default": _OldDefaultUserSerializer}

            @staticmethod
            def get_serializers_map():
                return {"default": _NewDefaultUserSerializer}

        request = Request(APIRequestFactory().get("/api/model_info/user/fields"))
        response = Fields().get(request, model_container=_Container())
        field_names = {item["name"] for item in response.data["fields"]}

        self.assertIn("override_marker", field_names)
        self.assertNotIn("username", field_names)

    def test_history_view_uses_refreshed_default_serializer(self):
        class _Pk:
            name = "id"

        class _Meta:
            pk = _Pk()

        class _HistoryModel:
            pass

        class _ModelClass:
            __name__ = "DummyModel"
            _meta = _Meta()
            history = type("HistoryAccessor", (), {"model": _HistoryModel})()

        class _Container:
            model_class = _ModelClass
            serializers_map = {"default": _OldDefaultUserSerializer}

            @staticmethod
            def get_serializers_map():
                return {"default": _NewDefaultUserSerializer}

        class _HistoryViewSpy(HistoryModelEntry):
            captured_serializer_class = None

            def _get_history_queryset(self, _request, _history_model, _pk_name, _pk):
                return [object()]

            def _serialize_record(self, _record, serializer_class=None, serializer_context=None):
                self.captured_serializer_class = serializer_class
                return {"ok": True}

        view = _HistoryViewSpy()
        request = Request(APIRequestFactory().get("/api/model_entries/dummy/history/1"))
        view.list(request, model_container=_Container(), pk=1)

        self.assertIs(view.captured_serializer_class, _NewDefaultUserSerializer)
