#!/usr/bin/env python3
"""Docs mirror helper for Model A (mirror-down from lex-app-docs).

Two subcommands, both driven by the single manifest at ``docs/.docs-sync.yml``:

  sync           Mirror each managed path from an upstream checkout of
                 lex-app-docs/content/ into this repo's docs/, deleting files
                 that disappeared upstream (scoped to managed paths only).

  check-readonly Given a list of changed files, exit non-zero if any fall under
                 a managed (mirror-owned) path. Used by docs_mirror_guard.yml to
                 reject local edits to read-only mirrored docs.

The manifest is the only place that declares which docs/ paths are mirror-owned;
both subcommands read it so they can never disagree.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
MANIFEST_PATH = DOCS_DIR / ".docs-sync.yml"


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict:
    """Parse and minimally validate the docs-sync manifest."""
    with manifest_path.open("r", encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)

    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path} did not parse to a mapping")
    managed = manifest.get("managed_paths")
    if not isinstance(managed, list) or not managed:
        raise ValueError(f"{manifest_path} must define a non-empty 'managed_paths' list")
    if not manifest.get("source_root"):
        raise ValueError(f"{manifest_path} must define 'source_root'")
    return manifest


def managed_paths(manifest: dict) -> list[str]:
    """Normalised list of mirror-owned paths (POSIX, no leading 'docs/')."""
    out = []
    for raw in manifest["managed_paths"]:
        p = str(raw).strip().strip("/")
        if p:
            out.append(p)
    return out


def _is_under_managed(rel_path: str, managed: list[str]) -> bool:
    """True if rel_path (relative to docs/) is a managed file or under a managed dir."""
    rel = rel_path.strip().strip("/")
    for m in managed:
        if rel == m or rel.startswith(m + "/"):
            return True
    return False


def cmd_sync(args: argparse.Namespace) -> int:
    """Mirror managed paths from the upstream content checkout into docs/."""
    manifest = load_manifest()
    source_content = Path(args.source).resolve()  # .../lex-app-docs/content
    if not source_content.is_dir():
        print(f"::error::source content dir not found: {source_content}", file=sys.stderr)
        return 2

    changed = 0
    for rel in managed_paths(manifest):
        src = source_content / rel
        dest = DOCS_DIR / rel
        if not src.exists():
            print(f"::warning::managed path missing upstream, skipping: content/{rel}")
            continue

        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            # Trailing slashes => sync dir *contents*; --delete prunes removed files.
            rsync_cmd = ["rsync", "-a", "--delete", f"{src}/", f"{dest}/"]
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            rsync_cmd = ["rsync", "-a", str(src), str(dest)]

        print(f"mirror: content/{rel} -> docs/{rel}")
        subprocess.run(rsync_cmd, check=True)
        changed += 1

    print(f"Mirrored {changed} managed path(s).")
    return 0


def cmd_check_readonly(args: argparse.Namespace) -> int:
    """Fail if any changed file is under a managed (mirror-owned) path."""
    manifest = load_manifest()
    managed = managed_paths(manifest)

    files = list(args.files)
    if not files and not sys.stdin.isatty():
        files = [line.strip() for line in sys.stdin if line.strip()]

    violations = []
    for f in files:
        norm = f.strip()
        if norm.startswith("docs/"):
            rel = norm[len("docs/"):]
            if _is_under_managed(rel, managed):
                violations.append(norm)

    if violations:
        print(
            "::error::This PR edits docs that are MIRRORED (read-only) from "
            f"{manifest.get('source_repo', 'lex-app-docs')}. "
            "Edit them upstream in lex-app-docs/content/ instead; the mirror sync "
            "will bring them back down. Offending files:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("OK: no edits to mirror-owned docs paths.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Mirror managed paths from upstream content/ into docs/")
    p_sync.add_argument(
        "--source",
        required=True,
        help="Path to the checked-out lex-app-docs content/ directory",
    )
    p_sync.set_defaults(func=cmd_sync)

    p_check = sub.add_parser(
        "check-readonly", help="Fail if changed files touch mirror-owned docs paths"
    )
    p_check.add_argument(
        "files",
        nargs="*",
        help="Changed file paths (repo-relative). If omitted, read from stdin.",
    )
    p_check.set_defaults(func=cmd_check_readonly)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
