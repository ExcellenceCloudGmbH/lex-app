"""
Tests for the dynamic serializer map and permission-scoped serialization.

Verifies:
    • Custom ``api_serializers`` are additive to the auto-generated default
    • Explicit ``default`` overrides still inherit internal fields
    • ``ModelContainer`` refreshes when ``api_serializers`` change
    • ``lex_reserved_scopes`` reflects field-level ``PermissionResult``
      for edit, delete, and export actions
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

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

from lex.api.serializers import base_serializers
from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.api.views.model_entries.History import HistoryModelEntry
from lex.api.views.model_info.Fields import Fields
from lex.core.models.LexModel import PermissionResult
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


class _HiddenActionsUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username")
        hide_actions_column = True


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
    """Prove serializer map construction, refresh, and permission-scoped output."""
    def setUp(self):
        self._had_api_serializers = hasattr(User, "api_serializers")
        self._original_api_serializers = getattr(User, "api_serializers", None)
        base_serializers._capability_cache.pop(User, None)

    def tearDown(self):
        if self._had_api_serializers:
            User.api_serializers = self._original_api_serializers
        elif hasattr(User, "api_serializers"):
            delattr(User, "api_serializers")
        base_serializers._capability_cache.pop(User, None)

    def _permission_request(self):
        return SimpleNamespace(
            user=SimpleNamespace(
                email="viewer@example.com",
                is_authenticated=True,
                is_superuser=False,
                groups=SimpleNamespace(values_list=lambda *args, **kwargs: []),
            ),
            userinfo={},
            user_permissions=[],
            client_roles=[],
            session={},
        )

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

    def test_overridden_default_serializer_always_includes_id(self):
        """The wrapped developer override must always expose the model's PK
        as ``id`` so the frontend SSRM datasource can derive a real row id
        (otherwise show/edit URLs degrade to ``ssrm:groupPath:...`` and the
        cell-edit handler bails out, suppressing the CRUD loading overlay).
        """

        class _MinimalDefaultSerializer(serializers.ModelSerializer):
            class Meta:
                model = User
                fields = ("username",)  # intentionally omits "id"

        User.api_serializers = {"default": _MinimalDefaultSerializer}

        serializer_map = get_serializer_map_for_model(
            User, default_fields=["username"]
        )
        payload = serializer_map["default"](User(id=42, username="alice")).data

        self.assertEqual(payload.get("id"), 42)
        self.assertEqual(payload.get("id_field"), "id")
        self.assertIn("username", payload)

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

    def test_configured_default_serializer_alias_added_when_default_overridden(self):
        from lex.core import config as lex_config

        User.api_serializers = {"default": _CompactUserSerializer}

        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            serializer_map = get_serializer_map_for_model(
                User, default_fields=["id", "username"]
            )

        self.assertEqual(
            set(serializer_map.keys()), {"default", "framework_default"}
        )
        # The "default" entry holds the developer-provided serializer (wrapped),
        # while the configured alias exposes the framework auto-generated one.
        self.assertIsNot(
            serializer_map["default"], serializer_map["framework_default"]
        )
        framework_payload = serializer_map["framework_default"](
            User(id=1, username="alice")
        ).data
        self.assertIn("id_field", framework_payload)

    def test_configured_alias_not_added_when_default_not_overridden(self):
        from lex.core import config as lex_config

        User.api_serializers = {"compact": _CompactUserSerializer}

        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            serializer_map = get_serializer_map_for_model(
                User, default_fields=["id", "username"]
            )

        # Models without an explicit "default" override keep historical
        # behavior: only the auto-generated serializer is registered (under
        # "default") plus the developer's other custom serializers.
        self.assertEqual(set(serializer_map.keys()), {"default", "compact"})

    def test_configured_alias_skipped_when_name_collides_with_custom(self):
        from lex.core import config as lex_config

        User.api_serializers = {
            "default": _CompactUserSerializer,
            "framework_default": _FormattedUserSerializer,
        }

        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            serializer_map = get_serializer_map_for_model(
                User, default_fields=["id", "username"]
            )

        # The developer's own "framework_default" serializer is preserved and
        # not silently replaced by the framework alias.
        self.assertEqual(
            set(serializer_map.keys()), {"default", "framework_default"}
        )
        payload = serializer_map["framework_default"](
            User(username="alice", first_name="Alice", last_name="Smith")
        ).data
        self.assertEqual(payload["formatted_name"], "Alice Smith")

    def test_configured_alias_noop_when_name_equals_default(self):
        from lex.core import config as lex_config

        User.api_serializers = {"default": _CompactUserSerializer}

        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="default",
        ):
            serializer_map = get_serializer_map_for_model(
                User, default_fields=["id", "username"]
            )

        self.assertEqual(set(serializer_map.keys()), {"default"})

    # ------------------------------------------------------------------
    # resolve_default_serializer_name / resolve_requested_serializer_name
    # ------------------------------------------------------------------

    def test_resolve_default_returns_default_when_no_alias_registered(self):
        from lex.api.serializers.base_serializers import (
            resolve_default_serializer_name,
        )

        self.assertEqual(
            resolve_default_serializer_name({"default": _CompactUserSerializer}),
            "default",
        )

    def test_resolve_default_returns_alias_when_developer_overrode_default(self):
        from lex.core import config as lex_config
        from lex.api.serializers.base_serializers import (
            resolve_default_serializer_name,
        )

        serializer_map = {
            "default": _CompactUserSerializer,
            "framework_default": _NewDefaultUserSerializer,
        }
        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            self.assertEqual(
                resolve_default_serializer_name(serializer_map),
                "framework_default",
            )

    def test_resolve_default_falls_back_when_alias_not_registered(self):
        from lex.core import config as lex_config
        from lex.api.serializers.base_serializers import (
            resolve_default_serializer_name,
        )

        # Model did not override "default" → alias is not in the map → fall
        # back to literal "default".
        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            self.assertEqual(
                resolve_default_serializer_name(
                    {"default": _CompactUserSerializer}
                ),
                "default",
            )

    def test_resolve_requested_passes_through_explicit_custom_names(self):
        from lex.core import config as lex_config
        from lex.api.serializers.base_serializers import (
            resolve_requested_serializer_name,
        )

        serializer_map = {
            "default": _CompactUserSerializer,
            "framework_default": _NewDefaultUserSerializer,
            "compact": _CompactUserSerializer,
        }
        with patch.object(
            lex_config,
            "get_configured_default_serializer_name",
            return_value="framework_default",
        ):
            # Explicit non-"default" names are not rewritten.
            self.assertEqual(
                resolve_requested_serializer_name(serializer_map, "compact"),
                "compact",
            )
            # Explicit "default" gets routed to the framework alias by the
            # helper. (Backend endpoints intentionally do NOT call this for
            # the public ``?serializer=default`` query — frontends opt in by
            # passing the configured alias explicitly. This helper is
            # available for future framework-internal call sites.)
            self.assertEqual(
                resolve_requested_serializer_name(serializer_map, "default"),
                "framework_default",
            )

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

    def test_fields_view_reports_default_list_ui_options(self):
        class _Container:
            model_class = User

            @staticmethod
            def get_serializers_map():
                return {"default": _CompactUserSerializer}

        request = Request(APIRequestFactory().get("/api/model_info/user/fields"))
        response = Fields().get(request, model_container=_Container())

        self.assertEqual(response.data["list_ui"], {"hide_actions_column": False})

    def test_fields_view_uses_serializer_meta_to_hide_actions_column(self):
        User.api_serializers = {"compact": _HiddenActionsUserSerializer}

        class _Container:
            model_class = User

            @staticmethod
            def get_serializers_map():
                return get_serializer_map_for_model(User, default_fields=["id", "username"])

        request = Request(APIRequestFactory().get("/api/model_info/user/fields?serializer=compact"))
        response = Fields().get(request, model_container=_Container())

        self.assertEqual(response.data["list_ui"], {"hide_actions_column": True})

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

    def test_default_serializer_exposes_permission_result_scopes(self):
        serializer_map = get_serializer_map_for_model(User, default_fields=["id", "username", "email"])
        serializer_class = serializer_map["default"]
        request = self._permission_request()

        with (
            patch.object(
                User,
                "permission_edit",
                new=lambda _self, _user_context: PermissionResult.allow_fields(
                    {"username", "email", "id"},
                    "selected fields",
                ),
                create=True,
            ),
            patch.object(User, "permission_delete", new=lambda _self, _user_context: False, create=True),
            patch.object(
                User,
                "permission_export",
                new=lambda _self, _user_context: PermissionResult.allow_all_except({"password"}, "mask secrets"),
                create=True,
            ),
        ):
            base_serializers._capability_cache.pop(User, None)
            payload = serializer_class(
                User(id=1, username="alice", email="alice@example.com"),
                context={"request": request},
            ).data

        self.assertEqual(payload["lex_reserved_scopes"]["edit"], ["email", "username"])
        self.assertFalse(payload["lex_reserved_scopes"]["delete"])
        self.assertTrue(payload["lex_reserved_scopes"]["export"])

    def test_default_serializer_reports_denied_permission_results(self):
        serializer_map = get_serializer_map_for_model(User, default_fields=["id", "username"])
        serializer_class = serializer_map["default"]
        request = self._permission_request()

        with (
            patch.object(User, "permission_edit", new=lambda _self, _user_context: PermissionResult.deny("blocked"), create=True),
            patch.object(User, "permission_delete", new=lambda _self, _user_context: False, create=True),
            patch.object(User, "permission_export", new=lambda _self, _user_context: PermissionResult.deny("blocked"), create=True),
        ):
            base_serializers._capability_cache.pop(User, None)
            payload = serializer_class(
                User(id=1, username="alice"),
                context={"request": request},
            ).data

        self.assertEqual(payload["lex_reserved_scopes"], {"edit": [], "delete": False, "export": False})
