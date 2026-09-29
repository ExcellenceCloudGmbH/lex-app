#!/usr/bin/env python3
"""Refuse a lex-app wheel that is missing a file the framework reads at runtime.

Some framework code opens a file next to itself instead of importing it:

  * the ``core`` migration 0001 executes ``lex/core/sql/bitemporal_activation.sql``
    to install the in-database activation applier;
  * the Streamlit component shims serve their ``frontend/index.html``.

setuptools puts a non-Python file in the wheel only when the packaging declares
it -- ``[tool.setuptools.package-data]`` for the wheel, ``MANIFEST.in`` for the
sdist the release builds the wheel from. An editable install reads the source
tree, so every test passes either way. The gap shows only in a built wheel,
where the migration raises FileNotFoundError, ``lex init`` aborts, and the
instance never starts. That shipped in 2.2.2, 2.3.0 and 2.3.1.

Like ``check_wheel_frontend.py``, this reads the built artifact rather than the
config: every file under RUNTIME_DIRS in the source tree must be inside the
wheel at the same path. Add a directory here whenever code starts reading
files from one.
"""

from __future__ import annotations

import argparse
import fnmatch
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories whose files are opened at runtime, relative to the repo root.
RUNTIME_DIRS = (
    "lex/core/sql",
    "lex/lex_app/streamlit/_lex_view_component/frontend",
    "lex/lex_app/streamlit/_widget_host_component/frontend",
)

# Mirrors MANIFEST.in's global-exclude: never shipped, so never expected.
_IGNORED_NAMES = ("__pycache__", ".DS_Store")
_IGNORED_PATTERNS = ("*.pyc", "*.pyo", "*.pyd")


def _ignored(path: Path) -> bool:
    if any(part in _IGNORED_NAMES for part in path.parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in _IGNORED_PATTERNS)


def expected_files(root: Path, dirs: tuple[str, ...] = RUNTIME_DIRS) -> tuple[list[str], list[str]]:
    """(files the wheel must carry, runtime dirs that hold no file at all).

    An empty or absent directory means this list has gone stale -- a renamed
    directory would otherwise turn the guard into a silent pass.
    """
    files: list[str] = []
    empty: list[str] = []
    for rel in dirs:
        base = root / rel
        found = sorted(
            p.relative_to(root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not _ignored(p.relative_to(root))
        ) if base.is_dir() else []
        if found:
            files.extend(found)
        else:
            empty.append(rel)
    return files, empty


def find_wheel(dist: Path) -> Path | None:
    """The single wheel in `dist`, or None. Several are as bad as none: the
    publish step uploads all of them, so checking one proves nothing."""
    wheels = sorted(dist.glob("*.whl"))
    return wheels[0] if len(wheels) == 1 else None


def missing(wheel_names: list[str], expected: list[str]) -> list[str]:
    """Expected files the wheel does not carry. Pure, so the rule is testable."""
    present = set(wheel_names)
    return [name for name in expected if name not in present]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default=str(REPO_ROOT / "dist"),
                        help="Directory holding the built wheel.")
    parser.add_argument("--root", default=str(REPO_ROOT),
                        help="Source tree the wheel was built from.")
    args = parser.parse_args(argv)

    expected, empty = expected_files(Path(args.root))
    if empty:
        print(
            "::error title=Stale runtime-file list::These RUNTIME_DIRS hold no "
            f"file in the source tree: {empty}. Update "
            ".github/scripts/check_wheel_runtime_files.py so the guard still "
            "covers what the code reads."
        )
        return 1

    dist = Path(args.dist)
    wheel = find_wheel(dist)
    if wheel is None:
        found = sorted(p.name for p in dist.glob("*.whl")) if dist.is_dir() else []
        print(
            f"::error title=No wheel to check::Expected exactly one wheel in "
            f"{dist}, found {len(found)}: {found or 'nothing'}. The build step "
            "must run before this one."
        )
        return 1

    with zipfile.ZipFile(wheel) as archive:
        absent = missing(archive.namelist(), expected)
    if absent:
        print(
            "::error title=Wheel is missing runtime files::"
            f"{wheel.name} does not carry {absent}. Code opens these files at "
            "runtime, so an installed release fails the moment it needs one. "
            "Declare them under [tool.setuptools.package-data] in pyproject.toml "
            "and include them in MANIFEST.in."
        )
        return 1

    print(f"{wheel.name}: carries all {len(expected)} runtime files. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
