import argparse
import ast
import glob
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Tuple

# Default target directory (overridable via CLI).
DEFAULT_MIGRATIONS_DIR = "/home/syscall/LUND_IT/ArmiraCashflowDB/migrations"


@dataclass(frozen=True)
class ReplacementRule:
    pattern: str
    replacement: str
    description: str


# Regex rules use token-aware boundaries to avoid accidental partial replacements.
REPLACEMENT_RULES: Tuple[ReplacementRule, ...] = (
    # Module imports
    ReplacementRule(
        r"(?m)^\s*import\s+generic_app\.generic_models\.fields\.PDF_field\s*$",
        "import lex.core.fields.PDF_field",
        "PDF field module import",
    ),
    ReplacementRule(
        r"(?m)^\s*import\s+generic_app\.generic_models\.fields\.XLSX_field\s*$",
        "import lex.core.fields.XLSX_field",
        "XLSX field module import",
    ),
    ReplacementRule(
        r"(?m)^\s*import\s+generic_app\.generic_models\.upload_model\s*$",
        "import lex.core.models.LexModel\nimport lex.core.models.CalculationModel",
        "Upload model module import",
    ),
    ReplacementRule(
        r"(?m)^\s*import\s+generic_app\.generic_models\.calculated_model\s*$",
        "import lex.core.mixins.CalculatedModelMixin",
        "Calculated model module import",
    ),
    ReplacementRule(
        r"(?m)^\s*import\s+django\.utils\.datetime_safe\s*$",
        "import django.utils.timezone",
        "Legacy datetime_safe import",
    ),
    # Field references
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.fields\.PDF_field\.PDFField\b",
        "lex.core.fields.PDF_field.PDFField",
        "PDF field reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.fields\.XLSX_field\.XLSXField\b",
        "lex.core.fields.XLSX_field.XLSXField",
        "XLSX field reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.upload_model\.IsCalculatedField\b",
        "models.BooleanField",
        "IsCalculatedField compatibility",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.upload_model\.CalculateField\b",
        "models.BooleanField",
        "CalculateField compatibility",
    ),
    # Base class / mixin references
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.calculated_model\.CalculatedModelMixin\b",
        "lex.core.mixins.CalculatedModelMixin.CalculatedModelMixin",
        "CalculatedModelMixin class reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.upload_model\.UploadModelMixin\b",
        "lex.core.models.CalculationModel.CalculationModel",
        "UploadModelMixin class reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.upload_model\.ConditionalUpdateMixin\b",
        "lex.core.models.LexModel.LexModel",
        "ConditionalUpdateMixin class reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.LexModel\b",
        "lex.core.models.LexModel.LexModel",
        "LexModel class reference",
    ),
    ReplacementRule(
        r"\bgeneric_app\.generic_models\.CalculationModel\b",
        "lex.core.models.CalculationModel.CalculationModel",
        "CalculationModel class reference",
    ),
    # Datetime references
    ReplacementRule(
        r"\bdjango\.utils\.datetime_safe\.datetime\.now\b",
        "django.utils.timezone.now",
        "datetime.now compatibility",
    ),
)

UNRESOLVED_PATTERNS: Tuple[str, ...] = (
    "generic_app.",
    "django.utils.datetime_safe",
    "lex.core.models.base",
    "lex.core.mixins.calculated",
    "lex.core.models.calculation_model",
)


def _validate_python(content: str, file_path: str) -> None:
    try:
        ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        raise SyntaxError(f"AST validation failed for {file_path}: {exc}") from exc


def _apply_rules(content: str) -> Tuple[str, List[str]]:
    applied: List[str] = []
    new_content = content
    for rule in REPLACEMENT_RULES:
        new_content, count = re.subn(rule.pattern, rule.replacement, new_content)
        if count:
            applied.append(f"{rule.description} (x{count})")
    return new_content, applied


def _find_unresolved(content: str) -> List[str]:
    unresolved = []
    for marker in UNRESOLVED_PATTERNS:
        if marker in content:
            unresolved.append(marker)
    return unresolved


def _iter_migration_files(migrations_dir: str) -> Iterable[str]:
    for file_path in sorted(glob.glob(os.path.join(migrations_dir, "*.py"))):
        if file_path.endswith("__init__.py"):
            continue
        yield file_path


def fix_migrations(migrations_dir: str, dry_run: bool = False) -> int:
    print(f"Scanning directory: {migrations_dir}")
    if not os.path.isdir(migrations_dir):
        print(f"❌ Error: Directory does not exist: {migrations_dir}")
        return 1

    total_files = 0
    changed_files = 0
    unresolved_summary = {}

    for file_path in _iter_migration_files(migrations_dir):
        total_files += 1
        print(f"\nProcessing {file_path}...")
        with open(file_path, "r", encoding="utf-8") as handle:
            original = handle.read()

        updated, applied = _apply_rules(original)
        unresolved = _find_unresolved(updated)
        if unresolved:
            unresolved_summary[file_path] = unresolved

        if not applied:
            print("  No changes needed.")
            continue

        _validate_python(updated, file_path)
        changed_files += 1
        for item in applied:
            print(f"  [FIXED] {item}")

        if dry_run:
            print("  [DRY-RUN] Skipping file write.")
        else:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(updated)
            print("  Wrote updated migration file.")

    print("\n========================================================")
    print("Migration rewrite summary")
    print("========================================================")
    print(f"Files scanned: {total_files}")
    print(f"Files changed: {changed_files}")
    print(f"Dry run:      {dry_run}")

    if unresolved_summary:
        print("Unresolved legacy markers detected:")
        for file_path, markers in unresolved_summary.items():
            print(f"  - {file_path}: {', '.join(markers)}")
    else:
        print("Unresolved markers: none")

    print("Done.")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite V1 migration imports/references for V2 compatibility.",
    )
    parser.add_argument(
        "migrations_dir",
        nargs="?",
        default=DEFAULT_MIGRATIONS_DIR,
        help="Path to migration directory containing *.py files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply rewrite logic and validation, but do not write files.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    raise SystemExit(fix_migrations(args.migrations_dir, dry_run=args.dry_run))
