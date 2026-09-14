#!/usr/bin/env python3
"""Fail when the framework reads an environment variable the docs never mention.

The reference page had 66 entries. The framework read 131 names, and 58 of the
missing ones were operator-facing -- the whole `DB`/`REDIS`/`SHAREPOINT`
connection surface, every `CELERY_*` tuning knob, `LEX_API_KEY`, and six of the
eight keys `lex setup` writes into every new project's `.env`. A reader could
not look up a variable their own scaffolded config file contained.

Extraction deliberately covers both shapes, because the first sweep of this
codebase used only the first and missed ten variables:

  * `os.getenv("NAME")` / `os.environ.get("NAME")` / `os.environ["NAME"]`
  * `_env_bool("NAME", ...)` / `_env_int(...)` / `_env_str(...)` -- the proxy's
    typed wrappers, which are how most of its configuration is read

Names read through a variable rather than a literal (`os.getenv(self.ENV_X)`)
cannot be found this way. There is one, and it is in INTERNAL_OR_INDIRECT below
with the name it resolves to.

    python .github/scripts/check_doc_env_vars.py                 # mirror-owned docs/
    python .github/scripts/check_doc_env_vars.py <path>...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_doc_imports import REPO, mirror_owned_paths, warn_if_behind  # noqa: E402

PACKAGE = REPO / "lex"

LITERAL = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]{2,})["']\s*\]"""
    r"""|_env_(?:bool|int|str|float)\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""
)

#: Not configuration a reader of the docs can set, or already covered.
#: Every entry needs a reason -- an unexplained one is how a real variable
#: disappears from the docs permanently.
INTERNAL_OR_INDIRECT = {
    # Injected by GitHub Actions.
    "GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GITHUB_REPOSITORY", "GITHUB_RUN_ID",
    "GITHUB_SERVER_URL", "GITHUB_SHA", "COMMIT_SHA",
    # Test-suite switches, read only by lex/test_project and lex/tests.
    "LEX_RUN_KEYCLOAK_INTEGRATION", "LEX_RUN_KEYCLOAK_DESTRUCTIVE",
    "LEX_KEYCLOAK_INTEGRATION_ENV", "LEX_STRESS_VOLUME",
    "LEX_SUBMODEL_BRANCH", "LEX_SUBMODEL_NAME",
    # Set by the CLI for its own children, never by a user.
    "CALLED_FROM_START_COMMAND", "CELERY_WORKER_RUNNING", "C_FORCE_ROOT",
    # Standard Django/Python, documented by their own projects.
    "DJANGO_SETTINGS_MODULE", "DJANGO_BASE_PATH",
    # Container runtime facts, not settings.
    "ARCHITECTURE", "POD_IP",
    # Frontend build-time vars, baked into the bundle rather than read at runtime.
    "REACT_APP_DOMAIN_BASE", "REACT_APP_GRAFANA_DASHBOARD_URL",
    # Dev-only override for a component served from a local dev server.
    "LEX_WIDGET_HOST_DEV_URL", "LEX_VIEW_COMPONENT_URL",
    # Path to a generated artefact, set by the migration workflow itself.
    "LEX_LEGACY_FREEZE_MANIFEST_PATH",
    # Test-suite switches, read only by lex/test_project.
    "LEX_ALLOW_CLEAN_REBUILD_NON_TEST_CLIENT", "LEX_STRESS_REPORT_DIR",
}


def read_names() -> set[str]:
    names: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for m in LITERAL.finditer(path.read_text(encoding="utf-8", errors="replace")):
            names.add(next(g for g in m.groups() if g))
    return names - INTERNAL_OR_INDIRECT


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or mirror_owned_paths()
    pages = sorted({p for t in targets for p in ([t] if t.is_file() else t.rglob("*.md"))})
    if not pages:
        print(f"No .md files under {', '.join(str(t) for t in targets)}")
        return 0

    documented: set[str] = set()
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for token in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text):
            documented.add(token)

    names = read_names()
    missing = sorted(names - documented)
    if missing:
        print(
            "::error title=Undocumented environment variables::"
            + f"{len(missing)} variable(s) the framework reads appear nowhere in the "
            + "published docs: " + ", ".join(f"`{m}`" for m in missing)
            + ". Add each to reference/Environment Variables.md, or to "
            + "INTERNAL_OR_INDIRECT in this script with the reason it is not "
            + "something a reader sets.",
            file=sys.stderr,
        )
        return 0 if warn_if_behind() else 1

    print(f"OK: all {len(names)} environment variable(s) the framework reads are documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
