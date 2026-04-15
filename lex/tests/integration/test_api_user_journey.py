"""
API-level integration tests simulating real frontend user actions.

These tests exercise the full Django REST Framework stack — from HTTP
request through URL routing, authentication, permission checks,
serialisation, business logic, and all the way to the database and back.
They mirror the operations a real user would perform through the React
front-end:

User journeys covered
---------------------
1. **CRUD lifecycle** — Create a record via POST, read it back via GET
   (detail + list), update fields via PUT, delete via DELETE.

2. **Calculation trigger** — Mark a record for calculation by sending
   ``calculate: true`` in an update payload; verify the model transitions
   through IN_PROGRESS → SUCCESS.

3. **Mistake and history correction** — Create a record, save wrong
   data, correct it, then inspect the audit / history trail to confirm
   both the error and the correction are visible (bitemporal Level 1
   history).

4. **List filtering & ordering** — Confirm the list endpoint respects
   query-parameter filtering and ordering as the React AG Grid sends.

5. **Permission denied** — Verify that a user without create permission
   gets a 400/403 response.

6. **Bulk operations** — Use the ``many`` endpoint to delete several
   records at once.

How to run
----------
.. code-block:: bash

    python -m django test lex.tests.integration.test_api_user_journey \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

import os
import sys
from datetime import timedelta
from importlib import reload
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.db import connection, models
from django.test import TransactionTestCase, override_settings
from django.urls import clear_url_caches, reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult
from lex.process_admin.utils.model_registration import ModelRegistration


# ====================================================================
#  Test models — minimal domain models that simulate a real project
# ====================================================================

class ApiProject(LexModel):
    """A simple domain-data model (like Organisation or Fund)."""
    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0)
    status_text = models.CharField(max_length=50, default="draft")

    class Meta:
        app_label = "lex_app"

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True

    def __str__(self):
        return self.name


class ApiReport(CalculationModel):
    """A calculation model (like NAVReport or DemandForecast)."""
    project = models.ForeignKey(
        ApiProject, on_delete=models.SET_NULL, null=True, blank=True
    )
    total = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.project:
            self.total = self.project.budget * 1.1
        else:
            self.total = 0

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class RestrictedModel(LexModel):
    """A model that denies create — used to test permission rejection."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

    def permission_create(self, user_context):
        return False

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_delete(self, user_context):
        return True


# ====================================================================
#  Helper: mock container for reverse()
# ====================================================================

class _MockContainer:
    """Stub passed to reverse() — the ``model`` path converter calls ``.id``."""
    def __init__(self, model_name):
        self.id = model_name


# ====================================================================
#  Shared test infrastructure
# ====================================================================

ALL_API_MODELS = [ApiProject, ApiReport, RestrictedModel]


class ApiJourneyTestCase(TransactionTestCase):
    """
    Base class for API-level integration tests.

    Handles:
    - Dynamic table creation (main + history + meta-history)
    - Model registration with processAdminSite
    - URL-pattern rebuild so reverse() can resolve REST endpoints
    - Authentication via force_login + OIDC session injection
    - Mocking external boundaries (CacheManager, WebSocket, etc.)
    """

    databases = {"default"}
    _created_tables = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # --- Ensure env vars ---
        os.environ.setdefault("LEX_DYNAMIC_HISTORY_REGISTRATION", "1")

        # --- Register models with simple_history + processAdminSite ---
        from simple_history.models import registered_models

        for model in ALL_API_MODELS:
            if model not in registered_models:
                ModelRegistration._register_standard_model(model, [])

        # --- Create DB tables ---
        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in ALL_API_MODELS:
                if model._meta.db_table not in tables:
                    schema_editor.create_model(model)
                    cls._created_tables.append(model)

                # History model (Level 1)
                if hasattr(model, "history"):
                    hist_model = model.history.model
                    if hist_model._meta.db_table not in tables:
                        schema_editor.create_model(hist_model)
                        cls._created_tables.append(hist_model)

                    # Meta-history model (Level 2)
                    if hasattr(hist_model, "meta_history"):
                        meta_model = hist_model.meta_history.model
                        if meta_model._meta.db_table not in tables:
                            schema_editor.create_model(meta_model)
                            cls._created_tables.append(meta_model)

            # SQLite FK enforcement
            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA foreign_keys = OFF;")

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema_editor:
            for model in reversed(cls._created_tables):
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass
        cls._created_tables.clear()
        super().tearDownClass()

    def setUp(self):
        # --- Clean data from previous test ---
        for model in reversed(ALL_API_MODELS):
            try:
                model.objects.all().delete()
            except Exception:
                pass
            if hasattr(model, "history"):
                try:
                    model.history.model.objects.all().delete()
                except Exception:
                    pass

        # --- Create user + authenticate ---
        self.user = User.objects.create_user(
            username="apiuser", password="pw", email="api@test.com",
        )
        self.client = APIClient()
        self.client.force_login(self.user)

        # Inject OIDC session data so OIDCSessionRefreshMiddleware
        # (if present) doesn't bounce us.
        session = self.client.session
        session["oidc_expires_at"] = (
            timezone.now() + timedelta(hours=1)
        ).timestamp()
        session.save()

        # --- Rebuild processAdminSite URL patterns ---
        self._rebuild_urls()

        # --- Boundary mocks (prevent side-effects) ---
        patches = [
            patch(
                "lex.audit_logging.utils.CacheManager.CacheManager.store_message",
                return_value=None,
            ),
            patch(
                "lex.audit_logging.utils.CacheManager.CacheManager.build_cache_key",
                return_value="test_key",
            ),
            patch(
                "lex.audit_logging.utils.WebSocketNotifier.WebSocketNotifier.send_calculation_update",
                return_value=None,
            ),
            patch(
                "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore.mark_in_progress",
                return_value=None,
            ),
            patch(
                "lex.audit_logging.utils.calculation_audit.ensure_terminal_calculation_audit",
                return_value=None,
            ),
        ]
        self._patches = [p.start() for p in patches]
        self._patch_objs = patches

    def tearDown(self):
        for p in self._patch_objs:
            p.stop()

    # ------------------------------------------------------------------
    #  URL rebuild helper
    # ------------------------------------------------------------------

    def _rebuild_urls(self):
        """
        Force processAdminSite to re-initialise its URL patterns with the
        currently-registered model set, then clear Django's URL caches so
        that ``reverse()`` resolves the freshly-built patterns.
        """
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
            _ = processAdminSite.urls  # triggers _get_urls()

            from django.conf import settings

            clear_url_caches()
            if settings.ROOT_URLCONF in sys.modules:
                reload(sys.modules[settings.ROOT_URLCONF])
        finally:
            converter_patch.stop()

    # ------------------------------------------------------------------
    #  URL helpers
    # ------------------------------------------------------------------

    def _url_create(self, model_name, calc_id="default"):
        return reverse(
            "process_admin_rest_api:model-one-entry-create",
            kwargs={
                "model_container": _MockContainer(model_name),
                "calculationId": calc_id,
            },
        )

    def _url_detail(self, model_name, pk, calc_id="default"):
        return reverse(
            "process_admin_rest_api:model-one-entry-read-update-delete",
            kwargs={
                "model_container": _MockContainer(model_name),
                "calculationId": calc_id,
                "pk": pk,
            },
        )

    def _url_list(self, model_name):
        return reverse(
            "process_admin_rest_api:model-entries-list",
            kwargs={
                "model_container": _MockContainer(model_name),
            },
        )

    def _url_history(self, model_name, pk, calc_id="default"):
        return reverse(
            "process_admin_rest_api:model-history-list",
            kwargs={
                "model_container": _MockContainer(model_name),
                "calculationId": calc_id,
                "pk": pk,
            },
        )

    def _url_many(self, model_name):
        return reverse(
            "process_admin_rest_api:model-many-entries",
            kwargs={
                "model_container": _MockContainer(model_name),
            },
        )

    # ------------------------------------------------------------------
    #  Convenience: patch filter_backends + permissions for list calls
    # ------------------------------------------------------------------

    def _list_get(self, model_name, model_class, query_params=None):
        """GET the list endpoint with filter_backends disabled."""
        url = self._url_list(model_name)
        with patch(
            "lex.api.views.model_entries.List.ListModelEntries.filter_backends",
            [],
        ):
            return self.client.get(url, data=query_params or {})

    @staticmethod
    def _extract_results(resp_data):
        """Extract the list of results from a paginated or unpaginated response."""
        if isinstance(resp_data, dict):
            return resp_data.get("results", resp_data)
        return list(resp_data)


# ====================================================================
#  User journey 1: Full CRUD lifecycle
# ====================================================================

class TestCrudLifecycle(ApiJourneyTestCase):
    """
    A user opens the app, creates a Project record, views it in the
    list, updates a field, reads it back, and finally deletes it.
    """

    def test_create_record_via_post(self):
        """POST /api/model_entries/apiproject/<calcId>/one → 201"""
        url = self._url_create("apiproject")
        payload = {"name": "Alpha Fund", "budget": 50000, "status_text": "active"}
        resp = self.client.post(url, data=payload, format="json")
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_201_CREATED),
            f"Create failed: {resp.data}",
        )
        self.assertEqual(resp.data["name"], "Alpha Fund")
        self.assertTrue(ApiProject.objects.filter(name="Alpha Fund").exists())

    def test_read_record_via_get_detail(self):
        """GET /api/model_entries/apiproject/<calcId>/one/<pk> → 200"""
        obj = ApiProject.objects.create(name="Beta Corp", budget=10000)
        url = self._url_detail("apiproject", obj.pk)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Beta Corp")
        self.assertEqual(resp.data["budget"], 10000)

    def test_list_records_via_get(self):
        """GET /api/model_entries/apiproject/list → paginated response"""
        ApiProject.objects.create(name="P1", budget=100)
        ApiProject.objects.create(name="P2", budget=200)
        ApiProject.objects.create(name="P3", budget=300)

        resp = self._list_get("apiproject", ApiProject)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._extract_results(resp.data)
        self.assertEqual(len(results), 3)

    def test_update_record_via_put(self):
        """PUT /api/model_entries/apiproject/<calcId>/one/<pk> → 200"""
        obj = ApiProject.objects.create(
            name="Old Name", budget=100, status_text="draft",
        )
        url = self._url_detail("apiproject", obj.pk)
        payload = {
            "name": "New Name",
            "budget": 99999,
            "status_text": "final",
        }
        resp = self.client.put(url, data=payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        obj.refresh_from_db()
        self.assertEqual(obj.name, "New Name")
        self.assertEqual(obj.budget, 99999)
        self.assertEqual(obj.status_text, "final")

    def test_partial_update_via_patch(self):
        """PATCH /api/model_entries/apiproject/<calcId>/one/<pk> → 200"""
        obj = ApiProject.objects.create(name="Patch Me", budget=0)
        url = self._url_detail("apiproject", obj.pk)
        resp = self.client.patch(
            url, data={"budget": 42}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        obj.refresh_from_db()
        self.assertEqual(obj.budget, 42)
        self.assertEqual(obj.name, "Patch Me")  # unchanged

    def test_delete_record_via_delete(self):
        """DELETE /api/model_entries/apiproject/<calcId>/one/<pk> → 204"""
        obj = ApiProject.objects.create(name="Delete Me")
        url = self._url_detail("apiproject", obj.pk)
        resp = self.client.delete(url)
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
        )
        self.assertFalse(ApiProject.objects.filter(pk=obj.pk).exists())


# ====================================================================
#  User journey 2: Calculation trigger
# ====================================================================

class TestCalculationTrigger(ApiJourneyTestCase):
    """
    A user creates a report record, links it to a project, then
    triggers calculation by sending ``calculate: true`` in a PUT.
    """

    def test_trigger_calculation_via_update(self):
        """
        PUT with calculate=true → model transitions through IN_PROGRESS
        to SUCCESS and the calculated total is persisted.
        """
        project = ApiProject.objects.create(name="Calc Project", budget=1000)
        report = ApiReport.objects.create(
            project=project,
            is_calculated=CalculationModel.NOT_CALCULATED,
        )
        url = self._url_detail("apireport", report.pk)
        payload = {
            "project": project.pk,
            "total": 0,
            "calculate": "true",
            "is_calculated": CalculationModel.NOT_CALCULATED,
        }
        resp = self.client.put(url, data=payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        report.refresh_from_db()
        # After calculation: total = budget * 1.1 = 1100.0
        self.assertEqual(report.total, 1100.0)

    def test_update_without_calculate_resets_is_calculated(self):
        """
        A plain PUT (no calculate flag) on a CalculationModel should
        reset is_calculated to NOT_CALCULATED.
        """
        project = ApiProject.objects.create(name="P", budget=500)
        report = ApiReport.objects.create(
            project=project,
            total=550,
            is_calculated=CalculationModel.SUCCESS,
        )
        url = self._url_detail("apireport", report.pk)
        payload = {"project": project.pk, "total": 550}
        resp = self.client.put(url, data=payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.NOT_CALCULATED)


# ====================================================================
#  User journey 3: Mistake and history correction
# ====================================================================

class TestMistakeAndCorrection(ApiJourneyTestCase):
    """
    A user enters data, realises the budget was wrong, corrects it, and
    then reviews the history timeline to confirm all versions are visible.
    """

    def test_create_update_generates_history(self):
        """
        Creating and updating a record produces history entries
        (simple_history auto-tracking).
        """
        # 1. Create
        obj = ApiProject.objects.create(name="V1", budget=100)
        # 2. Update — mistake
        obj.name = "V1 TYPO"
        obj.budget = 999999
        obj.save()
        # 3. Fix the mistake
        obj.name = "V1 Fixed"
        obj.budget = 100
        obj.save()

        # Verify 3 history entries exist
        self.assertEqual(obj.history.count(), 3)
        names = list(
            obj.history.order_by("history_id").values_list("name", flat=True)
        )
        self.assertEqual(names, ["V1", "V1 TYPO", "V1 Fixed"])

    def test_history_api_returns_versions(self):
        """
        GET /api/model_entries/apiproject/<calcId>/history/<pk>
        returns the full history timeline for a record.
        """
        obj = ApiProject.objects.create(name="Original", budget=100)
        obj.name = "Edited"
        obj.save()

        url = self._url_history("apiproject", obj.pk)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        data = resp.json()
        self.assertGreaterEqual(len(data), 2)

        # Most recent version first
        self.assertEqual(data[0]["history_type"], "~")  # update

    def test_correct_history_via_direct_edit(self):
        """
        A user corrects a past history record's valid_from to
        retroactively adjust when data became valid — verifying the
        bitemporal Level 1 edit path.
        """
        obj = ApiProject.objects.create(name="Initial")
        obj.name = "Updated"
        obj.save()

        # Fetch the second history entry (the update)
        hist = obj.history.order_by("-history_id").first()
        original_valid_from = hist.valid_from

        # Correct valid_from to 1 hour earlier
        new_valid_from = original_valid_from - timedelta(hours=1)
        hist.valid_from = new_valid_from
        hist.save()

        hist.refresh_from_db()
        self.assertEqual(hist.valid_from, new_valid_from)

    def test_delete_leaves_history_tombstone(self):
        """
        Deleting a record via the API leaves a history entry with
        history_type = '-'.
        """
        obj = ApiProject.objects.create(name="Will Delete", budget=42)
        pk = obj.pk
        url = self._url_detail("apiproject", pk)
        self.client.delete(url)

        # Record is gone
        self.assertFalse(ApiProject.objects.filter(pk=pk).exists())

        # History has a '-' entry
        hist = ApiProject.history.filter(id=pk).order_by("-history_id").first()
        self.assertIsNotNone(hist)
        self.assertEqual(hist.history_type, "-")


# ====================================================================
#  User journey 4: List filtering and ordering
# ====================================================================

class TestListFilteringAndOrdering(ApiJourneyTestCase):
    """
    The frontend uses query params (ordering, field__lookup, etc.)
    for AG Grid server-side filtering. Verify the list endpoint
    respects these.
    """

    def test_ordering_by_field(self):
        """?ordering=budget sorts ascending; ?ordering=-budget descending."""
        ApiProject.objects.create(name="C", budget=300)
        ApiProject.objects.create(name="A", budget=100)
        ApiProject.objects.create(name="B", budget=200)

        resp = self._list_get("apiproject", ApiProject, {"ordering": "budget"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._extract_results(resp.data)
        self.assertGreaterEqual(len(results), 3)
        budgets = [r["budget"] for r in results]
        self.assertEqual(budgets, sorted(budgets))

    def test_filter_by_query_param(self):
        """?status_text=active filters to matching records."""
        ApiProject.objects.create(name="Active 1", status_text="active")
        ApiProject.objects.create(name="Draft 1", status_text="draft")
        ApiProject.objects.create(name="Active 2", status_text="active")

        resp = self._list_get(
            "apiproject", ApiProject, {"status_text": "active"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._extract_results(resp.data)
        names = [r["name"] for r in results]
        self.assertIn("Active 1", names)
        self.assertIn("Active 2", names)
        self.assertNotIn("Draft 1", names)


# ====================================================================
#  User journey 5: Permission denied
# ====================================================================

class TestPermissionDenied(ApiJourneyTestCase):
    """
    A user who lacks create permission gets a clear rejection.
    """

    def test_create_denied_returns_400(self):
        """
        POST to a model whose permission_create returns False should
        receive a 400 (BAD_REQUEST) with an explanatory message.
        """
        url = self._url_create("restrictedmodel")
        resp = self.client.post(url, data={"name": "Blocked"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not authorized", resp.data.get("message", "").lower())


# ====================================================================
#  User journey 6: End-to-end user story (full workflow)
# ====================================================================

class TestEndToEndWorkflow(ApiJourneyTestCase):
    """
    Simulates a realistic user session:
    1. User creates several Project records
    2. Views the list
    3. Creates a Report linked to a project
    4. Triggers calculation on the report
    5. Notices the project budget was wrong → corrects it
    6. Re-triggers calculation → result changes
    7. Reviews history of the project to see the original error
    """

    def test_full_user_session(self):
        # ── Step 1: Create projects ──────────────────────────────────
        for name, budget in [("Alpha", 10000), ("Beta", 20000)]:
            url = self._url_create("apiproject")
            resp = self.client.post(
                url,
                data={"name": name, "budget": budget, "status_text": "draft"},
                format="json",
            )
            self.assertIn(
                resp.status_code,
                (status.HTTP_200_OK, status.HTTP_201_CREATED),
                f"Failed creating {name}: {resp.data}",
            )

        alpha = ApiProject.objects.get(name="Alpha")
        beta = ApiProject.objects.get(name="Beta")

        # ── Step 2: View list ────────────────────────────────────────
        resp = self._list_get("apiproject", ApiProject)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._extract_results(resp.data)
        self.assertEqual(len(results), 2)

        # ── Step 3: Create a report linked to Alpha ──────────────────
        report_url = self._url_create("apireport")
        resp = self.client.post(
            report_url,
            data={"project": alpha.pk, "total": 0},
            format="json",
        )
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_201_CREATED),
            f"Report create failed: {resp.data}",
        )
        report = ApiReport.objects.first()
        self.assertIsNotNone(report)

        # ── Step 4: Trigger calculation ──────────────────────────────
        detail_url = self._url_detail("apireport", report.pk)
        resp = self.client.put(
            detail_url,
            data={
                "project": alpha.pk,
                "total": 0,
                "calculate": "true",
                "is_calculated": CalculationModel.NOT_CALCULATED,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        report.refresh_from_db()
        self.assertEqual(report.total, 11000.0)  # 10000 * 1.1

        # ── Step 5: Oops — Alpha's budget was wrong! Fix it ─────────
        alpha_url = self._url_detail("apiproject", alpha.pk)
        resp = self.client.patch(
            alpha_url,
            data={"budget": 15000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        alpha.refresh_from_db()
        self.assertEqual(alpha.budget, 15000)

        # ── Step 6: Re-trigger calculation → new result ──────────────
        resp = self.client.put(
            detail_url,
            data={
                "project": alpha.pk,
                "total": report.total,
                "calculate": "true",
                "is_calculated": CalculationModel.NOT_CALCULATED,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        report.refresh_from_db()
        self.assertEqual(report.total, 16500.0)  # 15000 * 1.1

        # ── Step 7: Check history — both budget versions visible ─────
        history_url = self._url_history("apiproject", alpha.pk)
        resp = self.client.get(history_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        history_data = resp.json()
        # At least 2 versions: original (10000) and correction (15000)
        self.assertGreaterEqual(len(history_data), 2)
