#!/usr/bin/env python3
"""Refuse to publish lex-app pinned to a frontend that does not exist.

`requirements.txt` carries `lex-app-frontend~=X.Y.Z`, and that one line does
two jobs:

  1. pip installs it, so a version that was never published makes every
     `pip install lex-app` fail with ResolutionImpossible, and
  2. the release notes read it as provenance and turn it into a PAC tag
     (`vX.Y.Z`), so a version that was never tagged degrades the frontend half
     of every note to "not yet recorded".

Both failures land on someone else — a customer, or a reader of the changelog —
minutes to days after the release is already out and unpublishable. This check
runs before the build, where the answer is a red job and a rerun.

No pin at all is fine and exits 0: the pin lands only once the frontend package
is published, and until then the frontend is resolved the old way.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPI_JSON = "https://pypi.org/pypi/{name}/json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_notes import ranges  # noqa: E402


def read_pin(requirements: Path) -> str | None:
    """The pinned frontend version, or None when the file declares none."""
    if not requirements.is_file():
        return None
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = ranges.PIN_RE.fullmatch(line)
        if match:
            return match["version"]
    return None


def published_versions(name: str, *, opener=urllib.request.urlopen) -> set[str] | None:
    """Every version of `name` on PyPI, or None when PyPI could not be asked.

    None is not an empty set. A network blip must not read as "the package was
    never published" and block a release that is perfectly fine.
    """
    try:
        with opener(PYPI_JSON.format(name=name), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()        # a real answer: no such project
        return None
    except Exception:
        return None
    releases = payload.get("releases")
    return set(releases) if isinstance(releases, dict) else None


def _version_key(version: str) -> tuple:
    """Sort key that orders 1.2.10 after 1.2.9.

    A plain string sort does not: it puts "1.12.0" before "1.9.0", so the
    "newest published" hint in the failure message would name an old release
    and send the reader looking in the wrong place. Leading numeric components
    sort numerically; whatever trails them (rc1, .post1) sorts as text, which
    is rough but only ever decides the hint, never the pass/fail.
    """
    head, parts = [], version.replace("-", ".").split(".")
    for index, part in enumerate(parts):
        if not part.isdigit():
            return (tuple(head), ".".join(parts[index:]))
        head.append(int(part))
    return (tuple(head), "")


def check(version: str | None, available: set[str] | None) -> tuple[int, str]:
    """(exit code, message). Pure, so the decision table is testable."""
    if version is None:
        return 0, (f"No `{ranges.PIN_NAME}` pin in {ranges.REQUIREMENTS_PATH} — "
                   "nothing to check.")
    if available is None:
        return 0, (f"Could not reach PyPI to confirm {ranges.PIN_NAME} {version}. "
                   "Continuing — a network failure is not a bad pin.")
    if not available:
        return 1, (
            f"{ranges.REQUIREMENTS_PATH} pins {ranges.PIN_NAME}~={version}, but no "
            f"`{ranges.PIN_NAME}` project exists on PyPI. Publish the frontend "
            "first, or remove the pin."
        )
    if version not in available:
        newest = max(available, key=_version_key)
        return 1, (
            f"{ranges.REQUIREMENTS_PATH} pins {ranges.PIN_NAME}~={version}, which "
            f"is not on PyPI (newest published: {newest}). Every `pip install "
            "lex-app` would fail to resolve, and the release note would look for "
            f"a `{ranges.pac_tag_for(version)}` tag that does not exist."
        )
    return 0, f"{ranges.PIN_NAME} {version} is published. OK."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", default=str(REPO_ROOT / ranges.REQUIREMENTS_PATH))
    args = parser.parse_args(argv)

    version = read_pin(Path(args.requirements))
    code, message = check(
        version, published_versions(ranges.PIN_NAME) if version else None
    )
    if code:
        print(f"::error title=Unpublished frontend pin::{message}")
    else:
        print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
