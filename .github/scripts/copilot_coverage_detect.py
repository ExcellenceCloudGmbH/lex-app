"""Phase 1 heuristic detection for ``copilot_coverage_check.yml``.

Spec: ``docs/superpowers/specs/2026-05-26-copilot-test-automation-pipeline-design.md``
(Feature 4, §7.2 — Approach D, Phase 1).

For each source file modified by the PR, decide whether the PR also
touches a matching test. The match rule has two arms:

  (a) the test file imports the source module path, or
  (b) the test file contains the source filename stem.

Either arm is sufficient. Arm (b) catches the common case where tests
reference the source by name (fixture, comment, string lookup) without a
direct import — important because the test-cluster pattern in this repo
keeps cluster `models.py` local rather than importing app code.

Outputs JSON on stdout::

    {
      "status": "pass" | "miss",
      "untested": ["lex/core/foo.py", ...],
      "matched": {"lex/api/bar.py": "lex/test_project/tests/.../test_x.py"},
      "skipped_allowlist": [...]
    }

Inputs are paths to two files holding the PR's changed-file lists, one
per line, produced by ``gh pr diff`` upstream. We split into a source-list
(``added``/``modified`` ``.py`` files inside any framework package under
``lex/`` — ``lex/lex_app/``, ``lex/core/``, ``lex/api/``,
``lex/audit_logging/``, ``lex/process_admin/``, … — excluding the test
trees, docs, and other non-source packages) and a test-list (any path
matching ``lex/test_project/tests/**/test_*.py``).

Pure-Python, no third-party deps — runs on a stock GitHub runner.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Framework source lives in app/packages directly under ``lex/`` —
# ``lex/lex_app/``, ``lex/core/``, ``lex/api/``, ``lex/audit_logging/``,
# ``lex/process_admin/`` and any future app. Rather than enumerate apps
# (a new app would silently escape the coverage gate until someone
# remembered to add it), we treat *every* package under ``lex/`` as
# source and subtract the trees where coverage is not meaningful.
_SOURCE_ROOT = "lex/"

# Packages under ``lex/`` that are NOT testable framework source: the
# test trees themselves, docs, frontend, static assets, vendored data.
_EXCLUDED_PACKAGES = frozenset(
    {
        "test_project",  # the cluster test tree itself
        "tests",         # legacy audit test tree
        "docs",
        "react",
        "assets",
        "legacy_data",
        "bin",
    }
)

_TEST_TREE = "lex/test_project/tests/"
_ALLOWLIST_NAMES = ("__init__.py", "apps.py")
_ALLOWLIST_NAME_STARTSWITH = ("settings",)  # settings.py, settings_dev.py, settings_local.py


@dataclass
class DetectionResult:
    status: str  # "pass" | "miss"
    untested: list[str] = field(default_factory=list)
    matched: dict[str, str] = field(default_factory=dict)
    skipped_allowlist: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "untested": self.untested,
                "matched": self.matched,
                "skipped_allowlist": self.skipped_allowlist,
            },
            indent=2,
            sort_keys=True,
        )


def _package_of(path: str) -> str | None:
    """``lex/core/foo/bar.py`` → ``core``.

    Returns ``None`` for paths outside ``lex/`` and for loose top-level
    files directly under ``lex/`` (e.g. ``lex/runtime_config.py``) — those
    are one-off scripts, not app code, and are deliberately out of scope.
    """
    if not path.startswith(_SOURCE_ROOT):
        return None
    rel = path[len(_SOURCE_ROOT):]
    if "/" not in rel:
        return None  # loose file directly under lex/, not a package member
    return rel.split("/", 1)[0]


def _is_allowlisted(path: str) -> bool:
    pkg = _package_of(path)
    if pkg is None or pkg in _EXCLUDED_PACKAGES:
        return False
    if "/migrations/" in path:
        return True
    name = path.rsplit("/", 1)[-1]
    if name in _ALLOWLIST_NAMES:
        return True
    if name.endswith(".py") and any(name.startswith(p) for p in _ALLOWLIST_NAME_STARTSWITH):
        return True
    return False


def _is_source_file(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    pkg = _package_of(path)
    if pkg is None or pkg in _EXCLUDED_PACKAGES:
        return False
    # Don't treat test files committed inside an app tree as source.
    name = path.rsplit("/", 1)[-1]
    if name.startswith("test_"):
        return False
    return True


def _is_test_file(path: str) -> bool:
    if not path.startswith(_TEST_TREE):
        return False
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def _module_path(source_file: str) -> str:
    """``lex/lex_app/foo/bar.py`` → ``lex.lex_app.foo.bar``."""
    no_ext = source_file[:-3] if source_file.endswith(".py") else source_file
    return no_ext.replace("/", ".")


def _stem(source_file: str) -> str:
    """``lex/lex_app/foo/bar.py`` → ``bar``."""
    return source_file.rsplit("/", 1)[-1][:-3]


def _test_matches_source(test_path: str, repo_root: Path, source: str) -> bool:
    """Return True if ``test_path`` covers ``source`` per the match rule.

    Reads the test file ONCE from disk; both arms of the rule operate on
    the same buffer.
    """
    full = repo_root / test_path
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Test file is in the diff but missing on disk (e.g. deletion);
        # treat as no-match. The Phase 2 path will produce a useful
        # error message rather than crashing the workflow.
        return False

    module = _module_path(source)
    stem = _stem(source)

    # Arm (a): a direct or partial-path import of the source module.
    # The ``\b`` boundary keeps ``lex.lex_app.foo`` from matching
    # ``lex.lex_app.foobar``.
    import_pattern = re.compile(
        rf"\b(?:from|import)\s+{re.escape(module)}\b"
    )
    if import_pattern.search(text):
        return True

    # Arm (b): the source filename stem appears as an identifier.
    # We require word boundaries so `models` doesn't match `models_v2`.
    stem_pattern = re.compile(rf"\b{re.escape(stem)}\b")
    if stem_pattern.search(text):
        return True

    return False


def _read_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def detect(
    changed_files: list[str],
    *,
    repo_root: Path,
) -> DetectionResult:
    """Run Phase 1 over the PR's changed-file set."""
    result = DetectionResult(status="pass")

    source_files = [f for f in changed_files if _is_source_file(f)]
    test_files = [f for f in changed_files if _is_test_file(f)]

    for src in source_files:
        if _is_allowlisted(src):
            result.skipped_allowlist.append(src)
            continue

        matched_test = None
        for test in test_files:
            if _test_matches_source(test, repo_root, src):
                matched_test = test
                break

        if matched_test:
            result.matched[src] = matched_test
        else:
            result.untested.append(src)

    if result.untested:
        result.status = "miss"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect untested source files in a PR diff (Feature 4 Phase 1)."
    )
    parser.add_argument(
        "--changed-files", type=Path, required=True,
        help="File listing the PR's changed file paths, one per line.",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="Repository root (default: cwd). Used to resolve test file contents.",
    )
    args = parser.parse_args()

    changed = _read_list(args.changed_files)
    result = detect(changed, repo_root=args.repo_root)
    print(result.to_json())
    # Exit 0 either way; the workflow inspects the JSON status field.
    # Non-zero exit is reserved for genuine script failures.
    return 0


if __name__ == "__main__":
    sys.exit(main())
