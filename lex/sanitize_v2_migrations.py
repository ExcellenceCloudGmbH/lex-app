import argparse
import ast
import json
from pathlib import Path
from typing import Iterable, Set


OP_MODEL_NAME_KWS = {"model_name", "name"}
OP_NAME_WITH_CAMEL_MODEL = {
    "CreateModel",
    "DeleteModel",
    "RenameModel",
    "AlterModelTable",
    "AlterModelOptions",
    "AlterModelManagers",
}


def _iter_migration_files(migrations_dir: Path, only_files: list[str] | None) -> Iterable[Path]:
    if only_files:
        for file_name in sorted(only_files):
            file_path = migrations_dir / file_name
            if file_path.name == "__init__.py":
                continue
            if file_path.suffix != ".py":
                continue
            if file_path.exists():
                yield file_path
        return

    for file_path in sorted(migrations_dir.glob("*.py")):
        if file_path.name == "__init__.py":
            continue
        yield file_path


def _table_suffixes_to_preserve(manifest_file: Path, app_name: str) -> Set[str]:
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    freeze_tables = data.get("freeze_tables", [])
    prefix = f"{app_name}_"
    suffixes: Set[str] = set()
    for table in freeze_tables:
        if table.startswith(prefix):
            suffixes.add(table[len(prefix) :].lower())
    return suffixes


def _get_call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _extract_model_token(node: ast.Call) -> str | None:
    op_name = _get_call_name(node)
    if op_name is None:
        return None

    for kw in node.keywords:
        if kw.arg not in OP_MODEL_NAME_KWS:
            continue
        if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, str):
            continue
        raw = kw.value.value
        # model_name is usually already lowercase.
        if kw.arg == "model_name":
            return raw.lower()
        # name is model class for model-level operations.
        if kw.arg == "name" and op_name in OP_NAME_WITH_CAMEL_MODEL:
            return raw.lower()
    return None


def _sanitize_file(file_path: Path, preserve_suffixes: Set[str], dry_run: bool) -> int:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))

    removed = 0
    changed = False

    class MigrationSanitizer(ast.NodeTransformer):
        def visit_Assign(self, node: ast.Assign):  # noqa: N802
            nonlocal removed, changed
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "operations"
                and isinstance(node.value, ast.List)
            ):
                new_elts = []
                for elt in node.value.elts:
                    if isinstance(elt, ast.Call):
                        model_token = _extract_model_token(elt)
                        if model_token and model_token in preserve_suffixes:
                            removed += 1
                            changed = True
                            continue
                    new_elts.append(elt)
                node.value.elts = new_elts
            return self.generic_visit(node)

    sanitizer = MigrationSanitizer()
    new_tree = sanitizer.visit(tree)
    ast.fix_missing_locations(new_tree)

    if changed and not dry_run:
        rewritten = ast.unparse(new_tree) + "\n"
        file_path.write_text(rewritten, encoding="utf-8")

    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove migration operations that would mutate/delete V1-only tables."
    )
    parser.add_argument("--migrations-dir", required=True, help="Path to target migrations dir.")
    parser.add_argument("--manifest", required=True, help="Pre-migrate freeze manifest JSON file.")
    parser.add_argument("--app-name", required=True, help="Django app name (table prefix).")
    parser.add_argument(
        "--only-files",
        nargs="*",
        default=None,
        help="Optional migration file names to sanitize (e.g. 0002_x.py 0003_y.py).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files.")
    args = parser.parse_args()

    migrations_dir = Path(args.migrations_dir)
    manifest_file = Path(args.manifest)
    preserve_suffixes = _table_suffixes_to_preserve(manifest_file, args.app_name)

    print(f"Sanitizing migrations in: {migrations_dir}")
    print(f"Preserve model suffixes: {sorted(preserve_suffixes)}")
    print(f"Dry run: {args.dry_run}")

    total_removed = 0
    for file_path in _iter_migration_files(migrations_dir, args.only_files):
        removed = _sanitize_file(file_path, preserve_suffixes, args.dry_run)
        if removed:
            total_removed += removed
            print(f"  {file_path.name}: removed {removed} operation(s)")

    print(f"Total removed operations: {total_removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
