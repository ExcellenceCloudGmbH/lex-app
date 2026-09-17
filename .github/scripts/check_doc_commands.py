#!/usr/bin/env python3
"""Fail when the docs tell a reader to run a `lex` command that does not exist.

Written after `lex Init` was found in the published docs 34 times -- in step one
of the installation guide and in every tutorial part. The command is `init`.
`Init.py` was renamed to `init.py` in the framework and the docs were never
followed; click does an exact lookup, so every new user following the
getting-started guide hit:

    Error: No such command 'Init'. (Did you mean one of: 'Init2', 'init'?)

The command set is assembled the same way the CLI assembles it, without
importing anything:

  * `@lex.command(name=...)` in lex/bin/lex.py            -- implemented by the CLI
  * module basenames under lex/**/management/commands/     -- Django passthrough
  * Django's own built-ins                                 -- also passthrough
  * anything matching `ai-*` / `ai_*`                       -- delegated at runtime
    to lex-mcp-local, which this repo cannot enumerate

    python .github/scripts/check_doc_commands.py                 # mirror-owned docs/
    python .github/scripts/check_doc_commands.py <path>...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_doc_imports import REPO, mirror_owned_paths, warn_if_behind  # noqa: E402

CLI_SOURCE = REPO / "lex" / "bin" / "lex.py"

# Directories that sit inside `lex/` on a working copy and are not the package.
# A clean CI checkout has none of them, which is why this was missing: the scan
# was correct in the only place it ever ran. Locally `lex/` holds `.venv-test`
# and `.claude/worktrees`, so this glob found Django's OWN management commands
# in site-packages and reported `lex collectstatic`, `lex runserver` and 36
# others as undocumented framework commands.
_NOT_THE_PACKAGE = {
    ".venv", ".venv-test", "venv", "site-packages", "node_modules",
    ".claude", ".git", "__pycache__", "build", "dist", ".tox", ".mypy_cache",
}


def management_commands(root: Path):
    """Management-command modules that this package actually ships."""
    for path in root.rglob("management/commands/*.py"):
        if _NOT_THE_PACKAGE.isdisjoint(path.parts):
            yield path


#: Django ships these; the CLI forwards anything it does not implement itself.
DJANGO_BUILTINS = {
    "changepassword", "check", "collectstatic", "compilemessages", "createcachetable",
    "createsuperuser", "dbshell", "diffsettings", "dumpdata", "flush", "inspectdb",
    "loaddata", "makemessages", "makemigrations", "migrate", "optimizemigration",
    "runserver", "sendtestemail", "shell", "showmigrations", "sqlflush", "sqlmigrate",
    "sqlsequencereset", "squashmigrations", "startapp", "startproject", "test",
    "testserver",
}

#: Words that follow "lex " in prose without being commands.
NOT_A_COMMAND = {"command", "commands", "app", "apps", "framework", "cli", "test"}

#: A line that says a command does not exist is documentation ABOUT its
#: absence, not an instruction to run it. The CLI reference has one: it warns
#: readers off `lex generate-configs` and points at the `lex-generate-configs`
#: console script instead. Scoped to the line, so it cannot mask a real error
#: anywhere else on the page.
DISCLAIMS = ("there is no", "does not exist", "is not a command", "no such command")

COMMAND_NAME = re.compile(r"@lex\.command\(\s*name\s*=\s*[\"']([A-Za-z0-9_-]+)[\"']")
# A backtick before `lex` is the NORMAL case -- `lex init` in prose is how a
# command is named. Only a word character or hyphen means this is part of
# some other token (`lex-generate-configs`, `flex init`).
MENTION = re.compile(r"(?<![\w-])lex ([A-Za-z][A-Za-z0-9_-]*)")


def known_commands() -> set[str]:
    names = set(DJANGO_BUILTINS)
    if CLI_SOURCE.is_file():
        names |= set(COMMAND_NAME.findall(CLI_SOURCE.read_text(encoding="utf-8")))
    for path in management_commands(REPO / "lex"):
        if path.name != "__init__.py":
            names.add(path.stem)
    return names


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or mirror_owned_paths()
    commands = known_commands()
    if not commands:
        print("Could not enumerate commands — is this the lex-app repo?", file=sys.stderr)
        return 2

    pages = sorted({p for t in targets for p in ([t] if t.is_file() else t.rglob("*.md"))})
    failures, checked = [], 0
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        # Drafts are not published, and the working notes among them discuss
        # wrong command names on purpose.
        parts = text.split("---")
        if text.startswith("---") and len(parts) > 2 and "draft: true" in parts[1]:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if any(phrase in lowered for phrase in DISCLAIMS):
                continue
            for m in MENTION.finditer(line):
                name = m.group(1)
                if name in NOT_A_COMMAND or name.startswith(("ai-", "ai_")):
                    continue
                checked += 1
                if name not in commands:
                    near = sorted(c for c in commands if c.lower() == name.lower())
                    hint = f" Did you mean `lex {near[0]}`?" if near else ""
                    failures.append((page, lineno, name, hint))

    for page, lineno, name, hint in failures:
        try:
            rel = page.relative_to(REPO)
        except ValueError:
            rel = page
        print(
            f"::error file={rel},line={lineno}::`lex {name}` is not a command.{hint}\n"
            f"    {rel}:{lineno}   `lex {name}` is not a command.{hint}",
            file=sys.stderr,
        )

    if failures:
        print(f"\n{len(failures)} unknown command reference(s).", file=sys.stderr)
        return 0 if warn_if_behind() else 1

    print(f"OK: {checked} `lex <command>` reference(s) across {len(pages)} page(s) all exist.")
    return coverage(pages, commands)


def coverage(pages: list[Path], commands: set[str]) -> int:
    """The other direction: a shipped command with no mention anywhere.

    The reference page is the only complete list of commands -- `lex --help`
    shows ten of them, because printing the Django passthrough would mean
    starting Django just to render help. So a new management command that
    nobody documents is invisible to every reader. This is the check that
    stops that happening quietly.
    """
    ours = {
        p.stem
        for p in management_commands(REPO / "lex")
        if p.name != "__init__.py" and not p.name.startswith("test_")
    }
    ours |= set(COMMAND_NAME.findall(CLI_SOURCE.read_text(encoding="utf-8")))
    mentioned: set[str] = set()
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for name in ours:
            if f"lex {name}" in text:
                mentioned.add(name)

    missing = sorted(ours - mentioned)
    if not missing:
        print(f"OK: all {len(ours)} shipped command(s) appear in the docs.")
        return 0

    print(
        "::error title=Undocumented commands::"
        + f"{len(missing)} command(s) ship with no mention in the published docs: "
        + ", ".join(f"`lex {m}`" for m in missing)
        + ". Add each to reference/CLI Commands.md, or to the "
        + '"Commands this page leaves out" table there with the reason.',
        file=sys.stderr,
    )
    return 0 if warn_if_behind() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
