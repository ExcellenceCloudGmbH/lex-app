#!/usr/bin/env python3
"""Fail when the docs teach a `lex` import that the framework does not provide.

This exists because a published page did. `calculations/scheduled calculations`
carried a full API reference -- parameter table, three conflict modes, a worked
example -- for `ScheduledCalculation.ensure()`. Neither the class nor the
module `lex.lex_app.scheduling` existed anywhere in the framework. The page had
been a design draft; it read like shipped documentation, so it was published as
shipped documentation. A reader following it would have hit ImportError on
line 1 of the first example.

The same sweep found `lex.utilities.multitasking`, which is the wrong path for
two classes the same docs import correctly elsewhere.

Neither is catchable by a build: Quartz renders a fenced code block without
looking inside it, and both pages built clean for months.

    python .github/scripts/check_doc_imports.py                  # mirror-owned docs/
    python .github/scripts/check_doc_imports.py <path>...         # or these paths
    python .github/scripts/check_doc_imports.py ../lex-app-docs/content

The default target is the set of paths `docs/.docs-sync.yml` declares
mirror-owned -- that is, the published documentation. Everything else under
`docs/` is internal working material (specs, plans, run notes) where naming an
API before it exists is the point, so checking it would be wrong.

Resolution is AST-only -- nothing is imported and nothing is executed, so this
runs in a bare checkout with no dependencies and no Django settings.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO / "lex"
DOCS_ROOT = REPO / "docs"
MANIFEST = DOCS_ROOT / ".docs-sync.yml"

#: Import lines that are deliberately not resolvable. Keep this empty if you
#: can: an entry here is a page the checker can no longer defend.
ALLOWED: set[str] = set()

FENCE = re.compile(r"^([ \t]*)(?:```|~~~)[ \t]*([A-Za-z0-9_+-]*)[ \t]*$")
IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+lex\b")


def mirror_owned_paths() -> list[Path]:
    """The published subset, read from the mirror manifest.

    Parsed with a three-line reader rather than PyYAML so the check needs no
    dependencies: the block is a flat list of scalars and stays that way.
    """
    if not MANIFEST.is_file():
        return [DOCS_ROOT]
    paths, inside = [], False
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        if raw.startswith("managed_paths:"):
            inside = True
            continue
        if inside:
            if raw[:1] not in {" ", "-", "", "#"}:
                break
            entry = raw.strip()
            if entry.startswith("- "):
                paths.append(DOCS_ROOT / entry[2:].strip().strip("\"'"))
    return [p for p in paths if p.exists()] or [DOCS_ROOT]


def mirror_is_behind() -> list[str]:
    """Managed paths the manifest declares that are not on disk.

    A non-empty answer means the mirror sync has not caught up -- `docs/` is
    not yet the published tree these checks are about. That is a problem with
    the sync, not with the docs, and failing here would report it in the wrong
    place and block the very change that fixes the sync.

    So all three checks downgrade to a warning while this is non-empty, and
    start enforcing by themselves once the next sync lands. Nothing to
    remember to turn back on.
    """
    if not MANIFEST.is_file():
        return []
    declared, inside = [], False
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        if raw.startswith("managed_paths:"):
            inside = True
            continue
        if inside:
            if raw[:1] not in {" ", "-", "", "#"}:
                break
            entry = raw.strip()
            if entry.startswith("- "):
                declared.append(entry[2:].strip().strip("\"'"))
    return [d for d in declared if not _has_content(DOCS_ROOT / d)]


def _has_content(path: Path) -> bool:
    """A path counts as mirrored only if something is actually in it.

    `exists()` alone is not enough. An empty directory satisfies it, contains
    no documentation, and would tell these checks the mirror had caught up --
    which is the one answer that switches them from warning to enforcing. Git
    cannot store an empty directory, so this cannot happen in a clean CI
    checkout; it happens on a working copy, which is exactly where someone
    would be running this by hand.
    """
    if path.is_file():
        return True
    if not path.is_dir():
        return False
    # Any file, not just markdown: `images/` and `videos/` are managed paths
    # and hold neither. Looking for *.md reported both as un-mirrored.
    return any(child.is_file() for child in path.rglob("*"))


def warn_if_behind() -> bool:
    """Print the notice and return True when checks should not fail."""
    behind = mirror_is_behind()
    if not behind:
        return False
    print(
        "::warning title=Mirror is behind::"
        f"docs/ is missing {len(behind)} path(s) the manifest declares "
        f"({', '.join(behind)}). The mirror sync has not caught up, so this "
        "check is reporting on a stale tree and will warn instead of fail. "
        "It enforces again once the next sync lands."
    )
    return True


def python_blocks(text: str):
    """Yield (start_line, block_text) for each fenced python block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m or m.group(2).lower() not in {"python", "py", "python3"}:
            i += 1
            continue
        indent, start = m.group(1), i + 1
        body: list[str] = []
        i += 1
        while i < len(lines):
            close = FENCE.match(lines[i])
            if close and close.group(2) == "":
                break
            body.append(lines[i][len(indent):] if lines[i].startswith(indent) else lines[i])
            i += 1
        yield start + 1, body
        i += 1


def statements(md: Path):
    """Yield (line_number, source_line) for every `lex` import in the page."""
    text = md.read_text(encoding="utf-8", errors="replace")
    for start, body in python_blocks(text):
        for offset, line in enumerate(body):
            if IMPORT_LINE.match(line):
                yield start + offset, line.strip()


def module_file(dotted: str) -> Path | None:
    """`lex.core.fields` -> the .py that defines it, or None."""
    rel = Path(*dotted.split("."))
    if rel.parts[:1] != ("lex",):
        return None
    base = REPO / rel
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def defined_names(path: Path) -> tuple[set[str], bool]:
    """Top-level bindings in a module, and whether it has a star-import."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set(), True  # unparseable: do not accuse it of anything
    names: set[str] = set()
    star = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                star = True
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    return names, star


def check_statement(line: str) -> str | None:
    """None if the import resolves; otherwise the reason it does not."""
    if line in ALLOWED:
        return None
    try:
        tree = ast.parse(line)
    except SyntaxError:
        return None  # an elided example (`from lex... import ...`), not a claim
    node = tree.body[0] if tree.body else None

    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("lex") and module_file(alias.name) is None:
                return f"no module `{alias.name}`"
        return None

    if not isinstance(node, ast.ImportFrom) or not node.module:
        return None

    target = module_file(node.module)
    if target is None:
        return f"no module `{node.module}`"

    names, star = defined_names(target)
    # A package re-exporting from submodules: a name may live one level down.
    if target.name == "__init__.py":
        star = star or any(
            (target.parent / f"{a.name}.py").is_file() or (target.parent / a.name).is_dir()
            for a in node.names
        )
    if star:
        return None

    missing = [a.name for a in node.names if a.name != "*" and a.name not in names]
    if missing:
        rel = target.relative_to(REPO)
        return f"`{node.module}` defines no {', '.join('`' + m + '`' for m in missing)} ({rel})"
    return None


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or mirror_owned_paths()
    if not PACKAGE_ROOT.is_dir():
        print(f"{PACKAGE_ROOT} not found — run this from the lex-app repo", file=sys.stderr)
        return 2

    pages = sorted(
        {p for t in targets for p in ([t] if t.is_file() else t.rglob("*.md"))}
    )
    if not pages:
        print(f"No .md files under {', '.join(str(t) for t in targets)}")
        return 0

    failures: list[tuple[Path, int, str, str]] = []
    checked = 0
    for page in pages:
        for lineno, line in statements(page):
            checked += 1
            reason = check_statement(line)
            if reason:
                failures.append((page, lineno, line, reason))

    for page, lineno, line, reason in failures:
        try:
            rel = page.relative_to(REPO)
        except ValueError:
            rel = page
        print(
            f"::error file={rel},line={lineno}::{reason}\n"
            f"    {rel}:{lineno}\n"
            f"        {line}\n"
            f"        {reason}",
            file=sys.stderr,
        )

    if failures:
        print(
            f"\n{len(failures)} unresolvable import(s) in {len(pages)} page(s). "
            "A code sample that cannot be imported is worse than no sample: "
            "fix the path, or unpublish the page if the API does not exist yet.",
            file=sys.stderr,
        )
        return 0 if warn_if_behind() else 1

    print(f"OK: {checked} `lex` import(s) across {len(pages)} page(s) all resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
