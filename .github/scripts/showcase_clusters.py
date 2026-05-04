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

from dataclasses import dataclass


@dataclass(frozen=True)
class Cluster:
    key: str
    label: str
    short_description: str
    what_it_proves: str
    what_it_means_if_broken: str
    why_it_matters: str
    # ``release_gate=True`` clusters are the curated subset that blocks
    # a PyPI publish. The CI workflows pass ``--only release-gate`` and
    # the runner expands that token to every cluster flagged here, so
    # the cluster list lives in exactly one place.
    release_gate: bool = False


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
        ),
        release_gate=True,
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
        release_gate=True,
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
        release_gate=True,
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
        release_gate=True,
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
        ),
        release_gate=True,
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
        )
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
        )
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
        ),
        release_gate=True,
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
        )
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
        )
    ),
    Cluster(
        key="queries",
        label="AG Grid filter & sort",
        short_description=(
            "The grid's filter and sort UI translates correctly into "
            "database queries."
        ),
        what_it_proves=(
            "Every filter operation the frontend grid can produce "
            "(text, number, date, multi-condition, legacy shape) is "
            "translated into a correct ORM query with correct sort."
        ),
        what_it_means_if_broken=(
            "Grids may show wrong rows, empty results, or the wrong "
            "sort order — customers lose trust in their own data."
        ),
        why_it_matters=(
            "Grids are where customers spend 90% of their time on the "
            "platform."
        ),
        release_gate=True,
    ),
)


# ── Release-gate helpers ───────────────────────────────────────────
RELEASE_GATE_TOKEN = "release-gate"


def release_gate_keys() -> tuple[str, ...]:
    """Return the keys of every cluster flagged ``release_gate=True``,
    in CLUSTERS declaration order. Used by ``run_showcase_suite.py``
    to expand the magic ``--only release-gate`` selector and by the
    workflow YAMLs to avoid duplicating this list.
    """
    return tuple(c.key for c in CLUSTERS if c.release_gate)


def cluster_by_key(key: str) -> Cluster | None:
    for c in CLUSTERS:
        if c.key == key:
            return c
    return None

