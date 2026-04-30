"""
Lex test-group support for `lex pytest`.

This module is the single home for:
  * loading and validating the effective Lex test config from
    ``lex_test_config.yaml`` or a workflow-supplied payload
  * the in-process pytest plugin that registers markers, validates that
    every used marker maps to a configured group, and accumulates per-group
    pass/fail/skip/error counts
  * the PDF report writer used by ``lex pytest --report`` and by per-recipient
    email deliveries
  * local email delivery planning/rendering so ``lex pytest`` can fan out one
    report email per resolved recipient set

It is intentionally self-contained so the ``lex pytest`` CLI handler stays
small.  A future iteration will replace the basic ReportLab layout with an
HTML template + email delivery; the data shape exposed by ``GroupResult``
is the contract that future renderer will consume.
"""

from __future__ import annotations

import datetime as _dt
import csv
import json
import os
from dataclasses import dataclass, field
from email.utils import formataddr
from pathlib import Path
from typing import Any, Mapping, TypedDict

import yaml


CONFIG_FILENAME = "lex_test_config.yaml"
DEFAULT_REPORT_OUTPUT_DIR = "reports"
EFFECTIVE_CONFIG_ENV_VAR = "LEX_TEST_CONFIG_PAYLOAD"
TEST_EMAIL_BACKEND_ENV_VAR = "LEX_TEST_EMAIL_BACKEND"
TEST_EMAIL_FILE_PATH_ENV_VAR = "LEX_TEST_EMAIL_FILE_PATH"


class ReceiverPayload(TypedDict, total=False):
    name: str
    email: str


class GroupPayload(TypedDict):
    name: str
    description: str
    receivers: list[ReceiverPayload]


class ReportPayload(TypedDict, total=False):
    output_dir: str


class EmailPayload(TypedDict, total=False):
    enabled: bool
    from_email: str
    from_name: str
    reply_to: str
    subject_prefix: str


class LexTestConfigPayload(TypedDict):
    """Canonical cross-repo payload shared by Lex, backend and workflow editor.

    This is the structured form-state/runtime contract for Lex pytest groups. It
    always represents the full effective config shape, never a partial patch.
    """

    tests_entrypoint: str
    receivers: list[ReceiverPayload]
    report: ReportPayload
    groups: list[GroupPayload]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class LexTestConfigError(RuntimeError):
    """Raised for any user-facing problem with lex_test_config.yaml."""


@dataclass(frozen=True)
class GroupConfig:
    name: str
    description: str
    receivers: list[dict[str, Any]]

    def to_payload(self) -> GroupPayload:
        return {
            "name": self.name,
            "description": self.description,
            "receivers": [dict(receiver) for receiver in self.receivers],
        }


@dataclass(frozen=True)
class LexTestConfig:
    project_root: Path
    source: str
    tests_entrypoint: str
    report_output_dir: str
    global_receivers: list[dict[str, Any]]
    email: dict[str, Any]
    groups: list[GroupConfig]

    @property
    def group_names(self) -> set[str]:
        return {g.name for g in self.groups}

    def receivers_for(self, group_name: str) -> list[dict[str, Any]]:
        for g in self.groups:
            if g.name == group_name:
                return g.receivers if g.receivers else list(self.global_receivers)
        return list(self.global_receivers)

    def to_payload(self) -> LexTestConfigPayload:
        return {
            "tests_entrypoint": self.tests_entrypoint,
            "receivers": [dict(receiver) for receiver in self.global_receivers],
            "report": {"output_dir": self.report_output_dir},
            "groups": [group.to_payload() for group in self.groups],
        }


def default_config_payload(*, tests_entrypoint: str = "") -> LexTestConfigPayload:
    """Return the canonical full payload shape for workflow-managed config.

    The workflow editor stores the entire effective Lex config as a structured
    object. Even when a repo has no ``lex_test_config.yaml``, IC can still seed
    a new config from this scaffold and require the user to fill mandatory
    fields such as ``tests_entrypoint`` before saving/running.
    """

    return {
        "tests_entrypoint": tests_entrypoint,
        "receivers": [],
        "report": {"output_dir": DEFAULT_REPORT_OUTPUT_DIR},
        "groups": [],
    }


def hydrate_workflow_config_payload(
    *,
    repo_defaults: LexTestConfig | Mapping[str, Any] | None = None,
    saved_workflow_config: Mapping[str, Any] | None = None,
) -> LexTestConfigPayload:
    """Return the canonical workflow payload shared across repos.

    Hydration rules for the structured workflow editor/runtime contract:

    1. If a saved workflow config exists, it is already the full effective
       config and remains authoritative.
    2. Otherwise, if ``lex_test_config.yaml`` exists, its contents seed the form.
    3. Otherwise, IC still gets a fully shaped scaffold so repos without the
       file remain supported.
    """

    if saved_workflow_config is not None:
        return _normalize_payload(saved_workflow_config)
    if repo_defaults is None:
        return default_config_payload()
    if isinstance(repo_defaults, LexTestConfig):
        return repo_defaults.to_payload()
    return _normalize_payload(repo_defaults)


def load_config(project_root: Path) -> LexTestConfig:
    """Load ``lex_test_config.yaml`` from *project_root*. Hard error on issues."""
    path = project_root / CONFIG_FILENAME
    if not path.exists():
        raise LexTestConfigError(
            f"{CONFIG_FILENAME} not found at {path}. "
            f"Copy {CONFIG_FILENAME}.example to get started."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LexTestConfigError(f"Failed to parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise LexTestConfigError(f"{path} must contain a YAML mapping at the top level.")

    return _load_config_mapping(project_root=project_root, raw=raw, source=path)


def resolve_config(
    project_root: Path,
    *,
    external_payload: Mapping[str, Any] | None = None,
) -> LexTestConfig:
    """Resolve the effective Lex test config for runtime use.

    Workflow-managed runs may provide the already-hydrated full config payload
    via ``external_payload`` or ``$LEX_TEST_CONFIG_PAYLOAD``. When that payload
    is available it is authoritative, allowing runs to succeed even when the
    repo has no ``lex_test_config.yaml``. Otherwise we fall back to the repo
    file.
    """
    payload = external_payload if external_payload is not None else _load_effective_payload_from_env()
    if payload is not None:
        return _load_config_mapping(project_root=project_root, raw=payload, source=f"${EFFECTIVE_CONFIG_ENV_VAR}")
    return load_config(project_root)


def _load_effective_payload_from_env() -> Mapping[str, Any] | None:
    raw_payload = os.environ.get(EFFECTIVE_CONFIG_ENV_VAR)
    if raw_payload is None or not raw_payload.strip():
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise LexTestConfigError(
            f"${EFFECTIVE_CONFIG_ENV_VAR} must contain valid JSON: {exc.msg}."
        ) from exc
    if not isinstance(payload, Mapping):
        raise LexTestConfigError(
            f"${EFFECTIVE_CONFIG_ENV_VAR} must contain a JSON object."
        )
    return payload


def _load_config_mapping(
    *,
    project_root: Path,
    raw: Mapping[str, Any],
    source: Path | str,
) -> LexTestConfig:
    tests_entrypoint = raw.get("tests_entrypoint")
    if not isinstance(tests_entrypoint, str) or not tests_entrypoint.strip():
        raise LexTestConfigError(f"{source}: 'tests_entrypoint' is required and must be a string.")
    tests_entrypoint = tests_entrypoint.strip()
    tests_path = project_root / tests_entrypoint
    if not tests_path.exists():
        raise LexTestConfigError(
            f"{source}: 'tests_entrypoint' must exist relative to the project root "
            f"(missing {tests_path})."
        )
    if not tests_path.is_dir():
        raise LexTestConfigError(
            f"{source}: 'tests_entrypoint' must point to a directory (got {tests_path})."
        )

    report_section = raw.get("report") or {}
    if not isinstance(report_section, dict):
        raise LexTestConfigError(f"{source}: 'report' must be a mapping.")
    report_output_dir = report_section.get("output_dir", DEFAULT_REPORT_OUTPUT_DIR)
    if not isinstance(report_output_dir, str) or not report_output_dir.strip():
        raise LexTestConfigError(f"{source}: 'report.output_dir' must be a non-empty string.")

    global_receivers = _normalize_receivers(raw.get("receivers"), source, "receivers")
    email = _normalize_email_settings(raw.get("email"), source)

    raw_groups = raw.get("groups") or []
    if not isinstance(raw_groups, list) or not raw_groups:
        raise LexTestConfigError(f"{source}: 'groups' must be a non-empty list.")

    groups: list[GroupConfig] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_groups):
        if not isinstance(item, dict):
            raise LexTestConfigError(f"{source}: groups[{idx}] must be a mapping.")
        name = item.get("name")
        if not isinstance(name, str) or not name.isidentifier():
            raise LexTestConfigError(
                f"{source}: groups[{idx}].name must be a valid Python/pytest marker "
                f"identifier (got {name!r})."
            )
        if name in seen:
            raise LexTestConfigError(f"{source}: duplicate group name {name!r}.")
        seen.add(name)
        description = item.get("description") or ""
        if not isinstance(description, str):
            raise LexTestConfigError(f"{source}: groups[{idx}].description must be a string.")
        receivers = _normalize_receivers(item.get("receivers"), source, f"groups[{idx}].receivers")
        groups.append(GroupConfig(name=name, description=description, receivers=receivers))

    return LexTestConfig(
        project_root=project_root,
        source=str(source),
        tests_entrypoint=tests_entrypoint,
        report_output_dir=report_output_dir,
        global_receivers=global_receivers,
        email=email,
        groups=groups,
    )


def _normalize_payload(raw: Mapping[str, Any] | None) -> LexTestConfigPayload:
    payload = default_config_payload()
    if not raw:
        return payload

    tests_entrypoint = raw.get("tests_entrypoint")
    if isinstance(tests_entrypoint, str):
        payload["tests_entrypoint"] = tests_entrypoint.strip()
    elif tests_entrypoint is not None:
        payload["tests_entrypoint"] = tests_entrypoint

    report = raw.get("report")
    if isinstance(report, Mapping):
        output_dir = report.get("output_dir")
        if isinstance(output_dir, str) and output_dir.strip():
            payload["report"]["output_dir"] = output_dir.strip()

    payload["receivers"] = _normalize_payload_receivers(raw.get("receivers"))

    raw_groups = raw.get("groups") or []
    if isinstance(raw_groups, list):
        payload["groups"] = [
            {
                "name": item.get("name", "") if isinstance(item, Mapping) else "",
                "description": item.get("description", "") if isinstance(item, Mapping) else "",
                "receivers": _normalize_payload_receivers(
                    item.get("receivers") if isinstance(item, Mapping) else None
                ),
            }
            for item in raw_groups
        ]

    return payload


def _normalize_payload_receivers(value: Any) -> list[ReceiverPayload]:
    if not isinstance(value, list):
        return []

    out: list[ReceiverPayload] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        receiver: ReceiverPayload = {}
        name = item.get("name")
        email = item.get("email")
        if isinstance(name, str):
            receiver["name"] = name.strip()
        if isinstance(email, str):
            receiver["email"] = email.strip()
        for key, extra_value in item.items():
            if key not in {"name", "email"}:
                receiver[key] = extra_value
        out.append(receiver)
    return out


def _normalize_receivers(value: Any, path: Path | str, location: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LexTestConfigError(f"{path}: '{location}' must be a list.")
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise LexTestConfigError(f"{path}: {location}[{idx}] must be a mapping.")
        name = item.get("name")
        email = item.get("email")
        if not isinstance(name, str) or not name.strip():
            raise LexTestConfigError(
                f"{path}: {location}[{idx}].name must be a non-empty string."
            )
        if not isinstance(email, str) or not email.strip():
            raise LexTestConfigError(
                f"{path}: {location}[{idx}].email must be a non-empty string."
            )
        normalized = dict(item)
        normalized["name"] = name.strip()
        normalized["email"] = email.strip()
        out.append(normalized)
    return out


def _normalize_email_settings(value: Any, path: Path | str) -> dict[str, Any]:
    if value is None:
        return {
            "enabled": False,
            "from_email": "",
            "from_name": "",
            "reply_to": "",
            "subject_prefix": "",
        }
    if not isinstance(value, Mapping):
        raise LexTestConfigError(f"{path}: 'email' must be a mapping.")

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise LexTestConfigError(f"{path}: 'email.enabled' must be a boolean.")

    normalized = {
        "enabled": str(enabled).lower() == "true" if isinstance(enabled, str) else enabled,
        "from_email": str(value.get("from_email", "")).strip(),
        "from_name": str(value.get("from_name", "")).strip(),
        "reply_to": str(value.get("reply_to", "")).strip(),
        "subject_prefix": str(value.get("subject_prefix", "")).strip(),
    }
    if normalized["enabled"] and not normalized["from_email"]:
        raise LexTestConfigError(f"{path}: 'email.from_email' is required when email.enabled is true.")
    return normalized


# ---------------------------------------------------------------------------
# Argument parsing — strip Lex-only flags before forwarding to pytest
# ---------------------------------------------------------------------------


@dataclass
class ParsedPytestArgs:
    forwarded: list[str]
    report: bool


def parse_lex_pytest_args(argv: list[str]) -> ParsedPytestArgs:
    """Pop Lex-only flags from *argv*; everything else flows to pytest verbatim.

    Currently only ``--report`` is intercepted.  Keep the implementation tiny
    and explicit so future custom flags slot in obviously.
    """
    forwarded: list[str] = []
    report = False
    for arg in argv:
        if arg == "--report":
            report = True
            continue
        forwarded.append(arg)
    return ParsedPytestArgs(forwarded=forwarded, report=report)


# ---------------------------------------------------------------------------
# Pytest plugin
# ---------------------------------------------------------------------------


@dataclass
class GroupResult:
    name: str
    description: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    test_count: int = 0  # distinct tests carrying this marker

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors

    @property
    def status(self) -> str:
        if self.total == 0:
            return "NO TESTS"
        if self.failed or self.errors:
            return "FAILED"
        if self.passed == 0 and self.skipped:
            return "SKIPPED"
        return "PASSED"


@dataclass
class RecipientDelivery:
    """One simulated outbound email containing all group summaries for a recipient."""

    name: str
    email: str
    group_results: list[GroupResult] = field(default_factory=list)

    @property
    def group_names(self) -> list[str]:
        return [group.name for group in self.group_results]

    @property
    def groups_csv(self) -> str:
        return ",".join(self.group_names)


class LexGroupsPlugin:
    """In-process pytest plugin: marker registration, validation, aggregation."""

    def __init__(self, config: LexTestConfig, strict: bool = True) -> None:
        self._config = config
        self._strict = strict
        self.results: dict[str, GroupResult] = {
            g.name: GroupResult(name=g.name, description=g.description) for g in config.groups
        }
        # Track which test nodeids we've already counted toward test_count
        # per group, since pytest_runtest_logreport fires for setup/call/teardown.
        self._counted: dict[str, set[str]] = {g.name: set() for g in config.groups}
        # Tracks the worst phase outcome we've recorded for a given (group, nodeid).
        self._final_outcome: dict[tuple[str, str], str] = {}
        # nodeid → set of configured group names declared via @pytest.mark.<group>.
        # Populated in pytest_collection_modifyitems; this is the ONLY source we
        # consult during logreport, so directory/file/class names never bleed
        # into group attribution (e.g. a folder named `creation2` would otherwise
        # show up as a keyword for tests inside it).
        self._test_groups: dict[str, set[str]] = {}

    # -- pytest hooks ------------------------------------------------------

    def pytest_configure(self, config) -> None:  # noqa: D401 — pytest hook
        for g in self._config.groups:
            config.addinivalue_line("markers", f"{g.name}: {g.description}")

    def pytest_collection_modifyitems(self, config, items) -> None:  # noqa: D401
        configured = self._config.group_names
        unknown: dict[str, list[str]] = {}
        for item in items:
            marker_names = {m.name for m in item.iter_markers()}
            # Record only configured group markers; the rest (parametrize,
            # skip, asyncio, ...) are filtered out by `& configured`.
            self._test_groups[item.nodeid] = marker_names & configured
            for name in marker_names:
                if name in _BUILTIN_MARKERS or name in configured:
                    continue
                unknown.setdefault(name, []).append(item.nodeid)

        if unknown and self._strict:
            details = "\n".join(
                f"  - {name}: used by {len(nodes)} test(s) (e.g. {nodes[0]})"
                for name, nodes in sorted(unknown.items())
            )
            message = (
                f"\nThe following pytest markers are not declared as groups in "
                f"{CONFIG_FILENAME}:\n{details}\n"
                "Add them under `groups:` or remove the @pytest.mark.<name> tag.\n"
            )
            # pytest.exit gives a clean shutdown without a traceback dump,
            # while still surfacing a non-zero exit code to the wrapper.
            import pytest as _pytest
            _pytest.exit(message, returncode=4)

    def pytest_runtest_logreport(self, report) -> None:  # noqa: D401
        # Group attribution comes exclusively from markers we recorded at
        # collection time — never from `report.keywords`, which also contains
        # parent directory/module/class names and would mis-attribute tests.
        groups_for_test = self._test_groups.get(report.nodeid)
        if not groups_for_test:
            return

        for group_name in groups_for_test:
            self._counted[group_name].add(report.nodeid)
            key = (group_name, report.nodeid)

            if report.when == "call":
                if report.passed:
                    self._record(group_name, key, "passed")
                elif report.failed:
                    self._record(group_name, key, "failed")
                elif report.skipped:
                    self._record(group_name, key, "skipped")
            else:  # setup / teardown
                if report.failed:
                    self._record(group_name, key, "errors")
                elif report.skipped and report.when == "setup":
                    self._record(group_name, key, "skipped")

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: D401
        for name, result in self.results.items():
            result.test_count = len(self._counted[name])

    # -- internal ----------------------------------------------------------

    def _record(self, group_name: str, key: tuple[str, str], outcome: str) -> None:
        # Promote to the most severe outcome seen for this (group, test).
        previous = self._final_outcome.get(key)
        if previous == outcome:
            return
        severity = {"passed": 0, "skipped": 1, "failed": 2, "errors": 3}
        if previous is not None and severity[previous] >= severity[outcome]:
            return
        if previous is not None:
            setattr(
                self.results[group_name],
                previous,
                getattr(self.results[group_name], previous) - 1,
            )
        setattr(
            self.results[group_name],
            outcome,
            getattr(self.results[group_name], outcome) + 1,
        )
        self._final_outcome[key] = outcome


# Markers that pytest (or common plugins) ship out of the box. Tests carrying
# only these don't trigger our "unknown marker" guard.
_BUILTIN_MARKERS: frozenset[str] = frozenset(
    {
        "parametrize",
        "skip",
        "skipif",
        "xfail",
        "usefixtures",
        "filterwarnings",
        "tryfirst",
        "trylast",
        "asyncio",
        "django_db",
    }
)


# ---------------------------------------------------------------------------
# Group listing (dry run)
# ---------------------------------------------------------------------------


@dataclass
class GroupListing:
    """Result of a `--collect-only` pass: which tests belong to which groups."""

    group_to_tests: dict[str, list[str]]
    untagged: list[str]
    exit_code: int


def collect_groups(config: LexTestConfig, extra_args: list[str] | None = None) -> GroupListing:
    """Run pytest in collect-only mode and return per-group test attribution.

    No tests are executed. Marker validation still runs (strict mode), so an
    unknown marker aborts with a clean message — matching the behaviour of
    ``lex pytest``.

    The listing reflects pytest's *final* selection, so ``-m`` / ``-k``
    expressions and positional path filters narrow the output as expected.
    """
    import pytest as _pytest

    plugin = LexGroupsPlugin(config, strict=True)

    # Tiny secondary plugin that captures session.items AFTER all
    # `pytest_collection_modifyitems` hooks have run (including the built-in
    # mark plugin that drops deselected items). Without `trylast`, our hook
    # would observe items before deselection.
    class _SelectedItemsRecorder:
        def __init__(self) -> None:
            self.selected_nodeids: set[str] = set()

        def pytest_collection_finish(self, session) -> None:  # noqa: D401
            self.selected_nodeids = {item.nodeid for item in session.items}

    recorder = _SelectedItemsRecorder()

    args = ["--collect-only", "-q", config.tests_entrypoint]
    if extra_args:
        args.extend(extra_args)

    previous_cwd = Path.cwd()
    try:
        os.chdir(config.project_root)
        exit_code = _pytest.main(args, plugins=[plugin, recorder])
    finally:
        os.chdir(previous_cwd)

    group_to_tests: dict[str, list[str]] = {g.name: [] for g in config.groups}
    untagged: list[str] = []
    selected = recorder.selected_nodeids
    for nodeid, groups in plugin._test_groups.items():
        if nodeid not in selected:
            continue
        if not groups:
            untagged.append(nodeid)
            continue
        for g in groups:
            if g in group_to_tests:
                group_to_tests[g].append(nodeid)

    for tests in group_to_tests.values():
        tests.sort()
    untagged.sort()
    return GroupListing(
        group_to_tests=group_to_tests,
        untagged=untagged,
        exit_code=int(exit_code),
    )


# ---------------------------------------------------------------------------
# Simulated email delivery planning
# ---------------------------------------------------------------------------


def plan_recipient_deliveries(
    *,
    config: LexTestConfig,
    group_results: Mapping[str, GroupResult],
) -> list[RecipientDelivery]:
    """Aggregate configured group summaries into one delivery per recipient.

    Group-level receivers override the global list; when a recipient appears in
    multiple groups they should receive one combined email containing all of
    those group summaries. Duplicate addresses within the same group are
    de-duplicated so a single group is never attached twice to one delivery.
    """

    deliveries_by_email: dict[str, RecipientDelivery] = {}
    for group in config.groups:
        result = group_results[group.name]
        seen_for_group: set[str] = set()
        for receiver in config.receivers_for(group.name):
            email = receiver["email"]
            if email in seen_for_group:
                continue
            seen_for_group.add(email)

            delivery = deliveries_by_email.get(email)
            if delivery is None:
                delivery = RecipientDelivery(name=receiver["name"], email=email)
                deliveries_by_email[email] = delivery

            if result.name not in {existing.name for existing in delivery.group_results}:
                delivery.group_results.append(result)

    return list(deliveries_by_email.values())


def write_simulated_email_deliveries(
    *,
    deliveries: list[RecipientDelivery],
    output_path: Path,
) -> Path:
    """Persist planned deliveries as a CSV file for local inspection/tests."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["recipient_name", "recipient_email", "groups"],
        )
        writer.writeheader()
        for delivery in deliveries:
            writer.writerow(
                {
                    "recipient_name": delivery.name,
                    "recipient_email": delivery.email,
                    "groups": delivery.groups_csv,
                }
            )
    return output_path


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------


def write_pdf_report(
    *,
    config: LexTestConfig,
    plugin: LexGroupsPlugin,
    pytest_exit_code: int,
    group_names: list[str] | None = None,
    filename_stem: str | None = None,
    title: str = "Lex test report",
) -> Path:
    """Write a PDF summary for all configured groups or a selected subset."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    output_dir = config.project_root / config.report_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = filename_stem or f"lex-test-report-{timestamp}"
    output_path = output_dir / f"{filename}.pdf"
    selected_names = set(group_names) if group_names is not None else None
    selected_groups = [
        group for group in config.groups
        if selected_names is None or group.name in selected_names
    ]

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
    )

    story: list[Any] = []
    story.append(Paragraph(title, styles["Title"]))
    story.append(
        Paragraph(
            f"Generated {_dt.datetime.now().isoformat(timespec='seconds')} — "
            f"pytest exit code: {pytest_exit_code}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 6 * mm))

    header = ["Group", "Status", "Passed", "Failed", "Skipped", "Errors", "Tests"]
    rows: list[list[str]] = [header]
    for g in selected_groups:
        r = plugin.results[g.name]
        rows.append(
            [
                g.name,
                r.status,
                str(r.passed),
                str(r.failed),
                str(r.skipped),
                str(r.errors),
                str(r.test_count),
            ]
        )

    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    for g in selected_groups:
        story.append(Paragraph(f"<b>{g.name}</b> — {g.description or '(no description)'}", styles["Normal"]))
        receivers = config.receivers_for(g.name)
        if receivers:
            joined = ", ".join(
                f"{r.get('name', '?')} &lt;{r.get('email', '?')}&gt;" for r in receivers
            )
            story.append(Paragraph(f"Receivers: {joined}", styles["BodyText"]))
        else:
            story.append(Paragraph("Receivers: (none configured)", styles["BodyText"]))
        story.append(Spacer(1, 3 * mm))

    doc.build(story)
    return output_path


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------


def send_report_emails(
    *,
    config: LexTestConfig,
    plugin: LexGroupsPlugin,
    pytest_exit_code: int,
) -> list[RecipientDelivery]:
    """Send one report email per resolved recipient delivery."""
    from django.core.mail import EmailMultiAlternatives, get_connection

    deliveries = plan_recipient_deliveries(config=config, group_results=plugin.results)
    if not deliveries or not config.email.get("enabled"):
        return []

    backend = os.getenv(TEST_EMAIL_BACKEND_ENV_VAR, "").strip() or None
    connection_kwargs: dict[str, Any] = {"fail_silently": False}
    if backend:
        connection_kwargs["backend"] = backend
        if backend == "django.core.mail.backends.filebased.EmailBackend":
            file_path = os.getenv(TEST_EMAIL_FILE_PATH_ENV_VAR, "").strip()
            if not file_path:
                file_path = str(config.project_root / config.report_output_dir / "email-outbox")
            Path(file_path).mkdir(parents=True, exist_ok=True)
            connection_kwargs["file_path"] = file_path

    connection = get_connection(**connection_kwargs)
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    messages: list[EmailMultiAlternatives] = []

    for delivery in deliveries:
        pdf_path = write_pdf_report(
            config=config,
            plugin=plugin,
            pytest_exit_code=pytest_exit_code,
            group_names=delivery.group_names,
            filename_stem=f"lex-test-report-{timestamp}-{_slug(delivery.email)}",
            title=f"Lex test report — {delivery.name or delivery.email}",
        )
        message = EmailMultiAlternatives(
            subject=_build_delivery_subject(config=config, delivery=delivery),
            body=_render_delivery_text_body(delivery=delivery, pytest_exit_code=pytest_exit_code),
            from_email=_formatted_sender(config),
            to=[delivery.email],
            reply_to=[config.email["reply_to"]] if config.email.get("reply_to") else None,
            connection=connection,
        )
        message.attach_alternative(
            _render_delivery_html_body(delivery=delivery, pytest_exit_code=pytest_exit_code),
            "text/html",
        )
        message.attach_file(str(pdf_path), mimetype="application/pdf")
        messages.append(message)

    sent = connection.send_messages(messages) or 0
    if sent != len(messages):
        raise RuntimeError(f"Expected to send {len(messages)} emails but backend reported {sent}.")
    return deliveries


def _formatted_sender(config: LexTestConfig) -> str:
    from_email = config.email["from_email"]
    from_name = config.email.get("from_name") or ""
    return formataddr((from_name, from_email)) if from_name else from_email


def _delivery_status(delivery: RecipientDelivery) -> str:
    if any(group.failed or group.errors for group in delivery.group_results):
        return "FAILED"
    if all(group.test_count == 0 for group in delivery.group_results):
        return "NO TESTS"
    if any(group.skipped for group in delivery.group_results):
        return "PASSED WITH SKIPS"
    return "PASSED"


def _build_delivery_subject(*, config: LexTestConfig, delivery: RecipientDelivery) -> str:
    prefix = config.email.get("subject_prefix") or "Lex test report"
    return f"[{_delivery_status(delivery)}] {prefix} — {delivery.groups_csv or delivery.email}"


def _render_delivery_text_body(
    *,
    delivery: RecipientDelivery,
    pytest_exit_code: int,
) -> str:
    lines = [
        f"Hello {delivery.name or delivery.email},",
        "",
        f"Lex pytest finished with exit code {pytest_exit_code}.",
        f"This delivery contains the groups: {delivery.groups_csv or '(none)'}",
        "",
    ]
    for group in delivery.group_results:
        lines.append(
            f"- {group.name}: {group.status} "
            f"(tests={group.test_count}, passed={group.passed}, failed={group.failed}, "
            f"skipped={group.skipped}, errors={group.errors})"
        )
    lines.extend(["", "The matching PDF report is attached."])
    return "\n".join(lines)


def _render_delivery_html_body(
    *,
    delivery: RecipientDelivery,
    pytest_exit_code: int,
) -> str:
    rows = "".join(
        (
            "<tr>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;'>{group.name}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;'>{group.status}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>{group.test_count}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>{group.passed}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>{group.failed}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>{group.skipped}</td>"
            f"<td style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>{group.errors}</td>"
            "</tr>"
        )
        for group in delivery.group_results
    )
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;color:#0f172a;'>"
        f"<p>Hello {delivery.name or delivery.email},</p>"
        f"<p>Lex pytest finished with exit code <strong>{pytest_exit_code}</strong>. "
        f"This delivery contains the groups: <strong>{delivery.groups_csv or '(none)'}</strong>.</p>"
        "<table style='border-collapse:collapse;'>"
        "<thead><tr>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:left;'>Group</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:left;'>Status</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>Tests</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>Passed</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>Failed</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>Skipped</th>"
        "<th style='padding:8px;border:1px solid #cbd5e1;text-align:right;'>Errors</th>"
        "</tr></thead><tbody>"
        f"{rows}"
        "</tbody></table>"
        "<p>The matching PDF report is attached.</p>"
        "</body></html>"
    )


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
