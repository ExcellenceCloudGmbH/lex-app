#!/usr/bin/env python3
"""Refuse to publish a lex-app wheel that delivers no frontend at all.

A wheel serves the single-page app one of two ways:

  1. it carries the compiled bundle at ``lex/react/build``, or
  2. it depends on ``lex-app-frontend``, which pip installs alongside it.

Exactly one is enough. Neither is a release where every page 404s, and the
in-tree bundle stopped shipping the moment the frontend became a dependency —
so the two changes are only safe *together*. This check is what makes the order
they land in stop mattering: drop the bundle before the pin exists and the
release fails here, loudly, instead of reaching a customer.

It reads the built artifact rather than the packaging config, because the
config is two files and a default (``include-package-data``) that interact, and
what actually ends up in the zip is the only claim worth testing.

Runs after the build and before the upload — the last moment where the answer
is a red job. `smoke_published_wheel.py` checks the same property against PyPI,
but only once the release is out and unpublishable.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Declared here rather than imported from `release_notes.ranges`, which also
# knows this name. A packaging guard that cannot run without the release-notes
# package is a guard with a reason to be skipped, and this one runs between the
# build and the upload where nothing else should be able to break it.
# `test_check_wheel_frontend.py` fails if the two ever disagree.
FRONTEND_DIST = "lex-app-frontend"
REQUIREMENTS = "requirements.txt"

# The file whose absence means the bundle is not really there. A directory of
# assets with no entry point serves nothing.
BUNDLE_ENTRY = "lex/react/build/index.html"

_REQUIRES_RE = re.compile(r"^Requires-Dist:\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def normalise(name: str) -> str:
    """PEP 503 normalisation: `lex_app_frontend` and `lex-app-frontend` are one."""
    return re.sub(r"[-_.]+", "-", name).lower()


def find_wheel(dist: Path) -> Path | None:
    """The single wheel in `dist`, or None when that is not what is there.

    More than one is as much of a problem as none: the publish step uploads
    whatever it finds, so checking an arbitrary one of them would attest to a
    file that may not be the one customers get.
    """
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) == 1:
        return wheels[0]
    return None


def inspect(wheel: Path) -> tuple[bool, list[str]]:
    """(carries the bundle, distributions it requires) for a built wheel."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        has_bundle = BUNDLE_ENTRY in names
        metadata = next(
            (n for n in names if n.endswith(".dist-info/METADATA")), None
        )
        requires: list[str] = []
        if metadata:
            for line in archive.read(metadata).decode("utf-8").splitlines():
                match = _REQUIRES_RE.match(line)
                if match:
                    requires.append(match["name"])
    return has_bundle, requires


def check(has_bundle: bool, requires: list[str]) -> tuple[int, str]:
    """(exit code, message). Pure, so the decision table is testable."""
    wanted = normalise(FRONTEND_DIST)
    declares = any(normalise(name) == wanted for name in requires)

    if has_bundle and declares:
        return 0, (
            f"This wheel carries {BUNDLE_ENTRY} AND depends on {FRONTEND_DIST}. "
            "Both work, but the bundle is dead weight the dependency already "
            "covers — drop it from MANIFEST.in once the pin has shipped once."
        )
    if declares:
        return 0, f"Frontend arrives via the {FRONTEND_DIST} dependency. OK."
    if has_bundle:
        return 0, f"Frontend ships in the wheel at {BUNDLE_ENTRY}. OK."
    return 1, (
        f"This wheel has no frontend. It carries no {BUNDLE_ENTRY} and declares "
        f"no `{FRONTEND_DIST}` dependency, so every page would 404 for anyone "
        f"who installs it. Either add the pin to {REQUIREMENTS}, or "
        "put the bundle back in MANIFEST.in. One of the two is required."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default=str(REPO_ROOT / "dist"),
                        help="Directory holding the built wheel.")
    args = parser.parse_args(argv)

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

    code, message = check(*inspect(wheel))
    if code:
        print(f"::error title=Wheel ships no frontend::{message}")
    else:
        print(f"{wheel.name}: {message}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
