"""Dry-run the Copilot prompt assembly without leaving your laptop.

Reads a YAML or JSON document on stdin in the same shape as the issue
form (``.github/ISSUE_TEMPLATE/copilot-test-request.yml``), invokes the
same ``assemble_prompt`` function the production ``copilot_test_bot.yml``
workflow uses, and prints the resulting ``[copilot-task]`` body to stdout.

Wraps — does not duplicate — ``.github/scripts/copilot_assemble_prompt.py``.
That file is the single source of truth for prompt assembly; if it
changes, this dry-runner picks the change up for free on the next run.

Usage::

    cat <<EOF | python3 scripts/copilot_dry_run.py
    mode: regression
    behaviour: |
      When a CalculatedModel.create() hits IntegrityError on save,
      the framework should call delete_models_with_same_defining_fields,
      rewire the pk, and retry the save.
    cluster_hint: 7g
    EOF

No GitHub API calls. No network. Purely local — answers Feature 3's
"can I see what Copilot will see before I pay for cycles?" question.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the workflow-side assembler importable without packaging gymnastics.
# The script lives at <repo>/scripts/, the assembler at <repo>/.github/scripts/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / ".github" / "scripts"))

# Imported below the sys.path mutation. The assembler ships its own
# IssueInput / Mode types; reusing them keeps the dry-runner in lockstep
# with what CI does — schema drift would surface here as an ImportError
# instead of a silent mismatch in production.
from copilot_assemble_prompt import IssueInput, Mode, assemble_prompt  # noqa: E402


_DEFAULT_TEST_PLAN_DIR = _REPO_ROOT / "lex" / "test_project" / "test-plan"

# Form field name → IssueInput attribute. Kept here (not in the assembler)
# because it's a UI/CLI concern: the form uses these names, the assembler
# uses snake_case Python identifiers. Single mapping point for renames.
_FIELD_ALIASES = {
    "mode": "mode",
    "behaviour": "behaviour",
    "behavior": "behaviour",  # accept US spelling as a courtesy
    "reproducer": "reproducer",
    "reproducer_steps": "reproducer",
    "cluster_hint": "cluster_hint",
    "files": "files",
    "title": "title",
    "number": "number",
}


def _load_doc(text: str) -> dict:
    """Parse YAML if PyYAML is available, else JSON.

    YAML support is optional so the dry-runner works in a fresh venv
    without a hard PyYAML dependency. JSON always works because it's in
    the stdlib.
    """
    text = text.strip()
    if not text:
        raise ValueError("stdin is empty — pipe a YAML or JSON form document in.")

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "PyYAML not installed and stdin is not valid JSON. "
                "Either install PyYAML (`pip install pyyaml`) or pass JSON."
            ) from exc
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"top-level document must be a mapping (got {type(parsed).__name__}). "
            "Use ``key: value`` form, not a list."
        )
    return parsed


def _normalize(raw: dict) -> dict:
    """Translate form-style field names into IssueInput kwargs."""
    out: dict = {}
    for key, value in raw.items():
        canonical = _FIELD_ALIASES.get(key.lower())
        if canonical is None:
            # Tolerate unknown keys silently — the issue form may add
            # fields before this script catches up, and we don't want a
            # spurious failure to block a local dry-run.
            continue
        out[canonical] = value
    return out


def _coerce_files(value) -> list[str]:
    """``files:`` may be a list, a comma-separated string, or omitted."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    raise ValueError(f"`files` must be list or comma-separated string, got {type(value).__name__}")


def build_issue(fields: dict) -> IssueInput:
    """Construct an IssueInput with sensible defaults for dry-run."""
    mode_raw = fields.get("mode")
    if not mode_raw:
        raise ValueError(
            "`mode` is required. Use one of: regression, bug-repro, fix-and-test."
        )
    try:
        mode = Mode(str(mode_raw).strip())
    except ValueError as exc:
        raise ValueError(
            f"invalid mode {mode_raw!r}. Choose: regression, bug-repro, fix-and-test."
        ) from exc

    behaviour = str(fields.get("behaviour", "")).strip()
    if not behaviour:
        raise ValueError("`behaviour` is required — describe the contract to test.")

    return IssueInput(
        # Dry-run has no real issue number; use 0 as a sentinel that
        # downstream tools can recognise. The assembler embeds it in the
        # body as "issue #0" + "Fixes #0" — fine for inspection, harmful
        # if the operator pastes the output into a real issue. The
        # cookbook (`docs/ci-cd/local-dev.md`) calls this out.
        number=int(fields.get("number", 0)),
        title=str(fields.get("title", "(dry-run — no title)")).strip(),
        mode=mode,
        behaviour=behaviour,
        reproducer=str(fields.get("reproducer", "")).strip(),
        cluster_hint=str(fields.get("cluster_hint", "")).strip(),
        files=_coerce_files(fields.get("files")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run Copilot prompt assembly from a form-style doc on stdin.",
    )
    parser.add_argument(
        "--test-plan-dir", type=Path, default=_DEFAULT_TEST_PLAN_DIR,
        help=f"Test-plan directory (default: {_DEFAULT_TEST_PLAN_DIR.relative_to(_REPO_ROOT)}).",
    )
    args = parser.parse_args()

    raw_text = sys.stdin.read()
    try:
        fields = _normalize(_load_doc(raw_text))
        issue = build_issue(fields)
        body = assemble_prompt(issue, test_plan_dir=args.test_plan_dir)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")

    # Quick stats so the operator knows whether the prompt fits in the
    # 64KB GitHub issue body cap before they ever hit "Submit".
    size = len(body.encode("utf-8"))
    print(
        f"\n# --- dry-run stats ---\n"
        f"# bytes: {size}  (GitHub cap: 65,536; assembler cap: 60,000)\n"
        f"# mode:  {issue.mode.value}\n"
        f"# files: {len(issue.files)} supplied",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
