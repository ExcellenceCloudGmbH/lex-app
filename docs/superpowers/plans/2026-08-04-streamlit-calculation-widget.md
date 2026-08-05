# Streamlit Calculation Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `lex_calculation()` — a native Streamlit widget that triggers a calculation on one record and shows its live status — so a dashboard no longer has to embed a whole React table to run one calculation.

**Architecture:** The widget never imports Django. It triggers via `PATCH …?calculate=true` over HTTP as the user (the same path the React UI uses) and reads state from a new read-only status endpoint, polling with a self-terminating `st.fragment`. An in-process ORM call was rejected: it would bypass DRF permissions, audit actor resolution and the `_defer_calculate_hook` trigger path.

**Tech Stack:** Django REST Framework, Streamlit 1.58 (`st.fragment`), `requests`, `lex.lex_app.design_system` tokens, pytest via `python -m lex pytest`.

**Spec:** `docs/superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md`

---

## File Structure

| File | Responsibility |
| --- | --- |
| `lex/api/views/calculations/CalculationStatus.py` | **Create.** Read-only endpoint returning status + optional bounded log for one record |
| `lex/process_admin/sites/process_admin_site.py` | **Modify.** Register the URL |
| `lex/lex_app/streamlit/_client.py` | **Create.** Backend URL resolution, bearer token, GET/PATCH, typed errors. Action-agnostic so a future `lex_action()` reuses it |
| `lex/lex_app/streamlit/calculation.py` | **Create.** Rendering, fragment poll loop, session state |
| `lex/lex_app/streamlit/__init__.py` | **Modify.** Becomes the public surface exporting `lex_view` + `lex_calculation` |
| `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py` | **Create.** Batch 10o, type E |
| `lex/test_project/tests/init/test_1ab_calculation_widget.py` | **Create.** Batch 1ab, type U |
| Plan shards for clusters 1 and 10 | **Modify.** allocation/batches/cluster + session fragment + regenerated dashboard |

**Allocations (verified against the branch):** cluster 1 is at `max_scenario: 222`, letters `a`–`z` + `aa` used → letter **`ab`**, scenarios **1.223–1.231**. Cluster 10 is at `max_scenario: 71`, letters `a,b,c,e,f,g,h,i,j,k,l,m,n` → letter **`o`**, scenarios **10.72–10.79**.

---

### Task 1: Status endpoint returns the calculation state

**Files:**
- Create: `lex/api/views/calculations/CalculationStatus.py`
- Modify: `lex/process_admin/sites/process_admin_site.py`
- Test: `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`:

```python
"""Read-only calculation-status endpoint for the Streamlit widget.

Intent: a Streamlit dashboard polls this every couple of seconds to render a
calculation's live state. Polling the full record serialization to learn one
enum is wasteful on wide models and cannot carry the log tail, so the widget
gets a purpose-built endpoint. It must expose exactly the state the widget
renders and nothing the caller is not allowed to see -- a response that
confirms a record exists and errored, to someone who cannot read that record,
is a leak.

Cluster 10o — scenarios 10.72–10.79. Type: E.
Covers: lex/api/views/calculations/CalculationStatus.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v
"""

from __future__ import annotations

import pytest

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ApiLayerCalc

pytestmark = pytest.mark.api_layer

CALC = "apilayercalc"


class TestCluster10o_CalculationStatusEndpoint(E2ETestCase):
    """Cluster 10o: the status contract the Streamlit widget polls."""

    # Required by E2ETestCase: drives dynamic table creation in setUpClass.
    # Without it every test dies on "relation lex_app_apilayercalc does not exist".
    e2e_models = ALL_MODELS

    def url_status(self, model_name: str, pk: int) -> str:
        return f"/api/model_entries/{model_name}/{pk}/calculation-status"

    def test_10_72_returns_status_for_a_never_calculated_record(self):
        """
        Scenario 10.72: a fresh record reports NOT_CALCULATED with no run data.
        Given: a calculation record that has never been run
        When: the widget polls its status
        Then: status is NOT_CALCULATED and the timing fields are null, so the
              widget can render "Never run" without guessing
        """
        item = ApiLayerCalc.objects.create(name="fresh")

        resp = self.client.get(self.url_status(CALC, item.pk))

        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], CalculationModel.NOT_CALCULATED)
        self.assertIsNone(body["started_at"], "A record never run has no start time.")
        self.assertIsNone(body["finished_at"])
        self.assertIsNone(body["error"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py::TestCluster10o_CalculationStatusEndpoint::test_10_72_returns_status_for_a_never_calculated_record -v`

Expected: FAIL, because the URL is not registered. Note the failure shape: an
unregistered route falls through to the SPA catch-all, which returns a streaming
`FileResponse`, so the assertion dies on `resp.content` rather than showing a
clean 404. That is still a genuine red state.

- [ ] **Step 3: Write the endpoint**

Create `lex/api/views/calculations/CalculationStatus.py` (`LOG_TAIL_LIMIT` and the
read-permission filter arrive in Tasks 3 and 4 — do not add them yet, and do not
document them before they exist):

```python
"""Read-only calculation state for one record.

Serves the Streamlit calculation widget, which polls this while a calculation
runs. Deliberately narrow: it returns only what the widget renders, so polling
stays cheap regardless of how wide the model is.
"""

from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

class CalculationStatus(APIView):
    http_method_names = ["get"]
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        model_class = model_container.model_class
        pk = self.kwargs["pk"]

        instance = model_class.objects.filter(pk=pk).first()
        if instance is None:
            return JsonResponse({"detail": "Not found."}, status=404)

        return JsonResponse(self._envelope(instance))

    def _envelope(self, instance) -> dict:
        return {
            "status": instance.is_calculated,
            "error": self._error_of(instance),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
        }

    @staticmethod
    def _error_of(instance):
        """Read the subclass-convention error field, if the model has one."""
        for field in ("calculation_error_message", "error_message"):
            value = getattr(instance, field, None)
            if value:
                return value
        return None
```

- [ ] **Step 4: Register the URL**

In `lex/process_admin/sites/process_admin_site.py`, add the import next to the other calculation views (near line 7):

```python
from lex.api.views.calculations.CalculationStatus import CalculationStatus
```

and add this `path(...)` entry immediately after the `init-calculation-logs` entry (near line 250):

```python
            path(
                "api/model_entries/<model:model_container>/<int:pk>/calculation-status",
                CalculationStatus.as_view(),
                name="calculation-status",
            ),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v`

Expected: PASS — 1 passed.

- [ ] **Step 6: Commit**

```bash
git add lex/api/views/calculations/CalculationStatus.py \
        lex/process_admin/sites/process_admin_site.py \
        lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py
git commit -m "feat(api): read-only calculation-status endpoint for the Streamlit widget"
```

---

### Task 2: Endpoint reports every terminal state and the error text

**Files:**
- Test: `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestCluster10o_CalculationStatusEndpoint`:

```python
    def test_10_73_reports_each_terminal_status_distinctly(self):
        """
        Scenario 10.73: ABORTED and CANCELLED are not collapsed into ERROR.
        Given: records in each terminal state
        When: the widget polls each
        Then: each reports its own status. An aborted row is a stale state, not
              a failure, and the widget renders a re-run nudge rather than an
              error for it -- collapsing them is what made incident 1410 hard
              to read
        """
        for state in (
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
            CalculationModel.CANCELLED,
            CalculationModel.IN_PROGRESS,
        ):
            item = ApiLayerCalc.objects.create(name=f"c-{state}")
            ApiLayerCalc.objects.filter(pk=item.pk).update(is_calculated=state)

            resp = self.client.get(self.url_status(CALC, item.pk))

            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertEqual(
                resp.json()["status"], state,
                f"{state} must be reported as itself, not mapped to another state.",
            )

    def test_10_74_surfaces_the_calculation_error_message(self):
        """
        Scenario 10.74: a failed calculation returns its error text.
        Given: a record in ERROR with a calculation_error_message
        When: the widget polls
        Then: the message is in the envelope, so the dashboard can explain the
              failure without the user opening the table to find out why
        """
        item = ApiLayerCalc.objects.create(name="failed")
        ApiLayerCalc.objects.filter(pk=item.pk).update(
            is_calculated=CalculationModel.ERROR,
            calculation_error_message="ValueError: no FX rate for 2026-03-31",
        )

        resp = self.client.get(self.url_status(CALC, item.pk))

        self.assertEqual(resp.json()["error"], "ValueError: no FX rate for 2026-03-31")

    def test_10_75_unknown_pk_is_a_404(self):
        """
        Scenario 10.75: a stale pk in a dashboard does not 500.
        Given: a pk that does not exist
        When: the widget polls
        Then: 404, so the widget renders "Record not found" rather than an
              exception that would kill everything below it on the page
        """
        resp = self.client.get(self.url_status(CALC, 999999))
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v -k "10_73 or 10_74 or 10_75"`

Expected: 10.73 and 10.75 PASS already (the Task 1 implementation covers them); 10.74 PASS. If any fail, fix `CalculationStatus._envelope` / `_error_of` before continuing — do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py
git commit -m "test(10o): pin terminal states and error text on the status endpoint"
```

---

### Task 3: Endpoint enforces the record's read permission

**Files:**
- Modify: `lex/api/views/calculations/CalculationStatus.py`
- Test: `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`

- [ ] **Step 1: Write the failing test**

Append to the test class:

```python
    def test_10_76_unreadable_record_is_indistinguishable_from_missing(self):
        """
        Scenario 10.76: the endpoint never confirms a record the caller cannot read.
        Given: a record the requesting user has no read permission for
        When: the widget polls its status
        Then: the response is identical to the one for a nonexistent pk. A
              distinguishable 403 would confirm the record exists and leak its
              state to someone not allowed to see it
        """
        item = ApiLayerCalc.objects.create(name="secret")
        ApiLayerCalc.objects.filter(pk=item.pk).update(is_calculated=CalculationModel.ERROR)

        with self.as_user_without_read_permission(ApiLayerCalc):
            denied = self.client.get(self.url_status(CALC, item.pk))
        missing = self.client.get(self.url_status(CALC, 999999))

        self.assertEqual(denied.status_code, missing.status_code)
        self.assertEqual(denied.json(), missing.json())
```

If `E2ETestCase` has no `as_user_without_read_permission` helper, add it in the same commit:

```python
    # lex/test_project/tests/_e2e_test_case.py
    @contextlib.contextmanager
    def as_user_without_read_permission(self, model_class):
        """Run the block with permission_read denying everything for model_class."""
        from lex.core.models.LexModel import PermissionResult

        original = getattr(model_class, "permission_read", None)
        model_class.permission_read = lambda self, uc: PermissionResult.deny_all("test")
        try:
            yield
        finally:
            if original is None:
                del model_class.permission_read
            else:
                model_class.permission_read = original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v -k 10_76`

Expected: FAIL — the endpoint returns 200 with the record's state, because it does not consult `permission_read`.

- [ ] **Step 3: Apply the read filter**

**Delegate to the filter backend the list view already uses — do not hand-roll a check.**
`UserReadRestrictionFilterBackend` (`lex/api/views/model_entries/filter_backends.py`,
declared by `ListModelEntries` at `List.py:439`) is the only thing that handles all
the real cases: models with a custom `permission_read` (per-row `result.allowed`),
models on the default `LexModel.permission_read` (translated into a **queryset
filter** from Keycloak scopes, never a boolean), legacy `can_read`, and the AuditLog
special cases.

```python
    def _readable_or_none(self, request, model_class, pk):
        """The record, or None when it is missing OR unreadable by this caller."""
        from lex.api.views.model_entries.filter_backends import (
            UserReadRestrictionFilterBackend,
        )

        queryset = model_class.objects.filter(pk=pk)
        readable = UserReadRestrictionFilterBackend().filter_queryset(
            request, queryset, self
        )
        return readable.first()
```

Two traps this avoids, both of which a hand-rolled version walks into:

- `PermissionResult` exposes **`allowed`**, not `is_allowed`. A `getattr(result,
  "is_allowed", True)` silently defaults to *allow* on every deny — a security hole
  that passes review because it looks defensive.
- Models on the **default** `permission_read` never reach a boolean at all; their
  permission is expressed as a queryset filter. A boolean-only check leaves the
  majority of models completely unfiltered.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v`

Expected: PASS — all scenarios so far.

- [ ] **Step 5: Commit**

```bash
git add lex/api/views/calculations/CalculationStatus.py \
        lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py \
        lex/test_project/tests/_e2e_test_case.py
git commit -m "fix(api): status endpoint applies the record's read permission"
```

---

### Task 4: Bounded log tail, off by default

**Files:**
- Modify: `lex/api/views/calculations/CalculationStatus.py`
- Test: `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_10_77_log_is_absent_unless_requested(self):
        """
        Scenario 10.77: the default poll never pays for log data.
        Given: a record with calculation log lines
        When: the widget polls without include_log
        Then: no log key in the envelope — show_log defaults to False, and a
              dashboard that never shows logs must not query them every 2s
        """
        item = ApiLayerCalc.objects.create(name="quiet")
        self._make_logs(item, count=3)

        body = self.client.get(self.url_status(CALC, item.pk)).json()

        self.assertNotIn("log", body)

    def test_10_78_log_is_bounded_and_reports_truncation(self):
        """
        Scenario 10.78: a long calculation cannot bloat a 2-second poll.
        Given: more log lines than the tail limit
        When: the widget polls with include_log=true
        Then: at most LOG_TAIL_LIMIT lines come back, newest last, and
              log_truncated says there were more
        """
        from lex.api.views.calculations.CalculationStatus import LOG_TAIL_LIMIT

        item = ApiLayerCalc.objects.create(name="chatty")
        self._make_logs(item, count=LOG_TAIL_LIMIT + 10)

        body = self.client.get(
            self.url_status(CALC, item.pk) + "?include_log=true"
        ).json()

        self.assertEqual(len(body["log"]), LOG_TAIL_LIMIT)
        self.assertTrue(body["log_truncated"], "Callers must know lines were dropped.")

    def _make_logs(self, instance, count: int):
        """Create ``count`` CalculationLog rows attached to ``instance``."""
        from django.contrib.contenttypes.models import ContentType

        from lex.audit_logging.models.CalculationLog import CalculationLog

        ct = ContentType.objects.get_for_model(type(instance))
        for i in range(count):
            CalculationLog.objects.create(
                calculationId=f"calc-{instance.pk}",
                calculation_log=f"line {i}",
                content_type=ct,
                object_id=instance.pk,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v -k "10_77 or 10_78"`

Expected: 10.77 PASS (no log key yet), 10.78 FAIL with `KeyError: 'log'`.

- [ ] **Step 3: Add the log tail**

Add to `CalculationStatus`, and call it from `get`:

```python
    def get(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        pk = self.kwargs["pk"]

        instance = self._readable_or_none(request, model_container.model_class, pk)
        if instance is None:
            return JsonResponse({"detail": "Not found."}, status=404)

        envelope = self._envelope(instance)
        if request.query_params.get("include_log") == "true":
            envelope.update(self._log_tail(instance))
        return JsonResponse(envelope)

    @staticmethod
    def _log_tail(instance) -> dict:
        """Last LOG_TAIL_LIMIT log lines for this record, oldest first."""
        from django.contrib.contenttypes.models import ContentType

        from lex.audit_logging.models.CalculationLog import CalculationLog

        ct = ContentType.objects.get_for_model(type(instance))
        qs = CalculationLog.objects.filter(
            content_type=ct, object_id=instance.pk
        ).order_by("-timestamp", "-id")

        newest_first = list(
            qs.values_list("calculation_log", flat=True)[: LOG_TAIL_LIMIT + 1]
        )
        truncated = len(newest_first) > LOG_TAIL_LIMIT
        lines = newest_first[:LOG_TAIL_LIMIT]
        lines.reverse()
        return {"log": lines, "log_truncated": truncated}
```

The `LIMIT + 1` fetch is how truncation is detected without a second `COUNT` query.

- [ ] **Step 4: Run the whole batch**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v`

Expected: PASS — 7 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/api/views/calculations/CalculationStatus.py \
        lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py
git commit -m "feat(api): bounded calculation log tail behind include_log"
```

---

### Task 4b: Run timings from CalculationLog

The spec renders "Last run 12:04 · took 38s". There is no timestamp on the
record — since PR #675 `edited_at` deliberately is not one — so the timings come
from the first and last `CalculationLog` rows for that record.

**Files:**
- Modify: `lex/api/views/calculations/CalculationStatus.py`
- Test: `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_10_79_reports_run_timings_from_the_calculation_log(self):
        """
        Scenario 10.79: "last run" comes from the log, not the record.
        Given: a calculated record with log rows spanning a period
        When: the widget polls
        Then: started_at, finished_at and duration_seconds are populated from the
              first and last log rows. The record itself carries no timestamp —
              since PR #675 calculations deliberately do not stamp edited_at — so
              the log is the only source
        """
        item = ApiLayerCalc.objects.create(name="timed")
        ApiLayerCalc.objects.filter(pk=item.pk).update(is_calculated=CalculationModel.SUCCESS)
        self._make_logs(item, count=3)

        body = self.client.get(self.url_status(CALC, item.pk)).json()

        self.assertIsNotNone(body["started_at"], "First log row is the start.")
        self.assertIsNotNone(body["finished_at"], "Last log row is the finish.")
        self.assertIsNotNone(body["duration_seconds"])
        self.assertGreaterEqual(body["duration_seconds"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v -k 10_79`

Expected: FAIL — `started_at` is None; `_envelope` hardcodes the timing fields.

- [ ] **Step 3: Populate the timings**

Replace `_envelope` in `lex/api/views/calculations/CalculationStatus.py`:

```python
    def _envelope(self, instance) -> dict:
        started, finished = self._run_window(instance)
        duration = (
            (finished - started).total_seconds()
            if started and finished
            else None
        )
        return {
            "status": instance.is_calculated,
            "error": self._error_of(instance),
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_seconds": duration,
        }

    @staticmethod
    def _run_window(instance):
        """(first, last) CalculationLog timestamps for this record, or (None, None).

        The record has no timestamp of its own: since PR #675 a calculation-owned
        save deliberately does not stamp edited_at, so the log is the only record
        of when a run happened.
        """
        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Max, Min

        from lex.audit_logging.models.CalculationLog import CalculationLog

        ct = ContentType.objects.get_for_model(type(instance))
        window = CalculationLog.objects.filter(
            content_type=ct, object_id=instance.pk
        ).aggregate(first=Min("timestamp"), last=Max("timestamp"))
        return window["first"], window["last"]
```

One aggregate query, so adding timings does not add a per-poll round trip.

- [ ] **Step 4: Run the batch**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v`

Expected: PASS — 8 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/api/views/calculations/CalculationStatus.py \
        lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py
git commit -m "feat(api): report calculation run timings from CalculationLog"
```

---

### Task 5: HTTP client for the widget

**Files:**
- Create: `lex/lex_app/streamlit/_client.py`
- Test: `lex/test_project/tests/init/test_1ab_calculation_widget.py`

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1ab_calculation_widget.py`:

```python
"""The Streamlit calculation widget and its backend client.

Intent: a dashboard author should be able to trigger one calculation and watch
it, without embedding a React table. The widget talks to the backend over HTTP
as the user -- never the ORM -- so permissions, audit actor and the
_defer_calculate_hook trigger path stay identical to the React UI. A second way
to start a calculation is what produced the edited_at bug (PR #675).

Two regressions these scenarios exist to prevent: polling that never stops
(a dashboard silently hammering the backend forever), and any failure path that
raises out of the widget (Streamlit renders top-to-bottom, so an exception kills
everything below it on the page).

Cluster 1ab — scenarios 1.223–1.231. Type: U.
Covers: lex/lex_app/streamlit/_client.py, lex/lex_app/streamlit/calculation.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


class TestCluster01ab_CalculationClient(SimpleTestCase):
    """Cluster 1ab: the widget's HTTP client."""

    def test_1_223_resolves_the_in_cluster_backend_from_the_instance_id(self):
        """
        Scenario 1.223: no new configuration is needed to reach the backend.
        Given: only INSTANCE_RESOURCE_IDENTIFIER, which the Streamlit pod
               already receives from the dpag configmap
        When: the client resolves its base URL
        Then: it targets the in-cluster backend service, so shipping this widget
              needs no chart change and no new secret
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {"INSTANCE_RESOURCE_IDENTIFIER": "demo-prod-4"},
            clear=True,
        ):
            self.assertEqual(
                _client.resolve_api_base_url(),
                "http://lex-backend-demo-prod-4:7000",
            )

    def test_1_224_explicit_override_wins(self):
        """
        Scenario 1.224: a deployment can point the widget elsewhere.
        Given: LEX_API_URL set explicitly
        When: the client resolves its base URL
        Then: the override wins over the derived service name, matching the
              precedence embed._resolve_base_url already uses
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {"LEX_API_URL": "http://localhost:9999", "INSTANCE_RESOURCE_IDENTIFIER": "x"},
            clear=True,
        ):
            self.assertEqual(_client.resolve_api_base_url(), "http://localhost:9999")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v`

Expected: FAIL — `ModuleNotFoundError: lex.lex_app.streamlit._client`.

- [ ] **Step 3: Write the client**

Create `lex/lex_app/streamlit/_client.py`:

```python
"""HTTP access to the LEX backend from inside the Streamlit host.

Deliberately action-agnostic: this is the piece a future ``lex_action()`` reuses
unchanged, so generalising the widget is an additive module rather than a
refactor.

Never imports Django models. Every call goes over HTTP as the signed-in user, so
permissions, audit actor resolution and the ``calculate=true`` trigger path stay
identical to the React UI's.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

#: Seconds before a backend call is abandoned. Short: this runs inside a page
#: render, and a hung request would freeze the dashboard.
REQUEST_TIMEOUT = 10


class LexApiError(Exception):
    """A backend call did not succeed. Carries the HTTP status when there is one."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def resolve_api_base_url() -> str:
    """Base URL of the backend REST API.

    Priority:
      1. ``LEX_API_URL`` — explicit override
      2. the in-cluster service derived from ``INSTANCE_RESOURCE_IDENTIFIER``,
         which the Streamlit pod already receives from the dpag configmap
      3. localhost, for local development
    """
    override = os.getenv("LEX_API_URL")
    if override:
        return override.rstrip("/")

    instance = os.getenv("INSTANCE_RESOURCE_IDENTIFIER")
    if instance:
        port = "7001" if os.getenv("AI_PROCESS_ADMIN_CLIENT_ENABLED") == "true" else "7000"
        return f"http://lex-backend-{instance}:{port}"

    return "http://localhost:8000"


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def get_json(path: str, token: str, params: Optional[dict] = None) -> Dict[str, Any]:
    """GET ``path`` and return parsed JSON, or raise :class:`LexApiError`."""
    url = f"{resolve_api_base_url()}{path}"
    try:
        response = requests.get(
            url, headers=_headers(token), params=params, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        raise LexApiError(f"Backend unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise LexApiError(_message_of(response), status=response.status_code)
    return response.json()


def patch_json(path: str, token: str, params: Optional[dict] = None) -> Dict[str, Any]:
    """PATCH ``path`` and return parsed JSON, or raise :class:`LexApiError`."""
    url = f"{resolve_api_base_url()}{path}"
    try:
        response = requests.patch(
            url, headers=_headers(token), params=params, json={}, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        raise LexApiError(f"Backend unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise LexApiError(_message_of(response), status=response.status_code)
    return response.json() if response.content else {}


def _message_of(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    return payload.get("detail") or payload.get("message") or f"HTTP {response.status_code}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v`

Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/streamlit/_client.py \
        lex/test_project/tests/init/test_1ab_calculation_widget.py
git commit -m "feat(streamlit): backend HTTP client for widgets"
```

---

### Task 6: The widget — rendering every state without raising

**Files:**
- Create: `lex/lex_app/streamlit/calculation.py`
- Test: `lex/test_project/tests/init/test_1ab_calculation_widget.py`

- [ ] **Step 1: Write the failing tests**

Append a second class to the test file:

```python
class TestCluster01ab_CalculationWidget(SimpleTestCase):
    """Cluster 1ab: widget state, polling lifecycle and failure containment."""

    def test_1_225_polling_stops_on_a_terminal_status(self):
        """
        Scenario 1.225: a finished calculation stops the poll loop.
        Given: each terminal status
        When: the widget decides its poll interval
        Then: None — polling stops. Without this an idle dashboard keeps
              requesting status forever, which is a silent, permanent load on
              the backend that nobody would notice
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import poll_interval_for

        for state in (
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
            CalculationModel.CANCELLED,
            CalculationModel.NOT_CALCULATED,
        ):
            self.assertIsNone(
                poll_interval_for(state, requested=2.0),
                f"{state} is terminal — polling must stop.",
            )

    def test_1_226_polling_runs_only_while_in_progress(self):
        """
        Scenario 1.226: a running calculation is polled.
        Given: IN_PROGRESS
        When: the widget decides its poll interval
        Then: the requested interval, so the badge updates while work runs
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import poll_interval_for

        self.assertEqual(
            poll_interval_for(CalculationModel.IN_PROGRESS, requested=2.0), 2.0
        )

    def test_1_227_aborted_offers_a_rerun_nudge_and_error_does_not(self):
        """
        Scenario 1.227: ABORTED is a stale state, not a failure.
        Given: ABORTED and ERROR
        When: the widget decides how to present each
        Then: ABORTED gets a re-run nudge, ERROR gets the error treatment.
              Since PR #675 an aborted row means "interrupted, run it again";
              collapsing it into ERROR is what made incident 1410 hard to read
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import presentation_for

        aborted = presentation_for(CalculationModel.ABORTED)
        errored = presentation_for(CalculationModel.ERROR)

        self.assertTrue(aborted.suggests_rerun, "Aborted work should invite a re-run.")
        self.assertFalse(errored.suggests_rerun, "A real failure is not a re-run prompt.")
        self.assertNotEqual(aborted.label, errored.label)

    def test_1_228_failures_render_a_message_and_never_raise(self):
        """
        Scenario 1.228: no backend failure may take the dashboard down.
        Given: each backend failure the widget can meet
        When: the widget reads status
        Then: it returns a renderable error state rather than raising. Streamlit
              renders top-to-bottom, so an exception here erases every widget
              below it on the page
        """
        from lex.lex_app.streamlit._client import LexApiError
        from lex.lex_app.streamlit.calculation import read_status

        for status_code, expected in (
            (403, "not available"),
            (404, "not found"),
            (500, "unavailable"),
            (None, "unavailable"),
        ):
            with mock.patch(
                "lex.lex_app.streamlit.calculation._client.get_json",
                side_effect=LexApiError("boom", status=status_code),
            ):
                state = read_status("quarter", 1, token="t", include_log=False)

            self.assertIsNone(state.status, "A failed read has no calculation status.")
            self.assertIn(
                expected, state.message.lower(),
                f"HTTP {status_code} should explain itself to the user.",
            )

    def test_1_229_log_is_not_requested_when_disabled(self):
        """
        Scenario 1.229: show_log=False costs nothing.
        Given: a widget with the log tail disabled
        When: it reads status
        Then: include_log is not sent, so the backend skips the CalculationLog
              query entirely on every poll
        """
        from lex.lex_app.streamlit.calculation import read_status

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "SUCCESS", "error": None},
        ) as get_json:
            read_status("quarter", 1, token="t", include_log=False)

        _path, _token = get_json.call_args[0][:2]
        params = get_json.call_args[1].get("params") or {}
        self.assertNotIn("include_log", params)

    def test_1_230_colours_come_from_the_design_system(self):
        """
        Scenario 1.230: the widget cannot drift from the LEX design system.
        Given: the widget module source
        When: it is scanned for colour literals
        Then: none are found — every colour resolves from design_system, whose
              freshness CI already gates. LEX success is teal, not green, and a
              hardcoded value would silently diverge on the next token refresh
        """
        import inspect
        import re

        from lex.lex_app.streamlit import calculation

        source = inspect.getsource(calculation)
        self.assertEqual(
            re.findall(r"#[0-9a-fA-F]{6}\b", source), [],
            "Use lex.lex_app.design_system tokens rather than colour literals.",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v`

Expected: FAIL — `ModuleNotFoundError: lex.lex_app.streamlit.calculation`.

- [ ] **Step 3: Write the widget**

Create `lex/lex_app/streamlit/calculation.py`:

```python
"""``lex_calculation()`` — trigger one calculation and watch it, natively.

Replaces embedding a whole React table just to run one record. Talks only to
:mod:`lex.lex_app.streamlit._client`; it never imports Django models, so
permissions and audit behave exactly as they do for the React UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from lex.lex_app.design_system import BORDER, ERROR, INK, MUTED, SUCCESS, SURFACE, WARNING
from lex.lex_app.streamlit import _client

#: Statuses that mean work is still running. Everything else stops the poll.
_ACTIVE_STATUSES = {"IN_PROGRESS"}


@dataclass(frozen=True)
class Presentation:
    """How one status is rendered."""

    label: str
    colour: str
    suggests_rerun: bool


@dataclass(frozen=True)
class StatusState:
    """What one status read produced. ``status`` is None when the read failed."""

    status: Optional[str]
    error: Optional[str] = None
    message: str = ""
    log: Optional[list] = None
    log_truncated: bool = False


_PRESENTATIONS = {
    "NOT_CALCULATED": Presentation("Not calculated", MUTED, False),
    "IN_PROGRESS": Presentation("Running", WARNING, False),
    "SUCCESS": Presentation("Success", SUCCESS, False),
    "ERROR": Presentation("Error", ERROR, False),
    # Since PR #675 an aborted row is interrupted work, not a failure: the row
    # was left IN_PROGRESS by a restart and swept. Inviting a re-run is the
    # correct affordance; showing it as an error is what confused incident 1410.
    "ABORTED": Presentation("Interrupted", WARNING, True),
    "CANCELLED": Presentation("Cancelled", MUTED, False),
}


def presentation_for(status: str) -> Presentation:
    """Rendering rules for ``status``, falling back to a neutral treatment."""
    return _PRESENTATIONS.get(status, Presentation(status or "Unknown", MUTED, False))


def poll_interval_for(status: Optional[str], requested: float) -> Optional[float]:
    """Seconds between polls, or None when there is nothing left to watch.

    Returning None is what stops ``st.fragment`` re-running. Without it an idle
    dashboard would poll the backend forever.
    """
    return requested if status in _ACTIVE_STATUSES else None


def _status_path(model: str, pk: int) -> str:
    return f"/api/model_entries/{model}/{pk}/calculation-status"


def read_status(model: str, pk: int, token: str, include_log: bool) -> StatusState:
    """Read calculation state, converting every failure into a renderable state.

    Never raises. Streamlit renders top-to-bottom, so an exception escaping here
    would erase every widget below this one on the page.
    """
    params = {"include_log": "true"} if include_log else None
    try:
        payload = _client.get_json(_status_path(model, pk), token, params=params)
    except _client.LexApiError as exc:
        return StatusState(status=None, message=_failure_message(exc.status))

    return StatusState(
        status=payload.get("status"),
        error=payload.get("error"),
        log=payload.get("log"),
        log_truncated=bool(payload.get("log_truncated")),
    )


def _failure_message(status_code: Optional[int]) -> str:
    if status_code == 403:
        # Deliberately vague: the endpoint returns 404 for unreadable records,
        # so 403 here means the *action* was refused, not the record hidden.
        return "Not available"
    if status_code == 404:
        return "Record not found"
    return "Status unavailable"


def trigger_calculation(model: str, pk: int, token: str) -> Optional[str]:
    """Start the calculation. Returns an error message, or None on success.

    Uses the same ``calculate=true`` trigger the React UI uses, so permissions,
    audit actor and history are identical.
    """
    path = f"/api/model_entries/{model}/default/one/{pk}"
    try:
        _client.patch_json(path, token, params={"calculate": "true"})
    except _client.LexApiError as exc:
        if exc.status == 403:
            return "You don't have permission to run this"
        return str(exc)
    return None
```

Read `lex/lex_app/design_system/lex_tokens.py` to confirm the exported names before importing them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v`

Expected: PASS — 8 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/streamlit/calculation.py \
        lex/test_project/tests/init/test_1ab_calculation_widget.py
git commit -m "feat(streamlit): calculation status model, presentation and poll lifecycle"
```

---

### Task 7: The rendered widget and the public API

**Files:**
- Modify: `lex/lex_app/streamlit/calculation.py`
- Modify: `lex/lex_app/streamlit/__init__.py`
- Test: `lex/test_project/tests/init/test_1ab_calculation_widget.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_1_231_public_api_exports_both_widgets(self):
        """
        Scenario 1.231: authors import from the package, not its internals.
        Given: the streamlit package
        When: an author imports the widgets
        Then: both are on the package surface, and the pre-existing
              lex.lex_app.streamlit.embed path still works — dashboards written
              against it must not break
        """
        from lex.lex_app.streamlit import lex_calculation, lex_view
        from lex.lex_app.streamlit.embed import lex_view as legacy_lex_view

        self.assertTrue(callable(lex_calculation))
        self.assertIs(lex_view, legacy_lex_view)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v -k 1_231`

Expected: FAIL — `ImportError: cannot import name 'lex_calculation'`.

- [ ] **Step 3: Add the render function**

Append to `lex/lex_app/streamlit/calculation.py`:

```python
def _session_token() -> Optional[str]:
    """The signed-in user's access token, as held by the Streamlit host."""
    return st.session_state.get("access_token") or os.getenv("LEX_API_KEY")


def lex_calculation(
    model: str,
    pk: int,
    *,
    label: str = "Calculate",
    show_status: bool = True,
    show_last_run: bool = True,
    show_error: bool = True,
    show_log: bool = False,
    poll_interval: float = 2.0,
    key: Optional[str] = None,
) -> Optional[dict]:
    """Render a calculate button and live status for one record.

    Returns the latest status envelope, or None before the first read, so the
    dashboard can branch on it::

        status = lex_calculation("quarter", pk=42)
        if status and status["status"] == "SUCCESS":
            st.dataframe(load_results())
    """
    widget_key = key or f"lex_calc_{model}_{pk}"
    token = _session_token()

    if not token:
        st.warning("Session expired — reload the page to sign in again.")
        return None

    state_box = {}

    @st.fragment(run_every=st.session_state.get(f"{widget_key}__every"))
    def _render():
        state = read_status(model, pk, token, include_log=show_log)
        state_box["state"] = state

        st.session_state[f"{widget_key}__every"] = poll_interval_for(
            state.status, poll_interval
        )

        if state.status is None:
            st.caption(state.message)
            return

        look = presentation_for(state.status)
        running = state.status in _ACTIVE_STATUSES

        columns = st.columns([1, 2])
        with columns[0]:
            if st.button(label, key=f"{widget_key}__btn", disabled=running):
                failure = trigger_calculation(model, pk, token)
                if failure:
                    st.error(failure)
                else:
                    st.session_state[f"{widget_key}__every"] = poll_interval
                    st.rerun(scope="fragment")
        with columns[1]:
            if show_status:
                st.markdown(
                    f"<span style='color:{look.colour};font-weight:600'>{look.label}</span>",
                    unsafe_allow_html=True,
                )

        if look.suggests_rerun:
            st.caption("This run was interrupted — run it again.")
        if show_error and state.error:
            st.error(state.error)
        if show_log and state.log:
            st.code("\n".join(state.log), language=None)
            if state.log_truncated:
                st.caption("Showing the most recent lines only.")

    _render()
    state = state_box.get("state")
    if state is None or state.status is None:
        return None
    return {"status": state.status, "error": state.error}
```

`show_last_run` renders `started_at` / `finished_at` / `duration_seconds` from the envelope (Task 4b). When the record has never run they are `null` and the widget shows "Never run".

- [ ] **Step 4: Make the package the public surface**

Replace `lex/lex_app/streamlit/__init__.py` with:

```python
"""Public surface for the built-in Streamlit widgets."""

from lex.lex_app.streamlit.calculation import lex_calculation
from lex.lex_app.streamlit.embed import Flow, lex_view

__all__ = ["Flow", "lex_calculation", "lex_view"]
```

- [ ] **Step 5: Run the batch and the whole cluster**

Run: `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/ -v`

Expected: batch 1ab 9 passed; the rest of cluster 1 unchanged. Two pre-existing failures in `test_1r_lex_view_embed_helper.py` come from an untracked local file and are not caused by this work.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/streamlit/calculation.py \
        lex/lex_app/streamlit/__init__.py \
        lex/test_project/tests/init/test_1ab_calculation_widget.py
git commit -m "feat(streamlit): lex_calculation widget and package public surface"
```

---

### Task 8: Sync the test plan

**Files:**
- Modify: `lex/test_project/test-plan/clusters/01-init/{allocation.yaml,batches.md,cluster.md}`
- Modify: `lex/test_project/test-plan/clusters/10-api_layer/{allocation.yaml,batches.md,cluster.md}`
- Create: `lex/test_project/test-plan/progress/sessions/2026-08-04-streamlit-calculation-widget.md`
- Modify: `lex/test_project/test-plan/progress/dashboard.md`

- [ ] **Step 1: Add the cluster 1 allocation entry**

In `lex/test_project/test-plan/clusters/01-init/allocation.yaml`, set `max_scenario: 231` and append:

```yaml
  ab:
    title: Streamlit calculation widget (client, state, poll lifecycle)
    scenarios: 1.223-1.231
    status: complete
    tests:
      pass: 9
      skip: 0
      xfail: 0
    note: >-
      lex_calculation triggers via PATCH ?calculate=true over HTTP as the user and never touches
      the ORM, so permissions, audit actor and the _defer_calculate_hook path stay identical to
      the React UI. Two regressions pinned: polling that never stops, and any failure path that
      raises out of the widget. ABORTED renders as interrupted work with a re-run nudge, not as
      an error
```

- [ ] **Step 2: Add the cluster 10 allocation entry**

In `lex/test_project/test-plan/clusters/10-api_layer/allocation.yaml`, set `max_scenario: 79` and append:

```yaml
  o:
    title: Calculation status endpoint (Streamlit widget poll target)
    scenarios: 10.72-10.79
    status: complete
    tests:
      pass: 8
      skip: 0
      xfail: 0
    note: >-
      read-only state for one calculation record, cheap enough to poll every two seconds. An
      unreadable record is indistinguishable from a missing one, so the endpoint cannot confirm
      a record's existence or state to a caller who may not read it. Log tail bounded and absent
      unless requested
```

- [ ] **Step 3: Append the batch blocks**

Add a batch block to each cluster's `batches.md` matching the shape of the most recent block in that file (scenario range, type, files covered, test file, test classes, fixtures, status), and a scenario-table entry to each `cluster.md`.

- [ ] **Step 4: Write the session fragment**

Create `lex/test_project/test-plan/progress/sessions/2026-08-04-streamlit-calculation-widget.md`:

```markdown
---
date: 2026-08-04
clusters: [1, 10]
tests_added: 17
suite_tally: "1ab: 9 pass / 0 fail; 10o: 8 pass / 0 fail"
---

# Streamlit calculation widget

Two batches, because the surfaces are in two domains: the widget in
[cluster 1](../../clusters/01-init/batches.md) (which owns the Streamlit
helpers alongside `lex_view`) and its status endpoint in
[cluster 10](../../clusters/10-api_layer/batches.md).

Design: `docs/superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md`.

The load-bearing constraint is that the widget never imports Django. It triggers
through the same `PATCH ?calculate=true` the React UI uses, so there is exactly
one way to start a calculation — a second, divergent path is what produced the
`edited_at` bug in PR #675.
```

- [ ] **Step 5: Regenerate the dashboard and validate**

```bash
python .github/scripts/test_plan_aggregates.py build
python .github/scripts/test_plan_aggregates.py validate
```

Expected: `build` writes `progress/dashboard.md`; `validate` reports nothing for clusters 1 or 10.

- [ ] **Step 6: Commit**

```bash
git add lex/test_project/test-plan
git commit -m "docs(test-plan): sync batches 1ab and 10o"
```

---

## Verification

- [ ] `PROJECT_ROOT=lex/test_project python -m lex pytest lex/test_project/tests/init/ lex/test_project/tests/api_layer/ -q` — batches 1ab and 10o green, no new failures elsewhere
- [ ] `python .github/scripts/test_plan_aggregates.py validate` — clean for clusters 1 and 10
- [ ] `git diff --name-only origin/lex-app-v2...HEAD | wc -l` — the changed-file count matches the File Structure table. **Stage explicit paths; never `git add -A <dir>`** — this repo carries a large untracked working tree and `-A` has twice swept unrelated files into a PR
