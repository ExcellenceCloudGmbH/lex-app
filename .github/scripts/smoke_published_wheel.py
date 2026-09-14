#!/usr/bin/env python3
"""Install the just-published lex-app from PyPI and check it is usable.

The release pipeline published a wheel and then walked away. Nothing confirmed
the artifact customers are about to install actually works — so a release that
forgot its frontend, or pinned a version that does not exist, would be found by
a customer rather than by us.

The frontend is a dependency now, not a file inside the wheel, so the check is
that pip can satisfy it and that the installed package can locate its bundle:

  1. `lex-app==X` installs, with dependencies,
  2. `lex_app_frontend` came with it,
  3. its bundle exists and has an index.html.

PyPI indexing lags publication by a minute or two, so the install is retried
rather than failed on the first miss.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PACKAGE = "lex-app"
FRONTEND = "lex_app_frontend"


def install(version: str, target: Path, *, attempts: int = 6, delay: float = 20.0,
            run=subprocess.run, sleep=time.sleep) -> None:
    """Install `version` and its dependencies, retrying while PyPI catches up."""
    last = ""
    for attempt in range(1, attempts + 1):
        result = run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--target", str(target), f"{PACKAGE}=={version}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"installed {PACKAGE}=={version} (attempt {attempt})")
            return
        last = (result.stderr or "").strip()
        # A resolution failure will not fix itself by waiting — it means the
        # pin names a version that does not exist, which is a bad release
        # rather than a slow index.
        if "ResolutionImpossible" in last or "No matching distribution" in last and FRONTEND.replace("_", "-") in last:
            sys.exit(f"{PACKAGE}=={version} cannot be resolved: {last[:400]}")
        if attempt < attempts:
            print(f"attempt {attempt}: not installable yet, retrying in {delay:.0f}s",
                  file=sys.stderr)
            sleep(delay)
    sys.exit(
        f"{PACKAGE}=={version} was published but is not installable after "
        f"{attempts} attempts: {last[:400]}"
    )


def check_frontend(target: Path) -> str:
    """Assert the frontend came with it. Returns the version installed."""
    dist = sorted(target.glob(f"{FRONTEND}-*.dist-info"))
    if not dist:
        sys.exit(
            f"{PACKAGE} installed without {FRONTEND} — check the pin is still "
            "in requirements.txt, which is what pyproject reads as the "
            "dependency list"
        )
    version = dist[-1].name[len(f"{FRONTEND}-"):-len(".dist-info")]

    bundle = target / FRONTEND / "build"
    if not bundle.is_dir() or not (bundle / "index.html").is_file():
        sys.exit(
            f"{FRONTEND} {version} carries no usable bundle at {bundle} — it "
            "was published from a build that produced no SPA, and every page "
            "would 404"
        )
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="The version just published.")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=20.0)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        install(args.version, target, attempts=args.attempts, delay=args.delay)
        frontend = check_frontend(target)

    print(f"OK — {PACKAGE}=={args.version} installs and brings {FRONTEND} {frontend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
