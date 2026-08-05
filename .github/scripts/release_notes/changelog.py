#!/usr/bin/env python3
"""Render a digest as a Keep a Changelog section.

Pure: no network, no model, no clock. The date is passed in so the output is
reproducible, which is what lets publish_release_notes.yml re-derive the same
section at promotion time instead of carrying an artifact between workflows.
"""

from __future__ import annotations

PREAMBLE = """\
# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are generated from commits and pull requests at release time; see
`.github/scripts/release_notes/`.
"""

# Types that are real work but not changes to the shipped product.
EXCLUDED_TYPES = frozenset({"docs", "test", "ci", "chore", "build"})

# Order matters: it is the order sections appear. `other` joins Changed so
# that non-conforming commits are reported rather than silently dropped.
_SECTIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Added", frozenset({"feat"})),
    ("Fixed", frozenset({"fix"})),
    ("Changed", frozenset({"refactor", "perf", "style", "other"})),
    ("Removed", frozenset({"revert"})),
)


def _line(change: dict, repo: str) -> str:
    url = f"https://github.com/{repo}/commit/{change['sha']}"
    suffix = f" (#{change['pr_number']})" if change.get("pr_number") else ""
    return f"- **{change['component']}** {change['subject']} ([{change['sha']}]({url})){suffix}"


def render(digest: dict, *, date: str, repo: str) -> str:
    """Render one release section. Returns the heading alone if nothing shipped."""
    version = digest["tag"].lstrip("v")
    parts = [f"## [{version}] - {date}", ""]

    shippable = [c for c in digest["changes"] if c["type"] not in EXCLUDED_TYPES]

    breaking = [c for c in shippable if c.get("breaking")]
    if breaking:
        parts.append("### Breaking")
        parts.extend(_line(c, repo) for c in breaking)
        parts.append("")

    for heading, types in _SECTIONS:
        rows = [c for c in shippable if c["type"] in types and not c.get("breaking")]
        if not rows:
            continue
        parts.append(f"### {heading}")
        parts.extend(_line(c, repo) for c in rows)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _strip_existing_section(existing: str, heading: str) -> str:
    """Remove an already-present section for the same version, if any.

    publish_release_notes.yml exposes a `tag` input described as
    "(re)generate", and re-running a failed job is routine, so the same
    version can legitimately be rendered twice. Without this the second run
    appends a byte-identical duplicate — and the workflow's `git diff --quiet`
    guard does not catch it, because the file genuinely did change.
    """
    lines = existing.splitlines(keepends=True)
    out, skipping = [], False
    for line in lines:
        if line.startswith(heading):
            skipping = True
            continue
        if skipping:
            # Any other version heading ends the section being replaced.
            if line.startswith("## ["):
                skipping = False
            else:
                continue
        out.append(line)
    return "".join(out)


def prepend(existing: str | None, section: str) -> str:
    """Insert `section` directly below the preamble, replacing any same-version entry."""
    if not existing:
        return f"{PREAMBLE}\n{section}"

    # "## [2.1.7] - 2026-08-05" -> "## [2.1.7]", so a re-render on a different
    # date still replaces rather than duplicates.
    first = section.splitlines()[0]
    heading = first.split(" - ")[0] if " - " in first else first.rstrip()
    existing = _strip_existing_section(existing, heading)

    marker = PREAMBLE.rstrip()
    if existing.startswith(marker):
        rest = existing[len(marker):].lstrip("\n")
        return f"{marker}\n\n{section}\n{rest}"
    return f"{section}\n{existing}"
