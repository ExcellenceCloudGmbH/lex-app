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
  * ``worst_case_outcome`` — concrete one-line consequence used in
    the failure detail strip; never hedged ("may", "could").
  * ``why_it_matters`` — glossary entry for the PDF.
  * ``section`` — one of "foundations", "engine", "surface". The
    report groups rows by section so readers see related capabilities
    together rather than alphabetically.
  * ``release_gate`` — clusters flagged True are the curated subset
    that blocks a PyPI publish.

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
    worst_case_outcome: str = ""
    section: str = "foundations"
    # ``release_gate=True`` clusters are the curated subset that blocks
    # a PyPI publish. The CI workflows pass ``--only release-gate`` and
    # the runner expands that token to every cluster flagged here, so
    # the cluster list lives in exactly one place.
    release_gate: bool = False


# ── Section labels — used by the report grouping ───────────────────
SECTION_LABELS: dict[str, str] = {
    "foundations": "Foundations",
    "engine":      "Calculation engine & background work",
    "surface":     "API surface, grid & exports",
}
SECTION_ORDER: tuple[str, ...] = ("foundations", "engine", "surface")


CLUSTERS: tuple[Cluster, ...] = (
    Cluster(
        key="init",
        label="Project initialisation",
        short_description=(
            "First-run setup prepares the database and registers the "
            "project with access management — in one step."
        ),
        what_it_proves=(
            "Day-one onboarding flows end-to-end: the platform reads "
            "the customer's data model, generates and applies the "
            "matching database migrations, and registers the project "
            "with access management."
        ),
        what_it_means_if_broken=(
            "New customers cannot reliably onboard. A project gets "
            "left half-configured — either missing database tables or "
            "unknown to access management."
        ),
        worst_case_outcome=(
            "A new customer is unable to log in on day one."
        ),
        why_it_matters=(
            "Day-one onboarding is the single highest-risk moment in "
            "the customer journey."
        ),
        section="foundations",
        release_gate=True,
    ),
    Cluster(
        key="crud_api",
        label="Data API (create, read, update, delete)",
        short_description=(
            "Records sent to the API are accepted, stored, and "
            "retrievable as expected."
        ),
        what_it_proves=(
            "Authorised callers can create, read, modify and delete "
            "records through the public API, with stable identifiers "
            "and consistent responses across every operation."
        ),
        what_it_means_if_broken=(
            "Any customer-facing flow that moves data through the API "
            "is broken — forms, integrations, and bulk loaders alike."
        ),
        worst_case_outcome=(
            "Saving a record from the UI silently does nothing."
        ),
        why_it_matters=(
            "Data movement is the most fundamental operation of the "
            "platform — if it is broken, no other feature matters."
        ),
        section="foundations",
        release_gate=True,
    ),
    Cluster(
        key="validation_hooks",
        label="Data validation",
        short_description=(
            "Invalid data is rejected before it ever reaches the database."
        ),
        what_it_proves=(
            "Pre-save validation stops invalid records from being "
            "saved, and post-save validation rolls back transactions "
            "that break business rules — so stored data always "
            "conforms to the customer's invariants."
        ),
        what_it_means_if_broken=(
            "Invalid data lands in the database, leaving the "
            "customer's dataset in an inconsistent state that has to "
            "be cleaned up by hand."
        ),
        worst_case_outcome=(
            "A bad record corrupts a downstream calculation."
        ),
        why_it_matters=(
            "Data integrity is the foundation of every downstream "
            "calculation and report."
        ),
        section="foundations",
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
            "Users see data they shouldn't, or modify data they "
            "shouldn't. A data-leak or compliance incident becomes "
            "likely."
        ),
        worst_case_outcome=(
            "One customer's data is visible to another customer."
        ),
        why_it_matters=(
            "Enforcement of access rules is a hard compliance and "
            "security requirement."
        ),
        section="foundations",
        release_gate=True,
    ),
    Cluster(
        key="history",
        label="Change history (per record)",
        short_description=(
            "Every change to a record is captured as an immutable "
            "history row."
        ),
        what_it_proves=(
            "Each create, update and delete writes a history entry "
            "with correct timestamps and lineage chaining, so the "
            "full life of any record can be reconstructed on demand."
        ),
        what_it_means_if_broken=(
            "Change history is incomplete, making it impossible to "
            "reconstruct how a record arrived at its current state."
        ),
        worst_case_outcome=(
            "A 'who changed what' question has no answer."
        ),
        why_it_matters=(
            "A complete change history is required for compliance and "
            "for customer-facing 'who changed what, when' views."
        ),
        section="foundations",
    ),
    Cluster(
        key="audit_logging",
        label="Compliance audit log (per action)",
        short_description=(
            "Customer-visible actions are written to a tamper-evident "
            "audit log."
        ),
        what_it_proves=(
            "Each customer-visible action produces an audit entry "
            "with the actor, the target, and the outcome — including "
            "across long-running calculations."
        ),
        what_it_means_if_broken=(
            "Compliance records are missing or incomplete, blocking "
            "regulatory reporting."
        ),
        worst_case_outcome=(
            "An external audit fails because evidence is missing."
        ),
        why_it_matters=(
            "Regulated customers need a complete audit trail to pass "
            "external review."
        ),
        section="foundations",
    ),
    Cluster(
        key="calculations",
        label="Calculation engine",
        short_description=(
            "Calculations run, report progress, and either finish "
            "cleanly or fail loudly — never silently."
        ),
        what_it_proves=(
            "Calculations advance through their states correctly: "
            "they start, finish or fail, surface their errors, and "
            "coordinate parent/child relationships when work is "
            "decomposed."
        ),
        what_it_means_if_broken=(
            "Calculations stick mid-run, swallow errors, or produce "
            "wrong results without flagging the failure."
        ),
        worst_case_outcome=(
            "A failed calculation looks successful on the dashboard."
        ),
        why_it_matters=(
            "Calculated outputs drive customer dashboards and "
            "reports — silent failures cannot be tolerated."
        ),
        section="engine",
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
            "Work dispatched to the background queue runs reliably, "
            "with a synchronous fallback when the queue is "
            "unavailable, and without losing task results."
        ),
        what_it_means_if_broken=(
            "Background jobs vanish or stall silently, leaving "
            "customers with partial results and no error signal."
        ),
        worst_case_outcome=(
            "An overnight batch never completes and nobody is paged."
        ),
        why_it_matters=(
            "Customers rely on background calculations for workloads "
            "too large to run inline."
        ),
        section="engine",
    ),
    Cluster(
        key="signals_ws",
        label="Live updates to the UI",
        short_description=(
            "The frontend receives live status updates over its "
            "real-time channel."
        ),
        what_it_proves=(
            "Status changes (calculation started, progress, finished, "
            "failed) are broadcast in real time, so open browser tabs "
            "reflect reality without a manual refresh."
        ),
        what_it_means_if_broken=(
            "The UI shows stale 'in progress' spinners or misses a "
            "completion — users lose trust in live data."
        ),
        worst_case_outcome=(
            "A finished job sits forever as 'still running' in the UI."
        ),
        why_it_matters=(
            "Live status is a core part of the platform's perceived "
            "responsiveness."
        ),
        section="engine",
    ),
    Cluster(
        key="api_layer",
        label="REST API contract",
        short_description=(
            "The API obeys its documented contract — endpoints, "
            "shapes, status codes."
        ),
        what_it_proves=(
            "Every endpoint returns the documented shape, honours "
            "schema introspection, and handles search, export and "
            "bulk operations as specified."
        ),
        what_it_means_if_broken=(
            "Integrations that rely on the documented API silently "
            "break — UI navigation, schema-driven forms, and customer "
            "scripts."
        ),
        worst_case_outcome=(
            "A customer's automation pipeline starts returning errors."
        ),
        why_it_matters=(
            "Every customer-facing frontend and every external "
            "integration depends on a stable API contract."
        ),
        section="surface",
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
            "List reads, exports, filters and period calculations "
            "stay inside their documented time and query budgets at "
            "5 000 – 25 000 rows — and don't regress release over "
            "release."
        ),
        what_it_means_if_broken=(
            "Performance regresses silently on production-scale data, "
            "leading to slow pages, runaway memory, or timed-out "
            "exports for larger customers."
        ),
        worst_case_outcome=(
            "A larger customer's export times out repeatedly."
        ),
        why_it_matters=(
            "Performance budgets are a hard customer-SLA requirement."
        ),
        section="surface",
    ),
    Cluster(
        key="serializers",
        label="API payload shape",
        short_description=(
            "JSON payloads match the documented schema — types, keys, "
            "precision."
        ),
        what_it_proves=(
            "Decimals, datetimes with timezones, foreign-key "
            "references, nullable fields and many-to-many "
            "relationships all round-trip correctly through the "
            "serialiser layer."
        ),
        what_it_means_if_broken=(
            "Frontend forms, grids and integrations show wrong "
            "numbers, wrong times, or drop fields without raising "
            "errors."
        ),
        worst_case_outcome=(
            "A monetary amount is displayed at the wrong precision."
        ),
        why_it_matters=(
            "Serialiser bugs are some of the most dangerous — they "
            "corrupt data at the presentation layer without raising "
            "errors."
        ),
        section="surface",
    ),
    Cluster(
        key="exports",
        label="Export to spreadsheet",
        short_description=(
            "Excel and CSV exports return the correct rows, columns "
            "and values."
        ),
        what_it_proves=(
            "The export endpoint returns every requested row with "
            "the right columns, respecting field masking, grouping "
            "and foreign-key display names."
        ),
        what_it_means_if_broken=(
            "'Export to Excel' returns missing or wrong rows — a "
            "data-quality incident with external stakeholders."
        ),
        worst_case_outcome=(
            "A board-pack export ships with rows missing."
        ),
        why_it_matters=(
            "Exports are the primary way customers share data outside "
            "the platform."
        ),
        section="surface",
    ),
    Cluster(
        key="queries",
        label="Data grid filter & sort",
        short_description=(
            "The grid's filter and sort controls translate correctly "
            "into database queries."
        ),
        what_it_proves=(
            "Every filter the grid UI can produce — text, number, "
            "date, multi-condition, legacy shape — translates into a "
            "correct database query with correct sort order."
        ),
        what_it_means_if_broken=(
            "Grids show the wrong rows, empty results, or the wrong "
            "sort order — customers lose trust in their own data."
        ),
        worst_case_outcome=(
            "A customer sees an empty grid that should have rows."
        ),
        why_it_matters=(
            "Grids are where customers spend 90% of their time on the "
            "platform."
        ),
        section="surface",
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


# ── Index — built once at import time so callers don't pay O(N) ───
_CLUSTERS_BY_KEY: dict[str, Cluster] = {c.key: c for c in CLUSTERS}


def cluster_by_key(key: str) -> Cluster | None:
    return _CLUSTERS_BY_KEY.get(key)


def clusters_by_section() -> dict[str, list[Cluster]]:
    """Return clusters grouped by section, in declaration order within
    each section, in SECTION_ORDER between sections. Sections that have
    no clusters are omitted."""
    grouped: dict[str, list[Cluster]] = {s: [] for s in SECTION_ORDER}
    for c in CLUSTERS:
        grouped.setdefault(c.section, []).append(c)
    return {s: grouped[s] for s in SECTION_ORDER if grouped.get(s)}
