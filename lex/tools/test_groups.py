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

import base64
import csv
import datetime as _dt
import json
import mimetypes
import os
import subprocess
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
    email: EmailPayload
    groups: list[GroupPayload]
    group_assignments: dict[str, str]


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
    group_assignments: dict[str, str]

    @property
    def group_names(self) -> set[str]:
        return {g.name for g in self.groups}

    @property
    def report_dir(self) -> Path:
        return self.project_root / self.report_output_dir

    @property
    def coverage_html_dir(self) -> Path:
        return self.report_dir / "coverage-html"

    def receivers_for(self, group_name: str) -> list[dict[str, Any]]:
        receivers = list(self.global_receivers)
        for g in self.groups:
            if g.name == group_name:
                receivers.extend(g.receivers)
                break
        return receivers

    def to_payload(self) -> LexTestConfigPayload:
        return {
            "tests_entrypoint": self.tests_entrypoint,
            "receivers": [dict(receiver) for receiver in self.global_receivers],
            "report": {"output_dir": self.report_output_dir},
            "email": dict(self.email),
            "groups": [group.to_payload() for group in self.groups],
            "group_assignments": dict(self.group_assignments),
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
        "email": {
            "from_email": "",
            "from_name": "",
            "reply_to": "",
            "subject_prefix": "",
        },
        "groups": [],
        "group_assignments": {},
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

    raw_group_assignments = raw.get("group_assignments") or {}
    if not isinstance(raw_group_assignments, Mapping):
        raise LexTestConfigError(f"{source}: 'group_assignments' must be a mapping.")
    group_assignments: dict[str, str] = {}
    for nodeid, group_name in raw_group_assignments.items():
        if not isinstance(nodeid, str) or not nodeid.strip():
            raise LexTestConfigError(
                f"{source}: group_assignments keys must be non-empty test nodeids."
            )
        if not isinstance(group_name, str) or not group_name.strip():
            raise LexTestConfigError(
                f"{source}: group_assignments[{nodeid!r}] must be a non-empty group name."
            )
        if group_name not in seen:
            raise LexTestConfigError(
                f"{source}: group_assignments[{nodeid!r}] references unknown group {group_name!r}."
            )
        group_assignments[nodeid] = group_name

    return LexTestConfig(
        project_root=project_root,
        source=str(source),
        tests_entrypoint=tests_entrypoint,
        report_output_dir=report_output_dir,
        global_receivers=global_receivers,
        email=email,
        groups=groups,
        group_assignments=group_assignments,
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

    email = raw.get("email")
    if isinstance(email, Mapping):
        payload["email"] = {
            "from_email": str(email.get("from_email", "")).strip(),
            "from_name": str(email.get("from_name", "")).strip(),
            "reply_to": str(email.get("reply_to", "")).strip(),
            "subject_prefix": str(email.get("subject_prefix", "")).strip(),
        }

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

    raw_group_assignments = raw.get("group_assignments") or {}
    if isinstance(raw_group_assignments, Mapping):
        payload["group_assignments"] = {
            str(nodeid): group_name.strip()
            for nodeid, group_name in raw_group_assignments.items()
            if isinstance(nodeid, str) and isinstance(group_name, str) and group_name.strip()
        }

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
            "from_email": "",
            "from_name": "",
            "reply_to": "",
            "subject_prefix": "",
        }
    if not isinstance(value, Mapping):
        raise LexTestConfigError(f"{path}: 'email' must be a mapping.")

    normalized = {
        "from_email": str(value.get("from_email", "")).strip(),
        "from_name": str(value.get("from_name", "")).strip(),
        "reply_to": str(value.get("reply_to", "")).strip(),
        "subject_prefix": str(value.get("subject_prefix", "")).strip(),
    }
    return normalized


# ---------------------------------------------------------------------------
# Argument parsing — strip Lex-only flags before forwarding to pytest
# ---------------------------------------------------------------------------


@dataclass
class ParsedPytestArgs:
    forwarded: list[str]
    report: bool
    report_and_email: bool
    send_emails: bool


def parse_lex_pytest_args(argv: list[str]) -> ParsedPytestArgs:
    """Pop Lex-only flags from *argv*; everything else flows to pytest verbatim.

    Currently only ``--report``, ``--report-and-email``, and ``--send-emails``
    are intercepted. Keep the implementation tiny and explicit so future custom
    flags slot in obviously.
    """
    forwarded: list[str] = []
    report = False
    report_and_email = False
    send_emails = False
    for arg in argv:
        if arg == "--report":
            report = True
            continue
        if arg == "--report-and-email":
            report_and_email = True
            continue
        if arg == "--send-emails":
            send_emails = True
            continue
        forwarded.append(arg)
    return ParsedPytestArgs(
        forwarded=forwarded,
        report=report,
        report_and_email=report_and_email,
        send_emails=send_emails,
    )


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
    tests: list["TestResult"] = field(default_factory=list)

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
class TestResult:
    nodeid: str
    name: str
    location: str = ""
    status: str = "NOT RUN"
    duration_seconds: float = 0.0
    details: str = ""


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
        self._group_assignments = {
            nodeid: group_name
            for nodeid, group_name in config.group_assignments.items()
            if group_name in config.group_names
        }
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
        self._test_details: dict[tuple[str, str], TestResult] = {}
        self.coverage_summary: dict[str, Any] | None = None
        self.run_duration: str = ""

    # -- pytest hooks ------------------------------------------------------

    def pytest_configure(self, config) -> None:  # noqa: D401 — pytest hook
        for g in self._config.groups:
            config.addinivalue_line("markers", f"{g.name}: {g.description}")

    def pytest_collection_modifyitems(self, config, items) -> None:  # noqa: D401
        configured = self._config.group_names
        unknown: dict[str, list[str]] = {}
        for item in items:
            marker_names = {m.name for m in item.iter_markers()}
            # A workflow/runtime assignment is authoritative for this selected
            # test. That lets IC regroup tests under arbitrary run-time group
            # names even when the source still carries older repo markers such
            # as `creation` / `creation2`.
            explicit_group = self._group_assignments.get(item.nodeid)
            self._test_groups[item.nodeid] = (
                {explicit_group} if explicit_group else marker_names & configured
            )
            location = item.location[0]
            if len(item.location) > 1:
                location = f"{location}:{int(item.location[1]) + 1}"
            test_name = getattr(item, "name", "") or item.nodeid.split("::")[-1]
            for group_name in self._test_groups[item.nodeid]:
                self._test_details[(group_name, item.nodeid)] = TestResult(
                    nodeid=item.nodeid,
                    name=test_name,
                    location=location,
                )
            for name in marker_names:
                if explicit_group:
                    continue
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
                    self._update_test_result(group_name, key, "passed", report)
                elif report.failed:
                    self._record(group_name, key, "failed")
                    self._update_test_result(group_name, key, "failed", report)
                elif report.skipped:
                    self._record(group_name, key, "skipped")
                    self._update_test_result(group_name, key, "skipped", report)
            else:  # setup / teardown
                if report.failed:
                    self._record(group_name, key, "errors")
                    self._update_test_result(group_name, key, "errors", report)
                elif report.skipped and report.when == "setup":
                    self._record(group_name, key, "skipped")
                    self._update_test_result(group_name, key, "skipped", report)

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: D401
        for name, result in self.results.items():
            result.test_count = len(self._counted[name])
            result.tests = sorted(
                [
                    test_result
                    for (group_name, _), test_result in self._test_details.items()
                    if group_name == name
                ],
                key=lambda item: (item.location, item.name, item.nodeid),
            )

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

    def _update_test_result(self, group_name: str, key: tuple[str, str], outcome: str, report) -> None:
        test_result = self._test_details.get((group_name, key[1]))
        if test_result is None:
            return

        test_result.duration_seconds += float(getattr(report, "duration", 0.0) or 0.0)
        severity = {"NOT RUN": -1, "PASSED": 0, "SKIPPED": 1, "FAILED": 2, "ERROR": 3}
        status_map = {
            "passed": "PASSED",
            "skipped": "SKIPPED",
            "failed": "FAILED",
            "errors": "ERROR",
        }
        next_status = status_map[outcome]
        if severity[next_status] >= severity.get(test_result.status, -1):
            test_result.status = next_status

        if outcome in {"failed", "errors"}:
            details = _extract_report_details(report)
            if details:
                test_result.details = details


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

    Global receivers always receive every configured group summary. Group-level
    receivers are added for their group. When a recipient appears in multiple
    groups they receive one combined email containing all relevant group
    summaries. Duplicate addresses within the same group are de-duplicated so a
    single group is never attached twice to one delivery.
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


def build_report_email_recap(
    *,
    config: LexTestConfig,
    deliveries: list[RecipientDelivery],
) -> str:
    """Build a compact operator-facing recap before sending report emails."""

    if not deliveries:
        return "No Lex test report emails are configured for this run."

    backend = os.getenv(TEST_EMAIL_BACKEND_ENV_VAR, "").strip()
    lines = [
        f"Lex will send {len(deliveries)} report email(s).",
        f"From: {_formatted_sender(config)}",
    ]
    if config.email.get("reply_to"):
        lines.append(f"Reply-To: {config.email['reply_to']}")
    if backend:
        lines.append(f"Backend override: {backend}")
    else:
        lines.append("Backend: Django default (expects SENDGRID_API_KEY in the environment)")

    for index, delivery in enumerate(deliveries, start=1):
        recipient = f"{delivery.name} <{delivery.email}>" if delivery.name else delivery.email
        lines.append(f"{index}. To: {recipient}")
        lines.append(f"   Subject: {_build_delivery_subject(config=config, delivery=delivery)}")
        lines.append(f"   Groups: {delivery.groups_csv or '(none)'}")
    return "\n".join(lines)


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
    output_dir = config.report_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = filename_stem or f"lex-test-report-{timestamp}"
    output_path = output_dir / f"{filename}.pdf"
    context = _build_delivery_template_context(
        config=config,
        group_results=[plugin.results[group.name] for group in _select_config_groups(config, group_names)],
        pytest_exit_code=pytest_exit_code,
        recipient_name=None,
        recipient_email=None,
        report_title=title,
        subject_context=", ".join(group_names) if group_names else "All configured groups",
        headline=None,
        intro=None,
        outro=None,
        coverage_summary=plugin.coverage_summary,
        run_duration=plugin.run_duration,
    )
    html_content = _render_report_template("pdf.html", context)
    _write_pdf_from_html(html_content, output_path)
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
    if not deliveries:
        return []

    if not config.email.get("from_email"):
        raise LexTestConfigError(
            "email.from_email is required when using `lex pytest --report-and-email`."
        )

    backend = os.getenv(TEST_EMAIL_BACKEND_ENV_VAR, "").strip() or None
    connection_kwargs: dict[str, Any] = {"fail_silently": False}
    if backend:
        connection_kwargs["backend"] = backend
        if backend == "django.core.mail.backends.filebased.EmailBackend":
            file_path = os.getenv(TEST_EMAIL_FILE_PATH_ENV_VAR, "").strip()
            if not file_path:
                file_path = str(config.report_dir / "email-outbox")
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
            body=_render_delivery_text_body(
                delivery=delivery,
                pytest_exit_code=pytest_exit_code,
                coverage_summary=plugin.coverage_summary,
            ),
            from_email=_formatted_sender(config),
            to=[delivery.email],
            reply_to=[config.email["reply_to"]] if config.email.get("reply_to") else None,
            connection=connection,
        )
        logo_image_uri = _attach_inline_logo(message) or _branding_context().get("logo_image_uri")
        message.attach_alternative(
            _render_delivery_html_body(
                config=config,
                delivery=delivery,
                pytest_exit_code=pytest_exit_code,
                coverage_summary=plugin.coverage_summary,
                run_duration=plugin.run_duration,
                logo_image_uri=logo_image_uri,
            ),
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


def _template_assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "lex-test-report"


def _asset_data_uri(asset_path: Path) -> str:
    mime_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _branding_context() -> dict[str, Any]:
    branding = {
        "name": "Excellence Cloud",
        "logo_text": "EC",
        "primary_color": "#283067",
        "accent_color": "#24b6bb",
        "surface_color": "#f5f7fb",
        "logo_text_color": "#ffffff",
        "logo_alt": "Excellence Cloud",
    }
    logo_path = _template_assets_dir() / "lex-logo.png"
    if logo_path.exists():
        branding["logo_image_uri"] = _asset_data_uri(logo_path)
    return branding


def _build_template_environment():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    environment = Environment(
        loader=FileSystemLoader(str(_template_assets_dir())),
        autoescape=select_autoescape(["html", "xml"]),
    )
    environment.filters["status_badge"] = _status_badge
    environment.filters["status_tone"] = _status_tone
    environment.filters["format_timestamp"] = _format_timestamp
    return environment


def _write_pdf_from_html(html_content: str, output_path: Path) -> None:
    from weasyprint import HTML

    HTML(string=html_content, base_url=str(_template_assets_dir())).write_pdf(str(output_path))


def _render_report_template(template_name: str, context: dict[str, Any]) -> str:
    environment = _build_template_environment()
    return environment.get_template(template_name).render(**context)


def _select_config_groups(config: LexTestConfig, group_names: list[str] | None) -> list[GroupConfig]:
    selected_names = set(group_names) if group_names is not None else None
    return [group for group in config.groups if selected_names is None or group.name in selected_names]


def _format_timestamp(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def _status_badge(status: str | None) -> str:
    labels = {
        "success": "PASSED",
        "failure": "FAILED",
        "error": "ERROR",
        "warning": "SKIPPED",
        "skipped": "NO TESTS",
    }
    return labels.get((status or "").lower(), (status or "UNKNOWN").upper())


def _status_tone(status: str | None) -> str:
    tones = {
        "success": "#1b5e20",
        "failure": "#b91c1c",
        "error": "#7f1d1d",
        "warning": "#475569",
        "skipped": "#475569",
    }
    return tones.get((status or "").lower(), "#1a2230")


def _attach_inline_logo(message) -> str | None:
    logo_path = _template_assets_dir() / "lex-logo.png"
    if not logo_path.exists():
        return None

    from email.mime.image import MIMEImage

    content_id = "lex-report-logo"
    image = MIMEImage(logo_path.read_bytes())
    image.add_header("Content-ID", f"<{content_id}>")
    image.add_header("Content-Disposition", "inline", filename=logo_path.name)
    message.attach(image)
    return f"cid:{content_id}"


def _format_duration(value: float) -> str:
    if value <= 0:
        return ""
    if value >= 60:
        minutes, seconds = divmod(value, 60)
        return f"{int(minutes)}m {seconds:.1f}s"
    return f"{value:.2f}s"


def _extract_report_details(report) -> str:
    details = getattr(report, "longreprtext", "") or ""
    lines = [line.strip() for line in details.splitlines() if line.strip()]
    if not lines:
        return ""
    return " | ".join(lines[:3])[:600]


def _template_status(value: str) -> str:
    normalized = value.upper()
    if normalized == "PASSED":
        return "success"
    if normalized == "FAILED":
        return "failure"
    if normalized == "ERROR":
        return "error"
    if normalized == "SKIPPED":
        return "warning"
    if normalized == "NO TESTS":
        return "skipped"
    return "warning"


def _build_template_group(
    *,
    config: LexTestConfig,
    group_config: GroupConfig,
    group_result: GroupResult,
) -> dict[str, Any]:
    receivers: list[str] = []
    for receiver in config.receivers_for(group_config.name):
        email = receiver.get("email")
        name = receiver.get("name")
        receivers.append(f"{name} <{email}>" if name and email else name or email or "?")

    return {
        "name": group_config.name,
        "description": group_config.description,
        "status": _template_status(group_result.status),
        "counts": {
            "tests": group_result.test_count,
            "passed": group_result.passed,
            "failed": group_result.failed,
            "skipped": group_result.skipped,
            "errors": group_result.errors,
        },
        "coverage": None,
        "receivers": receivers,
        "tests": [
            {
                "name": test.name,
                "status": _template_status(test.status),
                "duration": _format_duration(test.duration_seconds),
                "location": test.location,
                "details": test.details,
            }
            for test in group_result.tests
        ],
    }


def _build_summary(groups: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "tests": sum(group["counts"]["tests"] for group in groups),
        "passed": sum(group["counts"]["passed"] for group in groups),
        "failed": sum(group["counts"]["failed"] for group in groups),
        "skipped": sum(group["counts"]["skipped"] for group in groups),
        "errors": sum(group["counts"]["errors"] for group in groups),
    }
    groups_passed = sum(1 for group in groups if group["status"] == "success")
    statuses = {group["status"] for group in groups}
    if "failure" in statuses:
        status = "failure"
    elif "error" in statuses:
        status = "error"
    elif counts["tests"] == 0:
        status = "skipped"
    elif "warning" in statuses:
        status = "warning"
    else:
        status = "success"
    return {
        "status": status,
        "group_count": len(groups),
        "groups_passed": groups_passed,
        "counts": counts,
    }


def _git_command_output(project_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _build_traceability_context(project_root: Path, run_duration: str | None) -> dict[str, str]:
    build_id = (
        os.getenv("GITHUB_SHA", "").strip()
        or _git_command_output(project_root, "rev-parse", "HEAD")
    )
    branch = (
        os.getenv("GITHUB_HEAD_REF", "").strip()
        or os.getenv("GITHUB_REF_NAME", "").strip()
        or _git_command_output(project_root, "branch", "--show-current")
    )

    run_url = ""
    server_url = os.getenv("GITHUB_SERVER_URL", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if server_url and repository and run_id:
        run_url = f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}"

    return {
        "duration": run_duration or "",
        "build_id": build_id,
        "branch": branch,
        "run_url": run_url,
    }


def _build_delivery_template_context(
    *,
    config: LexTestConfig,
    group_results: list[GroupResult],
    pytest_exit_code: int,
    recipient_name: str | None,
    recipient_email: str | None,
    report_title: str,
    subject_context: str,
    headline: str | None,
    intro: str | None,
    outro: str | None,
    coverage_summary: dict[str, Any] | None,
    run_duration: str | None,
) -> dict[str, Any]:
    group_config_by_name = {group.name: group for group in config.groups}
    groups = [
        _build_template_group(
            config=config,
            group_config=group_config_by_name[group_result.name],
            group_result=group_result,
        )
        for group_result in group_results
        if group_result.name in group_config_by_name
    ]
    summary = _build_summary(groups)
    return {
        "subject": report_title,
        "config": {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "branding": _branding_context(),
            "traceability": _build_traceability_context(config.project_root, run_duration),
        },
        "delivery": {
            "recipient_email": recipient_email,
            "name": recipient_name,
            "report_title": report_title,
            "subject_context": subject_context,
            "summary": summary,
            "report": {
                "subtitle": f"Lex pytest exit code {pytest_exit_code}",
                "notes": [],
            },
            "email": {
                "headline": headline or "",
                "intro": intro or "",
                "outro": outro or "",
            },
        },
        "summary": summary,
        "groups": groups,
        "coverage": coverage_summary,
        "has_group_coverage": False,
        "has_group_tests": any(group["tests"] for group in groups),
    }


def _render_delivery_text_body(
    *,
    delivery: RecipientDelivery,
    pytest_exit_code: int,
    coverage_summary: dict[str, Any] | None = None,
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
    if coverage_summary:
        lines.extend(["", f"{coverage_summary['label']}: {coverage_summary['display']}"])
    lines.extend(["", "The matching PDF report is attached."])
    return "\n".join(lines)


def _render_delivery_html_body(
    *,
    config: LexTestConfig,
    delivery: RecipientDelivery,
    pytest_exit_code: int,
    coverage_summary: dict[str, Any] | None,
    run_duration: str,
    logo_image_uri: str | None,
) -> str:
    subject = _build_delivery_subject(config=config, delivery=delivery)
    context = _build_delivery_template_context(
        config=config,
        group_results=delivery.group_results,
        pytest_exit_code=pytest_exit_code,
        recipient_name=delivery.name,
        recipient_email=delivery.email,
        report_title=subject,
        subject_context=delivery.groups_csv or "(none)",
        headline=None,
        intro=f"Lex pytest finished with exit code {pytest_exit_code}.",
        outro="The matching PDF report is attached to this email.",
        coverage_summary=coverage_summary,
        run_duration=run_duration,
    )
    if logo_image_uri:
        context["config"]["branding"]["logo_image_uri"] = logo_image_uri
    return _render_report_template("email.html", context)


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
