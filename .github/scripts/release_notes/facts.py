#!/usr/bin/env python3
"""Facts about a release that a note must state and a model cannot infer.

Commit prose does not reliably say whether a release needs a migration, adds a
command an operator has to run, or introduces an environment variable. Asking a
model to work it out from subject lines is how v2.1.3's published note came to
claim a migration that had actually shipped in v2.1.1.

So these are computed from the diff and handed to the drafter as facts. The
difference that matters is between "no migration" and "we could not tell":
`needs_migration` is None for the latter, and `render` says so rather than
offering the reassurance a reader would act on.
"""

from __future__ import annotations

import re
import subprocess

from .ranges import REPO_ROOT

_MIGRATION_RE = re.compile(r"/migrations/(?P<name>0\d{3}_[A-Za-z0-9_]+)\.py$")
_COMMAND_RE = re.compile(r"/management/commands/(?P<name>[a-z][A-Za-z0-9_]*)\.py$")
# All three spellings appear in this codebase. Matching only os.environ.get
# lost LEX_TIME_ZONE from v2.1.4 — the one setting a non-Berlin instance has
# to change.
_ENV_RE = re.compile(
    r"""os\.(?:getenv\(|environ(?:\.get\(|\[))\s*["'](?P<name>[A-Z][A-Z0-9_]*)["']"""
)


def _added(from_ref: str, to_ref: str) -> str:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{from_ref}..{to_ref}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout


def _settings_diff(from_ref: str, to_ref: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{from_ref}..{to_ref}", "--", "*/settings.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout


def collect(from_ref: str, to_ref: str, *, added=_added, settings_diff=_settings_diff) -> dict:
    """Migrations, new commands and new environment variables in a range.

    Never raises: a release must not be held up because a fact could not be
    established. An unknown stays an unknown — see `needs_migration`.
    """
    try:
        paths = [p for p in added(from_ref, to_ref).split() if p]
        resolved = True
    except Exception:
        paths, resolved = [], False

    migrations = sorted({m["name"] for p in paths if (m := _MIGRATION_RE.search(p))})
    commands = sorted({m["name"] for p in paths if (m := _COMMAND_RE.search(p))})

    try:
        added_lines = [
            ln for ln in settings_diff(from_ref, to_ref).splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        env_vars = sorted({m["name"] for ln in added_lines for m in _ENV_RE.finditer(ln)})
    except Exception:
        env_vars = []

    return {
        "migrations": migrations,
        "commands": commands,
        "env_vars": env_vars,
        # True/False only when the diff was actually read. None means unknown,
        # which must never be presented to a reader as "nothing to do".
        "needs_migration": bool(migrations) if resolved else None,
    }


def render(collected: dict) -> str:
    """The facts as a prompt block. Always says something about migrations."""
    lines: list[str] = []

    needs = collected.get("needs_migration")
    if needs is None:
        lines.append(
            "Whether this release adds a database migration could not be determined. "
            "Say that the upgrade note could not be confirmed rather than stating "
            "that no action is needed."
        )
    elif needs:
        names = ", ".join(collected["migrations"])
        lines.append(
            f"This release ADDS {len(collected['migrations'])} database migration(s): {names}. "
            "The upgrade note must tell the reader to run migrations."
        )
    else:
        lines.append(
            "This release adds no migration. The upgrade note may state that no "
            "migration is needed — do not hedge, and do not invent one."
        )

    if collected.get("commands"):
        names = ", ".join(collected["commands"])
        lines.append(
            f"New command(s) an operator can run: {names}. If a command exists to "
            "repair or migrate data, the upgrade note must say when it is needed, "
            "how to run it, and what happens if it is run wrongly."
        )

    if collected.get("env_vars"):
        names = ", ".join(collected["env_vars"])
        lines.append(
            f"New or changed configuration read from the environment: {names}. "
            "Say what it does and what it defaults to."
        )

    return "\n".join(f"- {line}" for line in lines)
