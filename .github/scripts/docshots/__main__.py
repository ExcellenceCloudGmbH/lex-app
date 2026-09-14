#!/usr/bin/env python3
"""Build every documentation figure declared in a spec file.

    python -m docshots build docs/figures.yml --out ../lex-app-docs/content/images
    python -m docshots build docs/figures.yml --check

A figure is declared once, in one place, and rebuilt from the live product on
every run:

    figures:
      - out: cli/lex-help.svg
        terminal:
          command: lex --help
        anchors:
          pytest: '^\\s+pytest\\b.*'
        callouts:
          - {anchor: pytest, step: 1, text: "Runs pytest with Django bootstrapped."}

      - out: grid/table-settings.svg
        shot: docshots/grid/table-settings.json     # written by the Playwright helper
        callouts:
          - {anchor: gear, step: 1, text: "The gear opens the per-table settings."}

`--check` rebuilds without writing and fails if any anchor no longer resolves.
That is the whole reason anchors are regexes and selectors rather than pixel
coordinates: run it in CI and a renamed button breaks the docs build on the pull
request that renamed it, instead of being found by a reader six months later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from docshots import annotate, model, terminal
from docshots.annotate import Callout


def _figure_shot(fig: dict, spec_dir: Path) -> model.Shot:
    """Build (or load) the shot for one figure declaration."""
    if "terminal" in fig and "shot" in fig:
        raise ValueError(f"{fig.get('out')}: give either `terminal` or `shot`, not both")

    if "terminal" in fig:
        t = dict(fig["terminal"])
        command = t.pop("command")
        # `display` lets the figure show `lex --help` while actually running
        # the binary from a virtualenv. The reader copies what they can type.
        display = t.pop("display", None)
        shot = terminal.capture(command, anchors=fig.get("anchors", {}), **t)
        if display:
            shot.cells["command"] = display
            shot.title = shot.title.replace(command, display)
        shot.surface = fig.get("surface", "dark")
        return shot

    if "shot" in fig:
        shot = model.load(spec_dir / fig["shot"])
        if "surface" in fig:
            shot.surface = fig["surface"]
        return shot

    raise ValueError(f"{fig.get('out')}: needs a `terminal` or a `shot` source")


def _callouts(fig: dict) -> list[Callout]:
    return [
        Callout(anchor=c["anchor"], text=c["text"], step=c.get("step"))
        for c in fig.get("callouts", [])
    ]


def cmd_build(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec).resolve()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    figures = spec.get("figures") or []
    if not figures:
        print(f"{spec_path}: no figures declared", file=sys.stderr)
        return 0

    out_root = Path(args.out).resolve() if args.out else spec_path.parent
    failures: list[str] = []
    built = 0

    for fig in figures:
        name = fig.get("out") or "<unnamed>"
        try:
            shot = _figure_shot(fig, spec_path.parent)
            callouts = _callouts(fig)
            # Render before writing so a broken anchor cannot leave a stale
            # figure looking freshly built.
            svg = annotate.render(
                shot, callouts,
                strict=True,
                animate=not args.static,
                surface=fig.get("surface", shot.surface),
            )
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            continue

        if args.check:
            print(f"  ok    {name}")
        else:
            dest = out_root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(svg, encoding="utf-8")
            print(f"  wrote {dest.relative_to(out_root)}  ({len(svg):,} bytes)")
        built += 1

    if failures:
        print(f"\n{len(failures)} figure(s) could not be built:", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        print(
            "\nAn anchor that stopped resolving means the product moved. Update the\n"
            "selector or the caption — do not delete the anchor to make this pass.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{built} figure(s) {'checked' if args.check else 'built'}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="Build every figure in a spec file.")
    b.add_argument("spec", help="Path to the figures spec (YAML).")
    b.add_argument("--out", default=None,
                   help="Directory to write into. Defaults to the spec's own directory.")
    b.add_argument("--check", action="store_true",
                   help="Rebuild without writing; non-zero if any anchor stopped resolving.")
    b.add_argument("--static", action="store_true",
                   help="Emit the final state with no animation (PDF, email, GitHub markdown).")
    b.set_defaults(func=cmd_build)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
