"""
Cluster showcase definitions — single source of truth.

Every cluster in the business-facing showcase has exactly one entry
here. The runner (``run_showcase_suite.py``) iterates over
``CLUSTERS`` to execute tests; the report builder
(``build_showcase_report.py``) iterates over the same list to render
rows — so the dashboard, the email table, and the PDF table are
guaranteed to stay in sync.

Each entry declares:

  * ``key`` — folder name under ``lex/test_project/tests/``.
  * ``label`` — short business-facing title.
  * ``short_description`` — one line under the label (HTML allowed).
  * ``what_it_proves`` — paragraph shown when the cluster passes.
  * ``what_it_means_if_broken`` — paragraph shown when it fails.
  * ``why_it_matters`` — glossary entry for the PDF.

Per-cluster coverage is computed automatically from
``coverage.py``'s per-test contexts (see ``.coveragerc`` →
``dynamic_context = test_function``) — a file counts toward a
cluster's denominator only if a test in that cluster executed one
of its lines. No manual ``cov_include`` list to maintain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Per-test descriptions are defined in their own module so this file
# stays a clean cluster-structure catalogue and the prose lives next to
# the rest of the descriptions. See ``showcase_test_descriptions.py``
# for the writing-style guide.
from showcase_test_descriptions import (  # noqa: E402
    CRUD_API_TESTS,
    EXPORTS_TESTS,
    PERMISSIONS_TESTS,
    QUERIES_TESTS,
    SERIALIZERS_TESTS,
    SIGNALS_WS_TESTS,
)


@dataclass(frozen=True)
class Cluster:
    key: str
    label: str
    short_description: str
    what_it_proves: str
    what_it_means_if_broken: str
    why_it_matters: str
    # Per-test human-readable descriptions, keyed by the unittest
    # method name (e.g. ``test_8_45_real_redis_round_trip``). When a
    # cluster fails, the report renders one row per failed test using
    # this mapping — so a stakeholder sees "what specifically broke"
    # and "why we care", not just "celery_async failed".
    #
    # Populating these is intentionally incremental: covering every
    # test in the suite would be a sizeable writing effort, so we
    # start with the clusters that fail loudest and matter most
    # (celery, validation). Tests without an entry fall back to a
    # generic description rendered from the method name.
    test_descriptions: dict[str, str] = field(default_factory=dict)


# ── Per-cluster per-test descriptions ───────────────────────────────
# Keep these short (one or two sentences). They appear in a
# table cell so anything longer than ~250 characters wraps poorly.
# The mapping key is the **method name only** (the part after the
# final ``.`` in the dotted path), because that's what's stable when
# tests get reorganised across modules.

_CELERY_ASYNC_TESTS: dict[str, str] = {
    # Cluster 8a/8b — eager mode + dispatch routing
    "test_8_1_lex_shared_task_runs_inline_when_celery_inactive":
        "When CELERY_ACTIVE is False the @lex_shared_task decorator must "
        "execute the task body in-process so a developer without a broker "
        "still sees results. If this fails the framework's sync fallback is "
        "broken.",
    "test_8_2_lex_shared_task_dispatches_to_celery_when_active":
        "When CELERY_ACTIVE is True the same decorator must enqueue work to "
        "the worker instead of running inline. If this fails background "
        "processing silently runs on the request thread.",
    "test_8_3_callback_task_marks_calculation_success":
        "After a calculation task completes its CallbackTask hook must flip "
        "is_calculated to SUCCESS. If broken, finished calculations stay "
        "stuck in IN_PROGRESS forever.",
    "test_8_4_callback_task_marks_calculation_error":
        "When a calculation task raises, the CallbackTask hook must flip the "
        "calculation to ERROR and capture the message. If broken, failures "
        "are silently swallowed and the UI keeps spinning.",
    # Cluster 8g — task body / context
    "test_8_25_tasks_context_collects_results":
        "WaitForTasks must collect every task it dispatched into "
        "tasks_context so the caller can join on them. If broken, callers "
        "lose the ability to wait for parallel work.",
    "test_8_26_unblock_tasks_context_releases":
        "FireAndForget (UnblockCelery) must clear tasks_context on exit so a "
        "follow-up WaitForTasks block doesn't accidentally inherit stale "
        "tasks. If broken, scopes leak between calculations.",
    # Cluster 8h — eager E2E
    "test_8_29_eager_calculation_completes":
        "End-to-end check that a CalculationModel dispatched in eager mode "
        "transitions IN_PROGRESS → SUCCESS using only the in-process cache "
        "backend. If this fails the customer-facing happy path is broken.",
    "test_8_30_eager_calculation_records_error":
        "End-to-end check that a CalculationModel raising mid-run lands in "
        "ERROR with the exception text captured. If broken, error capture is "
        "lost in eager mode.",
    # Cluster 8i — scope contracts
    "test_8_33_waitfortasks_blocks_until_done":
        "Inside a WaitForTasks block, exiting must not return until every "
        "dispatched task has finished. If broken, callers get a false "
        "'all done' signal while work is still running.",
    "test_8_34_fireandforget_does_not_block":
        "Inside a FireAndForget block, exiting returns immediately even with "
        "tasks still queued. If broken, the platform loses its non-blocking "
        "dispatch primitive.",
    # Cluster 8k — real Redis broker integration (the gate-critical ones)
    "test_8_45_real_redis_round_trip_dispatches_and_succeeds":
        "Producer enqueues a task to a real Redis broker, an in-process "
        "Celery worker consumes it, and the result lands in the Redis "
        "result backend. The single most important integration test: "
        "if this fails, no background work actually crosses the broker.",
    "test_8_46_real_redis_failure_is_captured":
        "When a task raises while running on the real broker+worker, the "
        "failure message must round-trip through the Redis result backend "
        "to the producer. If broken, customer errors disappear behind "
        "the queue.",
    "test_8_47_real_redis_skips_when_unreachable":
        "If the Redis service is not reachable AND opt-in flag is off, this "
        "test skips cleanly — but if the opt-in flag is on (CI release "
        "gate) it MUST fail loudly. Prevents shipping a green release "
        "that never actually exercised the broker.",
    "test_8_48_real_redis_concurrent_tasks_complete":
        "Multiple tasks dispatched to the real broker in parallel must all "
        "finish and report results. If broken, parallel calculations race "
        "or lose results under realistic load.",
}

_VALIDATION_HOOKS_TESTS: dict[str, str] = {
    "test_3_1_pre_save_rejects_invalid_record":
        "Records that fail the model's pre-save validator must be rejected "
        "before any database write. If broken, invalid data lands in the "
        "database and corrupts downstream calculations.",
    "test_3_2_pre_save_accepts_valid_record":
        "A record that satisfies every validator must be saved unchanged. "
        "If broken, validators reject legitimate data and customers can't "
        "save valid records.",
    "test_3_3_post_save_rolls_back_on_invariant_violation":
        "If a post-save hook raises (e.g. business invariant violated by "
        "the combination of fields), the transaction must roll back so no "
        "trace of the bad record remains. If broken, half-committed rows "
        "leak into the dataset.",
    "test_3_4_validation_error_message_surfaces_to_caller":
        "The exact validation error message must reach the API caller as a "
        "400 with the field name. If broken, the frontend shows a generic "
        "'something went wrong' and the user can't fix the input.",
    "test_3_5_validation_runs_on_update_not_just_create":
        "Updates must re-run validation, not just creates. If broken, a "
        "record that was valid at creation can be edited into an invalid "
        "state without anyone noticing.",
    "test_3_6_validation_skipped_when_explicitly_suppressed":
        "When code paths legitimately need to bypass validation (data "
        "migration, audit replay) the suppression context must work. If "
        "broken, those paths can't operate or they re-run validation and "
        "fail unexpectedly.",
}


CLUSTERS: tuple[Cluster, ...] = (
    Cluster(
        key="init",
        label="Project initialisation",
        short_description=(
            "Pressing <strong>Init</strong> prepares the database and "
            "registers the project with access management, in one step."
        ),
        what_it_proves=(
            "Day-one onboarding works end-to-end: the platform detects "
            "the customer's data model, generates migrations, applies "
            "them, and registers the project with Keycloak for access "
            "management."
        ),
        what_it_means_if_broken=(
            "New customers cannot reliably onboard. A project may be "
            "left half-configured — either missing database tables or "
            "unknown to access management."
        ),
        why_it_matters=(
            "Day-one onboarding is the single highest-risk moment in "
            "the customer journey."
        )
    ),
    Cluster(
        key="crud_api",
        label="Create / read / update / delete via REST API",
        short_description=(
            "Records sent to the public REST API are accepted, stored, "
            "and retrievable."
        ),
        what_it_proves=(
            "The public CRUD API works end-to-end: authorised callers "
            "can create, read, modify and delete records, with correct "
            "HTTP status codes and stable identifiers."
        ),
        what_it_means_if_broken=(
            "Any customer-facing flow that moves data through the API "
            "is broken — from forms to integrations to data loaders."
        ),
        why_it_matters=(
            "CRUD is the most fundamental operation of the platform — "
            "if it is broken, no other feature matters."
        ),
        test_descriptions=CRUD_API_TESTS,
    ),
    Cluster(
        key="validation_hooks",
        label="Data validation",
        short_description=(
            "Invalid data is rejected before it ever reaches the database."
        ),
        what_it_proves=(
            "Pre-save validation stops invalid records from being saved, "
            "and post-save validation rolls back transactions that "
            "break business invariants — so stored data always conforms "
            "to the customer's rules."
        ),
        what_it_means_if_broken=(
            "Invalid data can be persisted to the database, leaving "
            "the customer's dataset in an inconsistent state."
        ),
        why_it_matters=(
            "Data integrity is the foundation of every downstream "
            "calculation and report."
        ),
        test_descriptions=_VALIDATION_HOOKS_TESTS,
    ),
    Cluster(
        key="permissions",
        label="Access control",
        short_description=(
            "Users only see and modify the records they are permitted "
            "to see and modify."
        ),
        what_it_proves=(
            "Row-level and field-level permissions are enforced on "
            "every read and write — no data leaks, no unauthorised "
            "modifications."
        ),
        what_it_means_if_broken=(
            "Users may see data they shouldn't, or modify data they "
            "shouldn't. A data-leak or compliance incident becomes "
            "likely."
        ),
        why_it_matters=(
            "Enforcement of access rules is a hard compliance and "
            "security requirement."
        ),
        test_descriptions=PERMISSIONS_TESTS,
    ),
    Cluster(
        key="history",
        label="Audit trail & history",
        short_description=(
            "Every change to a record is captured as an immutable "
            "history row."
        ),
        what_it_proves=(
            "Every create, update and delete produces a history entry "
            "with correct timestamps and bitemporal chaining, so the "
            "full lineage of any record can be reconstructed on demand."
        ),
        what_it_means_if_broken=(
            "Change history may be incomplete, making it impossible to "
            "reconstruct how a record arrived at its current state."
        ),
        why_it_matters=(
            "A complete change history is required for compliance and "
            "for customer-facing 'who changed what, when' views."
        )
    ),
    Cluster(
        key="audit_logging",
        label="Compliance audit logging",
        short_description=(
            "Customer-visible actions are written to a tamper-evident "
            "audit log."
        ),
        what_it_proves=(
            "Every customer-visible action produces an audit entry "
            "with the actor, the target, and the outcome — including "
            "across long-running calculations."
        ),
        what_it_means_if_broken=(
            "Compliance records may be missing or incomplete, "
            "preventing regulatory reporting."
        ),
        why_it_matters=(
            "Regulated customers need a complete audit trail to pass "
            "external review."
        )
    ),
    Cluster(
        key="calculations",
        label="Calculation engine",
        short_description=(
            "Calculations run, report progress, and either finish "
            "cleanly or fail loudly — never silently."
        ),
        what_it_proves=(
            "Calculations transition through their state machine "
            "correctly: IN_PROGRESS → SUCCESS or FAILED, with errors "
            "captured and parent/child calculations coordinating."
        ),
        what_it_means_if_broken=(
            "Calculations may stick in IN_PROGRESS, swallow errors, or "
            "produce wrong results without flagging the failure."
        ),
        why_it_matters=(
            "Calculated outputs drive customer dashboards and "
            "reports — silent failures cannot be tolerated."
        )
    ),
    Cluster(
        key="celery_async",
        label="Background processing",
        short_description=(
            "Long-running work runs in the background without blocking "
            "the user interface."
        ),
        what_it_proves=(
            "Work dispatched to Celery runs reliably, with a sync "
            "fallback when Celery is unavailable, and without losing "
            "task results."
        ),
        what_it_means_if_broken=(
            "Background jobs may vanish or silently stall, leaving "
            "customers with partial results and no error signal."
        ),
        why_it_matters=(
            "Customers rely on background calculations for workloads "
            "too large to run inline."
        ),
        test_descriptions=_CELERY_ASYNC_TESTS,
    ),
    Cluster(
        key="signals_ws",
        label="Live updates to the UI",
        short_description=(
            "The frontend receives live status updates via WebSocket."
        ),
        what_it_proves=(
            "Status changes (calculation started, progress, finished, "
            "failed) are broadcast over WebSocket so open browser tabs "
            "reflect reality without manual refresh."
        ),
        what_it_means_if_broken=(
            "The UI may show stale 'in progress' spinners or miss a "
            "completion — users lose trust in live data."
        ),
        why_it_matters=(
            "Live status is a core part of the platform's perceived "
            "responsiveness."
        ),
        test_descriptions=SIGNALS_WS_TESTS,
    ),
    Cluster(
        key="api_layer",
        label="REST API contract",
        short_description=(
            "The REST API obeys its documented contract — endpoints, "
            "shapes, status codes."
        ),
        what_it_proves=(
            "Every endpoint returns the expected shape, honours "
            "schema introspection, and handles search, export and "
            "bulk operations as documented."
        ),
        what_it_means_if_broken=(
            "Integrations that rely on the documented API may silently "
            "break — UI navigation, schema-driven forms, and customer "
            "scripts."
        ),
        why_it_matters=(
            "Every customer-facing frontend and every external "
            "integration depends on a stable API contract."
        )
    ),
    Cluster(
        key="stress",
        label="Performance under realistic load",
        short_description=(
            "The platform stays fast and memory-safe on realistic "
            "customer-size datasets."
        ),
        what_it_proves=(
            "List reads, exports, filters and period calculations run "
            "within documented time and query budgets at 5 000 – 25 000 "
            "rows — and don't regress release over release."
        ),
        what_it_means_if_broken=(
            "Performance regresses silently on production-scale data, "
            "leading to slow pages, runaway memory, or timed-out "
            "exports for larger customers."
        ),
        why_it_matters=(
            "Performance budgets are a hard customer-SLA requirement."
        )  # stress tests cover everything; scoped cov. is meaningless
    ),
    Cluster(
        key="serializers",
        label="API payload shape",
        short_description=(
            "JSON payloads match the documented schema — types, keys, "
            "precision."
        ),
        what_it_proves=(
            "Decimal precision, datetime timezones, FK references, "
            "nullable fields and M2M relationships all round-trip "
            "correctly through the serialiser layer."
        ),
        what_it_means_if_broken=(
            "Frontend forms, grids and integrations may show wrong "
            "numbers, wrong times, or drop fields silently."
        ),
        why_it_matters=(
            "Serialiser bugs are some of the most dangerous — they "
            "corrupt data at the presentation layer without raising "
            "errors."
        ),
        test_descriptions=SERIALIZERS_TESTS,
    ),
    Cluster(
        key="exports",
        label="Export to spreadsheet",
        short_description=(
            "Excel / CSV exports return the correct rows, columns and "
            "values."
        ),
        what_it_proves=(
            "The Export endpoint returns all requested rows with the "
            "right columns, respecting field masking, grouping and "
            "FK display names."
        ),
        what_it_means_if_broken=(
            "'Export to Excel' may return missing or wrong rows — a "
            "data-quality incident with external stakeholders."
        ),
        why_it_matters=(
            "Exports are the primary way customers share data outside "
            "the platform."
        ),
        test_descriptions=EXPORTS_TESTS,
    ),
    Cluster(
        key="queries",
        label="AG Grid filter, sort, group & pivot",
        short_description=(
            "The AG Grid query layer — filter, sort, group, pivot — "
            "translates correctly into ORM queries on top of the REST "
            "CRUD endpoints."
        ),
        what_it_proves=(
            "Every filter shape the AG Grid frontend can produce — text, "
            "number, date, set, multi-condition, the legacy v1 condition "
            "model, plus row grouping, value aggregation and pivoting — "
            "is translated into a correct ORM query with correct sort "
            "and pagination on top of the CRUD endpoints."
        ),
        what_it_means_if_broken=(
            "Grids may show wrong rows, empty results, the wrong sort "
            "order, or grouped/pivoted views that don't sum correctly — "
            "customers lose trust in their own data."
        ),
        why_it_matters=(
            "Grids are where customers spend 90% of their time on the "
            "platform; the query layer underneath is what makes those "
            "grids actually useful for analysis, not just listing."
        ),
        test_descriptions=QUERIES_TESTS,
    ),
)


def cluster_by_key(key: str) -> Cluster | None:
    for c in CLUSTERS:
        if c.key == key:
            return c
    return None

