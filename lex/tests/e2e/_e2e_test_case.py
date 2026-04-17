"""
Shared base class for end-to-end tests.

Provides:
- Dynamic table creation for test models (main + History + MetaHistory)
- Model registration with ``simple_history``
- ``APIClient`` with session-authenticated user
- URL helpers (create / detail / list / history / many)
- Standard mocking of external boundaries (Celery, WebSocket, cache)
- Cleanup of all tables and registered models on teardown
"""

import os
import sys
from contextlib import suppress
from datetime import timedelta
from importlib import reload
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase
from django.urls import clear_url_caches
from django.utils import timezone

from rest_framework.test import APIClient

from lex.process_admin.utils.model_registration import ModelRegistration


class _MockContainer:
    """Fake model container for URL reverse()."""

    def __init__(self, model_name: str):
        self.id = model_name


class E2ETestCase(TransactionTestCase):
    """
    Base class for end-to-end tests.

    Subclasses must define:
        ``e2e_models``: list of model classes to register and create tables for.
    """

    e2e_models: list = []
    databases = {"default"}
    _created_tables: list = []

    # ── class-level setup / teardown ─────────────────────────────────

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ.setdefault("LEX_DYNAMIC_HISTORY_REGISTRATION", "1")

        from simple_history.models import registered_models

        for model in cls.e2e_models:
            if model not in registered_models:
                ModelRegistration._register_standard_model(model, [])

        tables = set(connection.introspection.table_names())
        cls._created_tables = []

        with connection.schema_editor() as schema_editor:
            for model in cls.e2e_models:
                if model._meta.db_table not in tables:
                    schema_editor.create_model(model)
                    cls._created_tables.append(model)
                    tables.add(model._meta.db_table)

                if hasattr(model, "history"):
                    hist = model.history.model
                    if hist._meta.db_table not in tables:
                        schema_editor.create_model(hist)
                        cls._created_tables.append(hist)
                        tables.add(hist._meta.db_table)

                    if hasattr(hist, "meta_history"):
                        meta = hist.meta_history.model
                        if meta._meta.db_table not in tables:
                            schema_editor.create_model(meta)
                            cls._created_tables.append(meta)
                            tables.add(meta._meta.db_table)

            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA foreign_keys = OFF;")

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema_editor:
            for model in reversed(cls._created_tables):
                with suppress(Exception):
                    schema_editor.delete_model(model)
        cls._created_tables.clear()
        super().tearDownClass()

    # ── per-test setup / teardown ────────────────────────────────────

    def setUp(self):
        super().setUp()

        # Clean data
        for model in reversed(self.e2e_models):
            with suppress(Exception):
                model.objects.all().delete()
            if hasattr(model, "history"):
                with suppress(Exception):
                    model.history.model.objects.all().delete()

        # Create authenticated user
        self.user = User.objects.create_user(
            username="e2e_user", password="pw", email="e2e@test.local",
        )
        self.client = APIClient()
        self.client.force_login(self.user)

        # Inject OIDC session expiry so middleware doesn't redirect to login
        session = self.client.session
        session["oidc_expires_at"] = (
            timezone.now() + timedelta(hours=1)
        ).timestamp()
        session.save()

        # Rebuild URL patterns
        self._rebuild_urls()

        # Mock external boundaries
        self._patch_objs = [
            patch(
                "lex.audit_logging.utils.CacheManager.CacheManager.store_message",
                return_value=None,
            ),
            patch(
                "lex.audit_logging.utils.CacheManager.CacheManager.build_cache_key",
                return_value="test_key",
            ),
            patch(
                "lex.audit_logging.utils.WebSocketNotifier.WebSocketNotifier"
                ".send_calculation_update",
                return_value=None,
            ),
            patch(
                "lex.core.signals.ActiveCalculationStateStore"
                ".ActiveCalculationStateStore.mark_in_progress",
                return_value=None,
            ),
            patch(
                "lex.audit_logging.utils.calculation_audit"
                ".ensure_terminal_calculation_audit",
                return_value=None,
            ),
        ]
        self._patches = [p.start() for p in self._patch_objs]

        # Env
        self._env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False,
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        for p in self._patch_objs:
            p.stop()
        super().tearDown()

    # ── URL helpers ──────────────────────────────────────────────────

    def _rebuild_urls(self):
        from django.urls import converters as django_converters
        from django.urls.converters import REGISTERED_CONVERTERS

        real_register = django_converters.register_converter

        def idempotent_register(converter, type_name):
            REGISTERED_CONVERTERS.pop(type_name, None)
            return real_register(converter, type_name)

        converter_patch = patch(
            "lex.process_admin.sites.process_admin_site.register_converter",
            new=idempotent_register,
        )
        converter_patch.start()
        try:
            from lex.process_admin.settings import processAdminSite

            processAdminSite.initialized = False
            _ = processAdminSite.urls
            from django.conf import settings

            clear_url_caches()
            if settings.ROOT_URLCONF in sys.modules:
                reload(sys.modules[settings.ROOT_URLCONF])
        finally:
            converter_patch.stop()

    def _url(self, view_name: str, **kwargs):
        """Reverse a ``process_admin_rest_api`` URL."""
        from django.urls import reverse

        return reverse(f"process_admin_rest_api:{view_name}", kwargs=kwargs)

    def url_create(self, model_name, calc_id="default"):
        return self._url(
            "model-one-entry-create",
            model_container=_MockContainer(model_name),
            calculationId=calc_id,
        )

    def url_detail(self, model_name, pk, calc_id="default"):
        return self._url(
            "model-one-entry-read-update-delete",
            model_container=_MockContainer(model_name),
            calculationId=calc_id,
            pk=pk,
        )

    def url_list(self, model_name):
        return self._url(
            "model-entries-list",
            model_container=_MockContainer(model_name),
        )

    def url_history(self, model_name, pk, calc_id="default"):
        return self._url(
            "model-history-list",
            model_container=_MockContainer(model_name),
            calculationId=calc_id,
            pk=pk,
        )

    def url_many(self, model_name):
        return self._url(
            "model-many-entries",
            model_container=_MockContainer(model_name),
        )

    # ── helpers ──────────────────────────────────────────────────────

    def list_get(self, model_name, query_params=None):
        """GET the list endpoint with filter backends disabled."""
        url = self.url_list(model_name)
        with patch(
            "lex.api.views.model_entries.List.ListModelEntries.filter_backends",
            [],
        ):
            return self.client.get(url, data=query_params or {})

    @staticmethod
    def extract_results(resp_data):
        """Handle both paginated (dict) and unpaginated (list) responses."""
        if isinstance(resp_data, dict):
            return resp_data.get("results", resp_data)
        return list(resp_data)
