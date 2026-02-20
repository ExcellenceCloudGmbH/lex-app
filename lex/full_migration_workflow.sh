#!/bin/bash
set -e

# Usage:
#   ./lex/full_migration_workflow.sh <V1_MIGRATIONS_SOURCE> <V2_PROJECT_ROOT> <DB_NAME> \
#     [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] \
#     [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] \
#     [--rollback-state-file <PATH>] [--skip-auditlog-backfill]
#   ./lex/full_migration_workflow.sh <V2_PROJECT_ROOT> <DB_NAME> \
#     [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] \
#     [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] \
#     [--rollback-state-file <PATH>] [--skip-auditlog-backfill]
#   ./lex/full_migration_workflow.sh <DB_NAME> \
#     [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] \
#     [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] \
#     [--rollback-state-file <PATH>] [--skip-auditlog-backfill]
#
# Example:
#   ./lex/full_migration_workflow.sh /tmp/v1_migrations /home/syscall/LUND_IT/ArmiraCashflowDB db_armiracashflowdb \
#     --migration-timestamp "2026-02-18T12:00:00Z" --chunk-size 500
#   ./lex/full_migration_workflow.sh /home/syscall/LUND_IT/ArmiraCashflowDB db_armiracashflowdb \
#     --migration-timestamp "2026-02-18T12:00:00Z" --chunk-size 500
#   ./lex/full_migration_workflow.sh db_armiracashflowdb \
#     --migration-timestamp "2026-02-18T12:00:00Z" --chunk-size 500

MIGRATION_TIMESTAMP=""
CHUNK_SIZE=500
DRY_RUN_BACKFILL=false
ENABLE_SANITIZATION=false
BACKFILL_ONLY=false
PRE_CLEAN_JSONS=false
ROLLBACK_ON_FAILURE=false
ROLLBACK_ONLY=false
ROLLBACK_STATE_FILE=".lex_migration_state_before.json"
SKIP_AUDITLOG_BACKFILL=false
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --migration-timestamp)
            if [ -z "$2" ]; then
                echo "❌ --migration-timestamp requires a value"
                exit 1
            fi
            MIGRATION_TIMESTAMP="$2"
            shift 2
            ;;
        --chunk-size)
            if [ -z "$2" ]; then
                echo "❌ --chunk-size requires a value"
                exit 1
            fi
            CHUNK_SIZE="$2"
            shift 2
            ;;
        --dry-run-backfill)
            DRY_RUN_BACKFILL=true
            shift 1
            ;;
        --enable-sanitization)
            ENABLE_SANITIZATION=true
            shift 1
            ;;
        --backfill-only)
            BACKFILL_ONLY=true
            shift 1
            ;;
        --pre-clean-jsons)
            PRE_CLEAN_JSONS=true
            shift 1
            ;;
        --rollback-on-failure)
            ROLLBACK_ON_FAILURE=true
            shift 1
            ;;
        --rollback-only)
            ROLLBACK_ONLY=true
            shift 1
            ;;
        --rollback-state-file)
            if [ -z "$2" ]; then
                echo "❌ --rollback-state-file requires a value"
                exit 1
            fi
            ROLLBACK_STATE_FILE="$2"
            shift 2
            ;;
        --skip-auditlog-backfill)
            SKIP_AUDITLOG_BACKFILL=true
            shift 1
            ;;
        -*)
            echo "❌ Unknown option: $1"
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift 1
            ;;
    esac
done

if ! [[ "$CHUNK_SIZE" =~ ^[0-9]+$ ]] || [ "$CHUNK_SIZE" -le 0 ]; then
    echo "❌ --chunk-size must be a positive integer"
    exit 1
fi

V1_SOURCE=""
USE_EXISTING_V2_MIGRATIONS=false

if [ "${#POSITIONAL_ARGS[@]}" -eq 3 ]; then
    V1_SOURCE="${POSITIONAL_ARGS[0]}"
    V2_ROOT="${POSITIONAL_ARGS[1]}"
    DB_NAME_ARG="${POSITIONAL_ARGS[2]}"
elif [ "${#POSITIONAL_ARGS[@]}" -eq 2 ]; then
    V2_ROOT="${POSITIONAL_ARGS[0]}"
    DB_NAME_ARG="${POSITIONAL_ARGS[1]}"
    USE_EXISTING_V2_MIGRATIONS=true
elif [ "${#POSITIONAL_ARGS[@]}" -eq 1 ]; then
    V2_ROOT="$(pwd)"
    DB_NAME_ARG="${POSITIONAL_ARGS[0]}"
    USE_EXISTING_V2_MIGRATIONS=true
else
    echo "Usage:"
    echo "  $0 <V1_MIGRATIONS_SOURCE> <V2_PROJECT_ROOT> <DB_NAME> [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] [--rollback-state-file <PATH>] [--skip-auditlog-backfill]"
    echo "  $0 <V2_PROJECT_ROOT> <DB_NAME> [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] [--rollback-state-file <PATH>] [--skip-auditlog-backfill]"
    echo "  $0 <DB_NAME> [--migration-timestamp <ISO8601>] [--chunk-size <INT>] [--dry-run-backfill] [--enable-sanitization] [--backfill-only] [--pre-clean-jsons] [--rollback-on-failure] [--rollback-only] [--rollback-state-file <PATH>] [--skip-auditlog-backfill]"
    exit 1
fi

# Locate the directory where this script and helper python scripts reside
SCRIPT_DIR=$(dirname "$(realpath "$0")")

APP_NAME=$(basename "$V2_ROOT")
V2_MIGRATIONS_DIR="$V2_ROOT/migrations"
TABLE_SNAPSHOT_FILE=".lex_tables_before.json"
PRE_FREEZE_MANIFEST_FILE=".lex_legacy_freeze_manifest.pre_migrate.json"
FREEZE_MANIFEST_FILE=".lex_legacy_freeze_manifest.json"
TABLE_SNAPSHOT_AFTER_FILE=".lex_tables_after.json"

cleanup_json_artifacts() {
    echo "🧹 Pre-cleaning JSON artifacts..."
    rm -f "$TABLE_SNAPSHOT_FILE" \
          "$TABLE_SNAPSHOT_AFTER_FILE" \
          "$PRE_FREEZE_MANIFEST_FILE" \
          "$FREEZE_MANIFEST_FILE" \
          "$ROLLBACK_STATE_FILE"
}

run_backfill_suite() {
    echo "--------------------------------------------------------"
    echo "🔧 Step 9.1: Normalizing is_calculated Values..."
    echo "--------------------------------------------------------"
    NORMALIZE_CMD=(lex normalize_is_calculated --chunk-size "$CHUNK_SIZE")
    if [ "$DRY_RUN_BACKFILL" = true ]; then
        NORMALIZE_CMD+=(--dry-run)
    fi
    "${NORMALIZE_CMD[@]}"

    echo "--------------------------------------------------------"
    echo "⏳ Step 9.2: Backfilling Bitemporal History..."
    echo "--------------------------------------------------------"
    BACKFILL_CMD=(lex backfill_bitemporal_history --chunk-size "$CHUNK_SIZE" --reason "V1 migration snapshot")
    if [ -n "$MIGRATION_TIMESTAMP" ]; then
        BACKFILL_CMD+=(--timestamp "$MIGRATION_TIMESTAMP")
    fi
    if [ "$DRY_RUN_BACKFILL" = true ]; then
        BACKFILL_CMD+=(--dry-run)
    fi
    "${BACKFILL_CMD[@]}"

    if [ "$SKIP_AUDITLOG_BACKFILL" = true ]; then
        echo "ℹ️  Audit-log backfill skipped by flag."
        return
    fi

    echo "--------------------------------------------------------"
    echo "🧾 Step 9.3: Backfilling Audit Logs..."
    echo "--------------------------------------------------------"
    AUDIT_BACKFILL_CMD=(lex backfill_audit_logging --chunk-size "$CHUNK_SIZE" --reason "V1 audit log migration snapshot")
    if [ "$DRY_RUN_BACKFILL" = true ]; then
        AUDIT_BACKFILL_CMD+=(--dry-run)
    fi
    "${AUDIT_BACKFILL_CMD[@]}"
}

rollback_on_error() {
    local exit_code=$?
    trap - ERR
    echo "❌ Workflow failed (exit=$exit_code)."
    if [ "$ROLLBACK_ON_FAILURE" = true ]; then
        if [ -f "$ROLLBACK_STATE_FILE" ]; then
            echo "↩ Attempting rollback to captured migration state: $ROLLBACK_STATE_FILE"
            if ! lex rollback_migration_state --input "$ROLLBACK_STATE_FILE"; then
                echo "⚠ Rollback command failed. Manual intervention is required."
            fi
        else
            echo "⚠ Rollback requested, but state file not found: $ROLLBACK_STATE_FILE"
        fi
    fi
    exit "$exit_code"
}

echo "========================================================"
echo "🚀 Starting Full End-to-End Migration Workflow"
echo "========================================================"
if [ "$USE_EXISTING_V2_MIGRATIONS" = true ]; then
    echo "V1 Source: <not provided> (using existing $V2_MIGRATIONS_DIR)"
else
    echo "V1 Source: $V1_SOURCE"
fi
echo "V2 Target: $V2_MIGRATIONS_DIR"
echo "DB Name:   $DB_NAME_ARG"
echo "App Name:  $APP_NAME"
echo "Timestamp: ${MIGRATION_TIMESTAMP:-AUTO}"
echo "ChunkSize: $CHUNK_SIZE"
echo "DryBackfill: $DRY_RUN_BACKFILL"
echo "SanitizeGeneratedMigrations: $ENABLE_SANITIZATION"
echo "BackfillOnly: $BACKFILL_ONLY"
echo "PreCleanJsons: $PRE_CLEAN_JSONS"
echo "RollbackOnFailure: $ROLLBACK_ON_FAILURE"
echo "RollbackOnly: $ROLLBACK_ONLY"
echo "RollbackStateFile: $ROLLBACK_STATE_FILE"
echo "SkipAuditLogBackfill: $SKIP_AUDITLOG_BACKFILL"

# 0. Environment Setup
if [ -d "$V2_ROOT/.venv" ]; then
    source "$V2_ROOT/.venv/bin/activate"
elif [ -d "$V2_ROOT/venv" ]; then
    source "$V2_ROOT/venv/bin/activate"
else
    echo "❌ Error: Virtual environment (.venv or venv) not found in $V2_ROOT"
    exit 1
fi

# Switch to V2 Root for Django context
cd "$V2_ROOT" || exit 1

export DATABASE_DEPLOYMENT_TARGET=default
export DB_NAME="$DB_NAME_ARG"

if [ "$PRE_CLEAN_JSONS" = true ]; then
    cleanup_json_artifacts
fi

if [ "$ROLLBACK_ONLY" = true ]; then
    echo "↩ Rollback-only mode enabled."
    lex rollback_migration_state --input "$ROLLBACK_STATE_FILE"
    echo "✅ Rollback-only mode complete."
    exit 0
fi

if [ "$BACKFILL_ONLY" = false ]; then
    echo "🧷 Capturing migration rollback state..."
    lex capture_migration_state --output "$ROLLBACK_STATE_FILE"
fi

if [ "$ROLLBACK_ON_FAILURE" = true ]; then
    trap rollback_on_error ERR
fi

if [ "$BACKFILL_ONLY" = true ]; then
    echo "⚙ Running in backfill-only mode (schema/migration steps skipped)."
    run_backfill_suite
    echo "MIGRATION_WORKFLOW_SUMMARY_START"
    cat <<EOF
{"backfill_only":true,"rollback_state_file":"$ROLLBACK_STATE_FILE","migration_timestamp":"${MIGRATION_TIMESTAMP:-AUTO}","chunk_size":$CHUNK_SIZE,"dry_run_backfill":$DRY_RUN_BACKFILL,"skip_auditlog_backfill":$SKIP_AUDITLOG_BACKFILL}
EOF
    echo "MIGRATION_WORKFLOW_SUMMARY_END"
    echo "========================================================"
    echo "✅ Backfill-Only Workflow Complete!"
    echo "========================================================"
    exit 0
fi

# 1. Pre-migration DB table snapshot
echo "--------------------------------------------------------"
echo "🧾 Step 1: Capturing Pre-Migration DB Table Snapshot..."
echo "--------------------------------------------------------"
lex capture_db_tables --output "$TABLE_SNAPSHOT_FILE"

# 2. Copy V1 Files
echo "--------------------------------------------------------"
echo "📂 Step 2: Preparing V1 Migration Files..."
echo "--------------------------------------------------------"

mkdir -p "$V2_MIGRATIONS_DIR"

if [ "$USE_EXISTING_V2_MIGRATIONS" = true ]; then
    V1_MIGRATIONS_SOURCE="$V2_MIGRATIONS_DIR"
    count=$(find "$V1_MIGRATIONS_SOURCE" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" | wc -l)
    if [ "$count" -eq 0 ]; then
        echo "❌ Error: No migration files found in existing V2 migrations directory: $V1_MIGRATIONS_SOURCE"
        exit 1
    fi
    echo "✅ Using $count existing migration files from $V1_MIGRATIONS_SOURCE"
else
    # Smart detection:
    # 1. path/to/v1/migrations
    # 2. path/to/v1/*/migrations (e.g. Project/App/migrations)
    # 3. path/to/v1 (Direct)
    if [ -d "$V1_SOURCE/migrations" ]; then
        echo "ℹ️  Found 'migrations' subdirectory in V1 source."
        V1_MIGRATIONS_SOURCE="$V1_SOURCE/migrations"
    elif compgen -G "$V1_SOURCE/*/migrations" > /dev/null; then
        # Grab the first match if multiple exist (unlikely for single app migration context, but safe)
        MATCH=$(ls -d "$V1_SOURCE"/*/migrations | head -n 1)
        echo "ℹ️  Found nested migrations directory: $MATCH"
        V1_MIGRATIONS_SOURCE="$MATCH"
    else
        V1_MIGRATIONS_SOURCE="$V1_SOURCE"
    fi

    if [ ! -d "$V1_MIGRATIONS_SOURCE" ]; then
        echo "❌ Error: V1 migrations directory $V1_MIGRATIONS_SOURCE does not exist."
        exit 1
    fi

    # Check if there are any .py files
    count=$(ls -1 "$V1_MIGRATIONS_SOURCE"/*.py 2>/dev/null | wc -l)
    if [ "$count" -eq 0 ]; then
        echo "❌ Error: No .py migration files found in $V1_MIGRATIONS_SOURCE"
        exit 1
    fi

    # Remove stale generated migrations from prior runs (keep __init__.py only).
    find "$V2_MIGRATIONS_DIR" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" -delete

    # Copy all .py files
    cp "$V1_MIGRATIONS_SOURCE"/*.py "$V2_MIGRATIONS_DIR/"
    echo "✅ Copied $count migration files from $V1_MIGRATIONS_SOURCE"
fi

# 3. Fix Imports
echo "--------------------------------------------------------"
echo "🛠️  Step 3: Fixing V1 Migration Imports..."
echo "--------------------------------------------------------"
# Pass the V2 migrations directory to the python script
python "$SCRIPT_DIR/fix_v1_migration.py" "$V2_MIGRATIONS_DIR"

# 4. Build pre-migrate freeze manifest (used to preserve V1-only tables)
echo "--------------------------------------------------------"
echo "🧊 Step 4: Generating Pre-Migrate Freeze Manifest..."
echo "--------------------------------------------------------"
if [ "$ENABLE_SANITIZATION" = true ]; then
    lex generate_legacy_freeze_manifest --before "$TABLE_SNAPSHOT_FILE" --output "$PRE_FREEZE_MANIFEST_FILE"
else
    echo "ℹ️  Sanitization disabled (default); pre-migrate manifest generation skipped."
fi

# 5. Generate New Migrations (V2 Differences)
echo "--------------------------------------------------------"
echo "📦 Step 5: Running makemigrations for $APP_NAME..."
echo "--------------------------------------------------------"
if [ "$ENABLE_SANITIZATION" = true ]; then
    mapfile -t PRE_MAKEMIGRATION_FILES < <(find "$V2_MIGRATIONS_DIR" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" -printf "%f\n" | sort)
fi
lex makemigrations $APP_NAME

# 6. Sanitize generated migrations so V1-only tables are preserved for freeze
echo "--------------------------------------------------------"
echo "🛡️  Step 6: Sanitizing Generated Migrations..."
echo "--------------------------------------------------------"
if [ "$ENABLE_SANITIZATION" = true ]; then
    mapfile -t POST_MAKEMIGRATION_FILES < <(find "$V2_MIGRATIONS_DIR" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" -printf "%f\n" | sort)
    NEW_MIGRATION_FILES=()
    for file in "${POST_MAKEMIGRATION_FILES[@]}"; do
        is_preexisting=false
        for pre_file in "${PRE_MAKEMIGRATION_FILES[@]}"; do
            if [ "$file" = "$pre_file" ]; then
                is_preexisting=true
                break
            fi
        done
        if [ "$is_preexisting" = false ]; then
            NEW_MIGRATION_FILES+=("$file")
        fi
    done

    if [ "${#NEW_MIGRATION_FILES[@]}" -gt 0 ]; then
        python "$SCRIPT_DIR/sanitize_v2_migrations.py" \
          --migrations-dir "$V2_MIGRATIONS_DIR" \
          --manifest "$PRE_FREEZE_MANIFEST_FILE" \
          --app-name "$APP_NAME" \
          --only-files "${NEW_MIGRATION_FILES[@]}"
    else
        echo "ℹ️  No newly generated migration files detected; sanitize step skipped."
    fi
else
    echo "ℹ️  Sanitization disabled (default); generated migrations left unchanged."
fi

# 7. Apply Migrations (Schema)
echo "--------------------------------------------------------"
echo "🔄 Step 7: Applying Schema Migrations..."
echo "--------------------------------------------------------"
# Apply all pending migrations across all installed apps.
lex migrate

# 8. Generate dynamic legacy-freeze manifest
echo "--------------------------------------------------------"
echo "🧊 Step 8: Generating Legacy Freeze Manifest..."
echo "--------------------------------------------------------"
lex generate_legacy_freeze_manifest --before "$TABLE_SNAPSHOT_FILE" --output "$FREEZE_MANIFEST_FILE"

# 9. Initialize History + Audit Logs
run_backfill_suite

echo "MIGRATION_WORKFLOW_SUMMARY_START"
cat <<EOF
{"backfill_only":false,"snapshot_file":"$TABLE_SNAPSHOT_FILE","freeze_manifest_file":"$FREEZE_MANIFEST_FILE","migration_timestamp":"${MIGRATION_TIMESTAMP:-AUTO}","chunk_size":$CHUNK_SIZE,"dry_run_backfill":$DRY_RUN_BACKFILL,"sanitization_enabled":$ENABLE_SANITIZATION,"rollback_on_failure":$ROLLBACK_ON_FAILURE,"rollback_state_file":"$ROLLBACK_STATE_FILE","skip_auditlog_backfill":$SKIP_AUDITLOG_BACKFILL}
EOF
echo "MIGRATION_WORKFLOW_SUMMARY_END"

echo "========================================================"
echo "✅ Workflow Complete!"
echo "========================================================"
