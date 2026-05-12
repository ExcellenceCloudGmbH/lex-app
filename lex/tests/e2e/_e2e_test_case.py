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
from lex.process_admin.utils.model_registration import ModelRegistration
from rest_framework.test import APIClient


class _MockContainer:
    """Fake model container for URL reverse()."""

    def __init__(self, model_name: str):
        self.id = model_name


class E2ETestCase(TransactionTestCase):
    """
    Base class for end-to-end tests.

    Subclasses must define:
        ``e2e_models``: list of model classes to register and create tables for.

    Optional class-level hooks (Pass B2 — fixture extensions):

        ``e2e_framework_models`` — framework-internal models that the
            *test* doesn't own but *does* depend on. Typical use:
            ``[AuditLog, AuditLogStatus]`` for Cluster-6 tests that
            observe audit rows. Their tables are created alongside
            ``e2e_models`` and torn down symmetrically. Unlike
            ``e2e_models``, they are NOT re-registered with simple_history
            (they already are by the framework).

        ``e2e_unpatch`` — names of default external-boundary patches to
            skip. The default patch list mocks ``store_message``,
            ``build_cache_key``, ``send_calculation_update``,
            ``mark_in_progress``, ``ensure_terminal_calculation_audit``.
            Add a name to ``e2e_unpatch`` to let the real implementation
            run — or to install a ``MagicMock`` spy on it (see
            ``spy_on`` below). Clusters 6 and 9 rely on this to observe
            the very systems the default patches silence.

        ``spy_on`` — convenience method on the instance; returns a
            ``MagicMock`` replacing one of the default-patched targets
            so the test can make call-count / call-arg assertions. Use
            this when you want *a* mock (for recording) but not the
            stock ``return_value=None`` mock.
    """

    e2e_models: list = []
    e2e_framework_models: list = []
    e2e_unpatch: set = set()
    databases = {"default"}
    _created_tables: list = []
    _created_framework_tables: list = []

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

        # Framework-internal model tables (e.g. AuditLog) — created
        # WITHOUT touching simple_history (the framework already
        # registered them on import).
        cls._created_framework_tables = []
        with connection.schema_editor() as schema_editor:
            for model in cls.e2e_framework_models:
                if model._meta.db_table not in tables:
                    schema_editor.create_model(model)
                    cls._created_framework_tables.append(model)
                    tables.add(model._meta.db_table)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema_editor:
            # Framework models first — they may reference e2e_models.
            for model in reversed(cls._created_framework_tables):
                with suppress(Exception):
                    schema_editor.delete_model(model)
            for model in reversed(cls._created_tables):
                with suppress(Exception):
                    schema_editor.delete_model(model)
        cls._created_tables.clear()
        cls._created_framework_tables.clear()
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

        # Create authenticated user.
        #
        # Defensive delete first: TransactionTestCase normally flushes
        # auth_user between tests, but when the suite is run with
        # ``--keepdb`` and a previous process was interrupted (Ctrl+C,
        # crash) after ``setUp`` ran but before ``_fixture_teardown``
        # flushed, a stale ``e2e_user`` row survives into the next
        # invocation and the first E2E test to run blows up with a
        # ``UniqueViolation`` on ``auth_user_username_key``. Clearing
        # the row unconditionally makes setUp idempotent against any
        # leftover state.
        User.objects.filter(username="e2e_user").delete()
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
        #
        # Each entry is (short-name, dotted-path). Subclasses can
        # opt OUT of any patch by adding its short-name to
        # ``e2e_unpatch`` — useful when the test *wants* to observe
        # the real call (Cluster 6 audit rows, Cluster 9 signal
        # fan-out) or wants to install its own spy via ``spy_on``.
        self._default_patch_specs = [
            (
                "store_message",
                "lex.audit_logging.utils.CacheManager.CacheManager.store_message",
            ),
            (
                "build_cache_key",
                "lex.audit_logging.utils.CacheManager.CacheManager.build_cache_key",
            ),
            (
                "send_calculation_update",
                "lex.audit_logging.utils.WebSocketNotifier.WebSocketNotifier"
                ".send_calculation_update",
            ),
            (
                "mark_in_progress",
                "lex.core.signals.ActiveCalculationStateStore"
                ".ActiveCalculationStateStore.mark_in_progress",
            ),
            (
                "ensure_terminal_calculation_audit",
                "lex.audit_logging.utils.calculation_audit"
                ".ensure_terminal_calculation_audit",
            ),
        ]
        # Per-patch return_value overrides so the defaults stay drop-in.
        _return_values = {
            "build_cache_key": "test_key",
        }
        self._patch_objs = []
        self._patch_map: dict = {}  # short-name → started mock
        for short_name, target in self._default_patch_specs:
            if short_name in self.e2e_unpatch:
                continue  # caller wants to see the real call
            p = patch(target, return_value=_return_values.get(short_name, None))
            self._patch_objs.append(p)
            self._patch_map[short_name] = p
        self._patches = [p.start() for p in self._patch_objs]
        # Rebuild map to hold the started mocks, not the patcher objects.
        self._patch_map = dict(zip(self._patch_map.keys(), self._patches))

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

    # ── Pass B3 fixture helpers ──────────────────────────────────────

    def authenticate_as_api_key(
        self,
        name: str = "Technical User",
        scopes=None,
    ):
        """
        Swap the session-authenticated user for an API-key caller.

        This is the E2E analogue of what happens in production when an
        external system calls the REST API with an ``Authorization:
        Api-Key <raw>`` header: the ``rest_framework_api_key``
        ``HasAPIKey`` permission accepts the request, and
        :meth:`UserContext._api_key_context` builds a context whose
        ``client_roles`` includes ``"api_key"`` and whose ``user`` is a
        :class:`~lex.api.utils.api_key_requests.TechnicalAPIKeyUser`
        carrying ``username = <api key name>``.

        The helper:

        1. Logs the session user out (so ``request.user.is_authenticated``
           is ``False`` — otherwise ``_api_key_context`` short-circuits
           and never runs).
        2. Creates an ``APIKey`` row and installs the raw key as the
           ``Authorization`` header on the test client via
           ``client.credentials()``. That makes ``HasAPIKey`` pass and
           lets ``KeyParser().get(request)`` resolve an identity.
        3. Returns the raw key string in case the test wants to verify
           header wiring directly.

        Unlocks the scenarios that were previously skipped with the
        reason *"requires the API-key auth middleware to attach
        identity to the request"*:

            * 2.7 — POST via API key → ``created_by = "Technical User"``
            * 4.11 — API-key context → ``client_roles`` includes ``"api_key"``
            * 6.8 — API-key actor → ``created_by = "Technical User"``

        Args:
            name: The ``APIKey.name`` — this is the string that
                downstream code (``TechnicalAPIKeyUser.username``,
                audit ``created_by`` / ``edited_by``) will see.
            scopes: Reserved for future use. The production
                :class:`APIKeyRequestIdentity` carries a frozenset of
                scopes; today the default ``DEFAULT_API_KEY_SCOPES``
                is always used, so this argument is accepted for API
                symmetry but not yet threaded through.

        Returns:
            The raw API-key string that was installed on the client.
        """
        import hashlib

        from django.conf import settings as _dj_settings
        from django.contrib.auth.hashers import BasePasswordHasher
        from rest_framework_api_key.models import APIKey
        from rest_framework_api_key.crypto import KeyGenerator

        self.client.logout()

        class _CompactAPIKeyHasher(BasePasswordHasher):
            """Short deterministic test hasher for old APIKey DB schemas.

            ``djangorestframework-api-key`` now generates SHA-512 hashes that
            need 150-character ``id`` / ``hashed_key`` columns, but some CI
            databases can still be created from the package's older migration
            shape (100-character columns). E2E tests only need a valid API key
            identity, not the production hash length, so keep the real model,
            manager, parser, and ``HasAPIKey`` verification path while using a
            compact SHA-256 representation that fits both schemas.
            """

            algorithm = "lex_test_sha256"

            def salt(self) -> str:
                return ""

            def encode(self, password: str, salt: str) -> str:
                if salt != "":
                    raise ValueError("salt is unnecessary for high entropy API tokens.")
                digest = hashlib.sha256(password.encode()).hexdigest()
                return f"{self.algorithm}$${digest}"

            def verify(self, password: str, encoded: str) -> bool:
                from django.utils.crypto import constant_time_compare

                return constant_time_compare(encoded, self.encode(password, ""))

        class _CompactAPIKeyGenerator(KeyGenerator):
            preferred_hasher = _CompactAPIKeyHasher()

        p_key_generator = patch.object(
            APIKey.objects,
            "key_generator",
            _CompactAPIKeyGenerator(),
        )
        p_key_generator.start()
        self._patch_objs.append(p_key_generator)

        _, raw_key = APIKey.objects.create_key(name=name)

        # The framework configures ``API_KEY_CUSTOM_HEADER = "HTTP_API_KEY"``
        # in settings, which tells ``rest_framework_api_key``'s
        # ``KeyParser`` to read the raw key from the ``Api-Key`` HTTP
        # header (META key ``HTTP_API_KEY``). Set only the custom
        # header — deliberately NOT ``Authorization: Api-Key ...``,
        # because the bundled ``BearerAuthenticationBackend`` logs a
        # warning every time it sees a non-Bearer ``Authorization``
        # header.
        custom_header = getattr(
            _dj_settings, "API_KEY_CUSTOM_HEADER", "HTTP_API_KEY",
        )
        self.client.credentials(**{custom_header: raw_key})

        # The default oauth2_authcodeflow LoginRequiredMiddleware runs
        # ``authenticate(request)`` on every API path and, if no user
        # comes back, raises ``MiddlewareException("id token is
        # missing, ...")`` → 401 before the view ever sees the
        # request. That's fine for session/Bearer traffic but it
        # locks out API-key requests entirely, because
        # ``HasAPIKey`` lives at the DRF view layer (one step later).
        #
        # Patch the middleware's login check to short-circuit for
        # requests that carry the custom API-key header. This mirrors
        # how production deployments list API routes in
        # ``OIDC_PUBLIC_URLS`` so they bypass OIDC; we do it by
        # header sniffing so the test does not depend on a settings
        # override.
        try:
            from oauth2_authcodeflow.middleware import LoginRequiredMiddleware

            real_check = LoginRequiredMiddleware.check_login_required
            meta_key = custom_header or "HTTP_API_KEY"

            def _skip_for_api_key(self_mw, request):
                if request.META.get(meta_key):
                    return
                return real_check(self_mw, request)

            p = patch.object(
                LoginRequiredMiddleware,
                "check_login_required",
                _skip_for_api_key,
            )
            p.start()
            self._patch_objs.append(p)
        except ImportError:  # pragma: no cover
            pass

        # Stash for tests that need to assert on header round-tripping.
        self._api_key_raw = raw_key
        self._api_key_name = name
        return raw_key

    # ── Pass B2 fixture helpers ──────────────────────────────────────

    def spy_on(self, short_name: str):
        """
        Replace one of the default patches with a fresh ``MagicMock``
        (spy) and return it.

        Usage::

            class TestSignal(E2ETestCase):
                e2e_unpatch = {"send_calculation_update"}  # disable default

                def test_signal_fires(self):
                    spy = self.spy_on("send_calculation_update")
                    ...do work...
                    spy.assert_called()

        Unlike the stock default mocks (which silently swallow calls
        for determinism), a spy records every call so the test can
        assert on it.
        """
        target = next(
            (t for name, t in self._default_patch_specs if name == short_name),
            None,
        )
        if target is None:
            raise ValueError(
                f"spy_on: unknown short-name {short_name!r}. Known: "
                f"{[n for n, _ in self._default_patch_specs]}"
            )
        p = patch(target)
        mock_obj = p.start()
        # Stop the spy at teardown via the existing patch list.
        self._patch_objs.append(p)
        self._patch_map[short_name] = mock_obj
        return mock_obj

    def operation_context(self, calculation_id: str = "test-calc"):
        """
        Return an :class:`OperationContext` pre-wired with a
        ``calculation_id`` so downstream code that resolves
        ``ContextResolver`` inside the save/hook flow does not raise
        ``Missing calculation_id in operation context``.

        Usage::

            with self.operation_context("calc-9-4"):
                obj = MyCalc(name="x")
                obj.is_calculated = CalculationModel.IN_PROGRESS
                obj.save()

        This is the E2E analogue of what the REST API's
        ``OperationContext(request, calculationId)`` wrapper does in
        ``views/model_entries/One.py``. Tests that need to observe
        ``CacheManager.cleanup_calculation`` (scenario 9.4) or any
        other context-dependent seam should wrap the body that
        triggers the calculation in this helper.
        """
        from lex.api.utils.Context import OperationContext

        return OperationContext(calculation_id=calculation_id)

    def dispatch_recorder(self):
        """
        Return a context manager that intercepts Celery
        ``apply_async`` dispatches and records their ``(args, kwargs)``
        without needing a live broker.

        Usage::

            with self.dispatch_recorder() as rec:
                trigger_the_thing()
            self.assertEqual(len(rec.calls), 1)
            self.assertEqual(rec.calls[0].kwargs["countdown"], 5)

        Falls back gracefully if the Celery-dispatch seam doesn't
        exist in the environment — the recorder records zero calls.
        """
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        class _Recorder:
            def __init__(self):
                self.calls = []
                self.mock = MagicMock(
                    side_effect=lambda *a, **kw: self.calls.append(
                        type("Call", (), {"args": a, "kwargs": kw})()
                    ),
                )

        recorder = _Recorder()

        @contextmanager
        def _cm():
            # Two candidate seams — whichever exists is patched.
            candidates = [
                "celery.app.task.Task.apply_async",
                "lex.lex_app.celery_tasks.lex_shared_task",
            ]
            patchers = []
            for dotted in candidates:
                try:
                    p = patch(dotted, new=recorder.mock)
                    p.start()
                    patchers.append(p)
                except Exception:  # pragma: no cover — seam not present
                    pass
            try:
                yield recorder
            finally:
                for p in patchers:
                    with suppress(Exception):
                        p.stop()

        return _cm()

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

    def url_export(self, model_name):
        """POST endpoint for ``ModelExportView`` — ``api/<model>/export``."""
        return self._url(
            "model-export",
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
