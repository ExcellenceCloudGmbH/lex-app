#!/usr/bin/env python3
"""CLI for the release-notes pipeline.

    python -m release_notes draft-notes    --tag v2.1.7 > body.md
    python -m release_notes render-changelog --tag v2.1.7 --date 2026-08-05

Both subcommands rebuild the digest from scratch. It is deterministic given a
fixed tag, which is why the changelog can be re-derived at promotion time
instead of being carried between two workflow runs as an artifact.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from release_notes import changelog, digest, notes, ranges

EXEMPLAR_PATH = ranges.REPO_ROOT / "docs/releases/RELEASE_NOTES_2.1.3_github.md"
CHANGELOG_PATH = ranges.REPO_ROOT / "CHANGELOG.md"


def _all_tags(tag: str) -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--merged", tag, "--sort=-creatordate"],
        cwd=ranges.REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Almost always a shallow clone or a missing tag fetch, and the raw
        # CalledProcessError ("exit status 128") says none of that.
        raise SystemExit(
            f"Could not list tags reachable from {tag!r}: "
            f"{result.stderr.strip() or 'git exited ' + str(result.returncode)}\n"
            "The tag must exist locally — check out with fetch-depth: 0, or "
            "run `git fetch origin --tags`."
        )
    return result.stdout.splitlines()


def _pac_log(pac_checkout: Path):
    """A `run_log` bound to a PAC working copy instead of this repository."""

    def run_log(from_ref: str | None, to_ref: str) -> str:
        spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
        result = subprocess.run(
            ["git", "log", "--no-merges", f"--pretty=%h{digest._FIELD_SEP}%s", spec],
            cwd=pac_checkout, check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    return run_log


def _digest_for(tag: str, *, pac_checkout: Path | None = None) -> dict:
    previous = ranges.previous_release_tag(tag, tags=_all_tags(tag))

    backend = digest.enrich_with_prs(digest.collect_commits(previous, tag))

    frontend: list[digest.Commit] = []
    fe_range = ranges.frontend_range(previous, tag)
    if fe_range is None:
        print("No frontend manifest at one or both ends — omitting the frontend section.",
              file=sys.stderr)
    elif pac_checkout is None:
        print(f"Frontend range {fe_range.from_sha}..{fe_range.to_sha} resolved, but no PAC "
              "checkout was supplied — omitting the frontend section.", file=sys.stderr)
    else:
        frontend = digest.collect_commits(
            fe_range.from_sha, fe_range.to_sha, run_log=_pac_log(pac_checkout)
        )
        print(f"Frontend: {len(frontend)} commits in "
              f"{fe_range.from_sha}..{fe_range.to_sha}", file=sys.stderr)

    return digest.build_digest(tag, previous, backend, frontend)


def _pac_arg(args: argparse.Namespace) -> Path | None:
    return Path(args.pac_checkout) if args.pac_checkout else None


def _load_exemplar() -> str:
    """The style exemplar, or a built-in stand-in if it is missing.

    A missing style file must never sink a release note. This crashed CI once:
    the exemplar existed on the author's laptop but had never been committed,
    so every local dry run passed and the first real run died before
    `notes.draft()` — the one function that guarantees a usable body.
    """
    if EXEMPLAR_PATH.exists():
        return EXEMPLAR_PATH.read_text(encoding="utf-8")
    print(
        f"Style exemplar missing at {EXEMPLAR_PATH} — falling back to the "
        "built-in one. Restore the file to control the house style.",
        file=sys.stderr,
    )
    return notes.FALLBACK_EXEMPLAR


def _pick_model():
    """Resolve the drafting transport from LEX_NOTES_PROVIDER and the environment.

    Which model writes the note is configuration. Set LEX_NOTES_PROVIDER to
    anthropic / gemini / openai / github-models, or leave it unset for "auto",
    which takes the first provider whose key is present.

    Returns None when nothing is configured, which `notes.draft()` turns into a
    fallback body. An explicit choice that cannot be honoured raises instead —
    quietly drafting with a different provider than the one requested would be
    worse than a visible failure.
    """
    choice = os.environ.get("LEX_NOTES_PROVIDER", "")
    try:
        resolved = notes.resolve_provider(choice, os.environ)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return None

    if resolved is None:
        configured = ", ".join(env for env, _ in notes.PROVIDERS.values())
        print(
            f"No drafting credential found. Set one of: {configured} — "
            "and optionally LEX_NOTES_PROVIDER to choose between them.",
            file=sys.stderr,
        )
        return None

    name, call = resolved
    how = "requested" if choice and choice.strip().lower() != "auto" else "auto-selected"
    print(f"Drafting via {name} ({how}).", file=sys.stderr)
    return call


def cmd_draft_notes(args: argparse.Namespace) -> int:
    model = _pick_model()
    if model is None:
        # _pick_model already explained why on stderr. Hand `draft()` something
        # that raises so it produces its fallback body: exiting here instead
        # would leave the release with no note and no explanation.
        def model(prompt: str) -> str:  # noqa: F811
            raise RuntimeError("no usable notes provider — see the log above")

    exemplar = _load_exemplar()
    body = notes.draft(
        _digest_for(args.tag, pac_checkout=_pac_arg(args)),
        exemplar=exemplar,
        model=model,
    )
    sys.stdout.write(body)
    return 0


def cmd_render_changelog(args: argparse.Namespace) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "ExcellenceCloudGmbH/lex-app")
    section = changelog.render(
        _digest_for(args.tag, pac_checkout=_pac_arg(args)), date=args.date, repo=repo
    )
    existing = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else None
    CHANGELOG_PATH.write_text(changelog.prepend(existing, section), encoding="utf-8")
    print(f"Wrote {CHANGELOG_PATH}", file=sys.stderr)
    return 0


def cmd_append_frontend_note(args: argparse.Namespace) -> int:
    """Add a frontend addendum to a published release body.

    The addendum is mechanical rather than model-drafted: it lists the frontend
    subjects from the digest. Re-drafting prose here would clash in tone with a
    body a human has already edited, and cost a model call to say what the
    digest already says plainly.
    """
    built = _digest_for(args.tag, pac_checkout=_pac_arg(args))
    frontend = [c for c in built["changes"] if c["component"] == "frontend"]
    if not frontend:
        print(f"{args.tag}: no frontend changes to append.", file=sys.stderr)
        return 0

    addendum = "\n".join(
        ["### Frontend changes (added after publication)", ""]
        + [f"- {c['subject']}" for c in frontend]
    )

    read = subprocess.run(
        ["gh", "release", "view", args.tag, "--json", "body", "-q", ".body"],
        cwd=ranges.REPO_ROOT, capture_output=True, text=True,
    )
    if read.returncode != 0:
        print(f"Could not read release {args.tag}: {read.stderr.strip()}", file=sys.stderr)
        return 1

    updated = notes.append_addendum(read.stdout, addendum)
    if updated == read.stdout:
        print(f"{args.tag}: an addendum is already present, leaving it alone.", file=sys.stderr)
        return 0

    if args.dry_run:
        sys.stdout.write(updated)
        return 0

    write = subprocess.run(
        ["gh", "release", "edit", args.tag, "--notes-file", "-"],
        cwd=ranges.REPO_ROOT, input=updated, capture_output=True, text=True,
    )
    if write.returncode != 0:
        print(f"Could not update release {args.tag}: {write.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"{args.tag}: frontend addendum appended.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # --pac-checkout is accepted now and supplied later. Wiring the PAC
    # checkout into the workflows needs FRONTEND_REPO_TOKEN, which does not
    # exist — see the spec's Prerequisite section. Without it the frontend
    # section is omitted rather than guessed.
    for name, help_text, handler in (
        ("draft-notes", "Draft the business note to stdout.", cmd_draft_notes),
        ("render-changelog", "Prepend a section to CHANGELOG.md.", cmd_render_changelog),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--tag", required=True)
        p.add_argument(
            "--pac-checkout",
            default=None,
            help="Path to a process-admin-general-client working copy. "
                 "Omit to skip the frontend section.",
        )
        if name == "render-changelog":
            p.add_argument("--date", required=True, help="ISO date, e.g. 2026-08-05")
        p.set_defaults(func=handler)

    add = sub.add_parser(
        "append-frontend-note",
        help="Append a frontend addendum to a published release body. Never rewrites it.",
    )
    add.add_argument("--tag", required=True)
    add.add_argument("--pac-checkout", default=None)
    add.add_argument("--dry-run", action="store_true")
    add.set_defaults(func=cmd_append_frontend_note)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
