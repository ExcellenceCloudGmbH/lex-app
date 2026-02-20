#!/usr/bin/env python
"""Pure-Python implementation of the full migration workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


TABLE_SNAPSHOT_FILE = ".lex_tables_before.json"
PRE_FREEZE_MANIFEST_FILE = ".lex_legacy_freeze_manifest.pre_migrate.json"
FREEZE_MANIFEST_FILE = ".lex_legacy_freeze_manifest.json"
TABLE_SNAPSHOT_AFTER_FILE = ".lex_tables_after.json"


class WorkflowError(Exception):
    """Base workflow error."""


class WorkflowUsageError(WorkflowError):
    """Input/usage/configuration error."""


class WorkflowCommandError(WorkflowError):
    """Raised when a subprocess command fails."""

    def __init__(self, command: Sequence[str], exit_code: int):
        self.command = list(command)
        self.exit_code = int(exit_code)
        super().__init__(
            f"Command failed with exit code {self.exit_code}: {' '.join(self.command)}"
        )


@dataclass(frozen=True)
class WorkflowOptions:
    v2_root: Path
    db_name: str
    v1_source: Path | None = None
    migration_timestamp: str | None = None
    chunk_size: int = 500
    dry_run_backfill: bool = False
    enable_sanitization: bool = False
    backfill_only: bool = False
    pre_clean_jsons: bool = False
    rollback_on_failure: bool = False
    rollback_only: bool = False
    rollback_state_file: str = ".lex_migration_state_before.json"
    skip_auditlog_backfill: bool = False


@dataclass(frozen=True)
class WorkflowRuntime:
    script_dir: Path
    v2_root: Path
    v2_migrations_dir: Path
    app_name: str
    python_executable: Path


def _render_usage(program_name: str) -> str:
    return "\n".join(
        [
            "Usage:",
            f"  {program_name} <V1_MIGRATIONS_SOURCE> <V2_PROJECT_ROOT> <DB_NAME> "
            "[--migration-timestamp <ISO8601>] [--chunk-size <INT>] "
            "[--dry-run-backfill] [--enable-sanitization] [--backfill-only] "
            "[--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] "
            "[--rollback-state-file <PATH>] [--skip-auditlog-backfill]",
            f"  {program_name} <V2_PROJECT_ROOT> <DB_NAME> "
            "[--migration-timestamp <ISO8601>] [--chunk-size <INT>] "
            "[--dry-run-backfill] [--enable-sanitization] [--backfill-only] "
            "[--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] "
            "[--rollback-state-file <PATH>] [--skip-auditlog-backfill]",
            f"  {program_name} <DB_NAME> "
            "[--migration-timestamp <ISO8601>] [--chunk-size <INT>] "
            "[--dry-run-backfill] [--enable-sanitization] [--backfill-only] "
            "[--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] "
            "[--rollback-state-file <PATH>] [--skip-auditlog-backfill]",
        ]
    )


def _artifact_path(v2_root: Path, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    if path.is_absolute():
        return path
    return v2_root / path


def _run_command(command: Sequence[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(list(command), cwd=str(cwd), env=env, check=False)
    if result.returncode != 0:
        raise WorkflowCommandError(command, result.returncode)


def _run_command_no_raise(
    command: Sequence[str], cwd: Path, env: dict[str, str]
) -> int:
    result = subprocess.run(list(command), cwd=str(cwd), env=env, check=False)
    return int(result.returncode)


def _run_lex(
    runtime: WorkflowRuntime, env: dict[str, str], *lex_args: str
) -> None:
    command = [str(runtime.python_executable), "-m", "lex", *lex_args]
    _run_command(command, cwd=runtime.v2_root, env=env)


def _run_python_script(
    runtime: WorkflowRuntime, env: dict[str, str], script_path: Path, *script_args: str
) -> None:
    command = [str(runtime.python_executable), str(script_path), *script_args]
    _run_command(command, cwd=runtime.v2_root, env=env)


def _resolve_runtime(options: WorkflowOptions) -> WorkflowRuntime:
    v2_root = options.v2_root.expanduser().resolve()
    if not v2_root.is_dir():
        raise WorkflowUsageError(f"❌ Error: V2 project root does not exist: {v2_root}")

    venv_root = None
    if (v2_root / ".venv").is_dir():
        venv_root = v2_root / ".venv"
    elif (v2_root / "venv").is_dir():
        venv_root = v2_root / "venv"
    else:
        raise WorkflowUsageError(
            f"❌ Error: Virtual environment (.venv or venv) not found in {v2_root}"
        )

    python_executable = venv_root / "bin" / "python"
    if not python_executable.exists():
        raise WorkflowUsageError(
            f"❌ Error: Python executable not found in virtual environment: {python_executable}"
        )

    return WorkflowRuntime(
        script_dir=Path(__file__).resolve().parent,
        v2_root=v2_root,
        v2_migrations_dir=v2_root / "migrations",
        app_name=v2_root.name,
        python_executable=python_executable,
    )


def _migration_files(migrations_dir: Path, include_init: bool) -> list[Path]:
    files = sorted(migrations_dir.glob("*.py"))
    if include_init:
        return files
    return [file_path for file_path in files if file_path.name != "__init__.py"]


def _migration_file_names(migrations_dir: Path) -> list[str]:
    return [file_path.name for file_path in _migration_files(migrations_dir, include_init=False)]


def _cleanup_json_artifacts(runtime: WorkflowRuntime, options: WorkflowOptions) -> None:
    print("🧹 Pre-cleaning JSON artifacts...")
    artifacts = [
        runtime.v2_root / TABLE_SNAPSHOT_FILE,
        runtime.v2_root / TABLE_SNAPSHOT_AFTER_FILE,
        runtime.v2_root / PRE_FREEZE_MANIFEST_FILE,
        runtime.v2_root / FREEZE_MANIFEST_FILE,
        _artifact_path(runtime.v2_root, options.rollback_state_file),
    ]
    for path in artifacts:
        if path.exists():
            path.unlink()


def _resolve_v1_migrations_source(v1_source: Path) -> Path:
    migrations_subdir = v1_source / "migrations"
    if migrations_subdir.is_dir():
        print("ℹ️  Found 'migrations' subdirectory in V1 source.")
        return migrations_subdir

    nested_matches = sorted(v1_source.glob("*/migrations"))
    if nested_matches:
        match = nested_matches[0]
        print(f"ℹ️  Found nested migrations directory: {match}")
        return match

    return v1_source


def _prepare_v1_migration_files(options: WorkflowOptions, runtime: WorkflowRuntime) -> None:
    runtime.v2_migrations_dir.mkdir(parents=True, exist_ok=True)

    if options.v1_source is None:
        v1_migrations_source = runtime.v2_migrations_dir
        count = len(_migration_files(v1_migrations_source, include_init=False))
        if count == 0:
            raise WorkflowUsageError(
                "❌ Error: No migration files found in existing V2 migrations directory: "
                f"{v1_migrations_source}"
            )
        print(f"✅ Using {count} existing migration files from {v1_migrations_source}")
        return

    v1_source = options.v1_source.expanduser().resolve()
    v1_migrations_source = _resolve_v1_migrations_source(v1_source)

    if not v1_migrations_source.is_dir():
        raise WorkflowUsageError(
            f"❌ Error: V1 migrations directory {v1_migrations_source} does not exist."
        )

    count = len(_migration_files(v1_migrations_source, include_init=True))
    if count == 0:
        raise WorkflowUsageError(
            f"❌ Error: No .py migration files found in {v1_migrations_source}"
        )

    for stale_file in _migration_files(runtime.v2_migrations_dir, include_init=False):
        stale_file.unlink()

    files_to_copy = _migration_files(v1_migrations_source, include_init=True)
    if not files_to_copy:
        raise WorkflowUsageError(
            f"❌ Error: No .py migration files found in {v1_migrations_source}"
        )
    for file_path in files_to_copy:
        shutil.copy2(file_path, runtime.v2_migrations_dir / file_path.name)

    print(f"✅ Copied {count} migration files from {v1_migrations_source}")


def _print_step(title: str) -> None:
    print("--------------------------------------------------------")
    print(title)
    print("--------------------------------------------------------")


def _run_backfill_suite(
    options: WorkflowOptions, runtime: WorkflowRuntime, env: dict[str, str]
) -> None:
    _print_step("🔧 Step 9.1: Normalizing is_calculated Values...")
    normalize_args = [
        "normalize_is_calculated",
        "--chunk-size",
        str(options.chunk_size),
    ]
    if options.dry_run_backfill:
        normalize_args.append("--dry-run")
    _run_lex(runtime, env, *normalize_args)

    _print_step("⏳ Step 9.2: Backfilling Bitemporal History...")
    backfill_args = [
        "backfill_bitemporal_history",
        "--chunk-size",
        str(options.chunk_size),
        "--reason",
        "V1 migration snapshot",
    ]
    if options.migration_timestamp:
        backfill_args.extend(["--timestamp", options.migration_timestamp])
    if options.dry_run_backfill:
        backfill_args.append("--dry-run")
    _run_lex(runtime, env, *backfill_args)

    if options.skip_auditlog_backfill:
        print("ℹ️  Audit-log backfill skipped by flag.")
        return

    _print_step("🧾 Step 9.3: Backfilling Audit Logs...")
    audit_args = [
        "backfill_audit_logging",
        "--chunk-size",
        str(options.chunk_size),
        "--reason",
        "V1 audit log migration snapshot",
    ]
    if options.dry_run_backfill:
        audit_args.append("--dry-run")
    _run_lex(runtime, env, *audit_args)


def _emit_summary(payload: dict[str, object]) -> None:
    print("MIGRATION_WORKFLOW_SUMMARY_START")
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    print("MIGRATION_WORKFLOW_SUMMARY_END")


def _rollback_after_failure(
    exit_code: int, options: WorkflowOptions, runtime: WorkflowRuntime, env: dict[str, str]
) -> None:
    print(f"❌ Workflow failed (exit={exit_code}).")
    if not options.rollback_on_failure:
        return

    rollback_file = _artifact_path(runtime.v2_root, options.rollback_state_file)
    if rollback_file.is_file():
        print(
            "↩ Attempting rollback to captured migration state: "
            f"{options.rollback_state_file}"
        )
        rc = _run_command_no_raise(
            [
                str(runtime.python_executable),
                "-m",
                "lex",
                "rollback_migration_state",
                "--input",
                options.rollback_state_file,
            ],
            cwd=runtime.v2_root,
            env=env,
        )
        if rc != 0:
            print("⚠ Rollback command failed. Manual intervention is required.")
    else:
        print(
            "⚠ Rollback requested, but state file not found: "
            f"{options.rollback_state_file}"
        )


def run_full_migration_workflow(options: WorkflowOptions) -> None:
    if options.chunk_size <= 0:
        raise WorkflowUsageError("❌ --chunk-size must be a positive integer")

    runtime = _resolve_runtime(options)
    migration_timestamp = options.migration_timestamp or "AUTO"
    use_existing_v2_migrations = options.v1_source is None

    print("========================================================")
    print("🚀 Starting Full End-to-End Migration Workflow")
    print("========================================================")
    if use_existing_v2_migrations:
        print(
            "V1 Source: <not provided> "
            f"(using existing {runtime.v2_migrations_dir})"
        )
    else:
        print(f"V1 Source: {options.v1_source}")
    print(f"V2 Target: {runtime.v2_migrations_dir}")
    print(f"DB Name:   {options.db_name}")
    print(f"App Name:  {runtime.app_name}")
    print(f"Timestamp: {migration_timestamp}")
    print(f"ChunkSize: {options.chunk_size}")
    print(f"DryBackfill: {options.dry_run_backfill}")
    print(f"SanitizeGeneratedMigrations: {options.enable_sanitization}")
    print(f"BackfillOnly: {options.backfill_only}")
    print(f"PreCleanJsons: {options.pre_clean_jsons}")
    print(f"RollbackOnFailure: {options.rollback_on_failure}")
    print(f"RollbackOnly: {options.rollback_only}")
    print(f"RollbackStateFile: {options.rollback_state_file}")
    print(f"SkipAuditLogBackfill: {options.skip_auditlog_backfill}")

    env = os.environ.copy()
    env["DATABASE_DEPLOYMENT_TARGET"] = "default"
    env["DB_NAME"] = options.db_name
    env["PROJECT_ROOT"] = str(runtime.v2_root)

    if options.pre_clean_jsons:
        _cleanup_json_artifacts(runtime, options)

    if options.rollback_only:
        print("↩ Rollback-only mode enabled.")
        _run_lex(
            runtime,
            env,
            "rollback_migration_state",
            "--input",
            options.rollback_state_file,
        )
        print("✅ Rollback-only mode complete.")
        return

    if not options.backfill_only:
        print("🧷 Capturing migration rollback state...")
        _run_lex(
            runtime,
            env,
            "capture_migration_state",
            "--output",
            options.rollback_state_file,
        )

    try:
        if options.backfill_only:
            print("⚙ Running in backfill-only mode (schema/migration steps skipped).")
            _run_backfill_suite(options, runtime, env)
            _emit_summary(
                {
                    "backfill_only": True,
                    "rollback_state_file": options.rollback_state_file,
                    "migration_timestamp": migration_timestamp,
                    "chunk_size": options.chunk_size,
                    "dry_run_backfill": options.dry_run_backfill,
                    "skip_auditlog_backfill": options.skip_auditlog_backfill,
                }
            )
            print("========================================================")
            print("✅ Backfill-Only Workflow Complete!")
            print("========================================================")
            return

        _print_step("🧾 Step 1: Capturing Pre-Migration DB Table Snapshot...")
        _run_lex(runtime, env, "capture_db_tables", "--output", TABLE_SNAPSHOT_FILE)

        _print_step("📂 Step 2: Preparing V1 Migration Files...")
        _prepare_v1_migration_files(options, runtime)

        _print_step("🛠️  Step 3: Fixing V1 Migration Imports...")
        _run_python_script(
            runtime,
            env,
            runtime.script_dir / "fix_v1_migration.py",
            str(runtime.v2_migrations_dir),
        )

        _print_step("🧊 Step 4: Generating Pre-Migrate Freeze Manifest...")
        if options.enable_sanitization:
            _run_lex(
                runtime,
                env,
                "generate_legacy_freeze_manifest",
                "--before",
                TABLE_SNAPSHOT_FILE,
                "--output",
                PRE_FREEZE_MANIFEST_FILE,
            )
        else:
            print(
                "ℹ️  Sanitization disabled (default); pre-migrate manifest generation skipped."
            )

        _print_step(f"📦 Step 5: Running makemigrations for {runtime.app_name}...")
        pre_makemigration_files = []
        if options.enable_sanitization:
            pre_makemigration_files = _migration_file_names(runtime.v2_migrations_dir)
        _run_lex(runtime, env, "makemigrations", runtime.app_name)

        _print_step("🛡️  Step 6: Sanitizing Generated Migrations...")
        if options.enable_sanitization:
            post_makemigration_files = _migration_file_names(runtime.v2_migrations_dir)
            pre_set = set(pre_makemigration_files)
            new_migration_files = [
                file_name
                for file_name in post_makemigration_files
                if file_name not in pre_set
            ]
            if new_migration_files:
                _run_python_script(
                    runtime,
                    env,
                    runtime.script_dir / "sanitize_v2_migrations.py",
                    "--migrations-dir",
                    str(runtime.v2_migrations_dir),
                    "--manifest",
                    PRE_FREEZE_MANIFEST_FILE,
                    "--app-name",
                    runtime.app_name,
                    "--only-files",
                    *new_migration_files,
                )
            else:
                print(
                    "ℹ️  No newly generated migration files detected; sanitize step skipped."
                )
        else:
            print(
                "ℹ️  Sanitization disabled (default); generated migrations left unchanged."
            )

        _print_step("🔄 Step 7: Applying Schema Migrations...")
        _run_lex(runtime, env, "migrate")

        _print_step("🧊 Step 8: Generating Legacy Freeze Manifest...")
        _run_lex(
            runtime,
            env,
            "generate_legacy_freeze_manifest",
            "--before",
            TABLE_SNAPSHOT_FILE,
            "--output",
            FREEZE_MANIFEST_FILE,
        )

        _run_backfill_suite(options, runtime, env)

        _emit_summary(
            {
                "backfill_only": False,
                "snapshot_file": TABLE_SNAPSHOT_FILE,
                "freeze_manifest_file": FREEZE_MANIFEST_FILE,
                "migration_timestamp": migration_timestamp,
                "chunk_size": options.chunk_size,
                "dry_run_backfill": options.dry_run_backfill,
                "sanitization_enabled": options.enable_sanitization,
                "rollback_on_failure": options.rollback_on_failure,
                "rollback_state_file": options.rollback_state_file,
                "skip_auditlog_backfill": options.skip_auditlog_backfill,
            }
        )
        print("========================================================")
        print("✅ Workflow Complete!")
        print("========================================================")
    except Exception as exc:
        if options.rollback_on_failure:
            exit_code = exc.exit_code if isinstance(exc, WorkflowCommandError) else 1
            _rollback_after_failure(exit_code, options, runtime, env)
        raise


def parse_cli_args(argv: Sequence[str] | None = None) -> WorkflowOptions:
    parser = argparse.ArgumentParser(
        description="Full end-to-end migration workflow (Python version).",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_render_usage("full_migration_workflow.py"),
    )
    parser.add_argument("positionals", nargs="*")
    parser.add_argument("--migration-timestamp", type=str, default=None)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--dry-run-backfill", action="store_true")
    parser.add_argument("--enable-sanitization", action="store_true")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--pre-clean-jsons", action="store_true")
    parser.add_argument("--rollback-on-failure", action="store_true")
    parser.add_argument("--rollback-only", action="store_true")
    parser.add_argument(
        "--rollback-state-file",
        type=str,
        default=".lex_migration_state_before.json",
    )
    parser.add_argument("--skip-auditlog-backfill", action="store_true")

    args = parser.parse_args(argv)
    positional_args = list(args.positionals)
    if len(positional_args) == 3:
        v1_source = Path(positional_args[0]).expanduser().resolve()
        v2_root = Path(positional_args[1]).expanduser().resolve()
        db_name = positional_args[2]
    elif len(positional_args) == 2:
        v1_source = None
        v2_root = Path(positional_args[0]).expanduser().resolve()
        db_name = positional_args[1]
    elif len(positional_args) == 1:
        v1_source = None
        v2_root = Path.cwd().resolve()
        db_name = positional_args[0]
    else:
        raise WorkflowUsageError(_render_usage("full_migration_workflow.py"))

    return WorkflowOptions(
        v2_root=v2_root,
        db_name=db_name,
        v1_source=v1_source,
        migration_timestamp=args.migration_timestamp,
        chunk_size=args.chunk_size,
        dry_run_backfill=args.dry_run_backfill,
        enable_sanitization=args.enable_sanitization,
        backfill_only=args.backfill_only,
        pre_clean_jsons=args.pre_clean_jsons,
        rollback_on_failure=args.rollback_on_failure,
        rollback_only=args.rollback_only,
        rollback_state_file=args.rollback_state_file,
        skip_auditlog_backfill=args.skip_auditlog_backfill,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = parse_cli_args(argv)
        run_full_migration_workflow(options)
        return 0
    except WorkflowCommandError as exc:
        return exc.exit_code
    except WorkflowUsageError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"❌ Unexpected workflow failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
