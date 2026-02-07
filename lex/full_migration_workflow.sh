#!/bin/bash
set -e

# Usage: ./lex/full_migration_workflow.sh <V1_MIGRATIONS_SOURCE> <V2_PROJECT_ROOT> <DB_NAME>
# Example: ./lex/full_migration_workflow.sh /tmp/v1_migrations /home/syscall/LUND_IT/ArmiraCashflowDB db_armiracashflowdb

V1_SOURCE=$1
V2_ROOT=$2
DB_NAME_ARG=$3

if [ -z "$V1_SOURCE" ] || [ -z "$V2_ROOT" ] || [ -z "$DB_NAME_ARG" ]; then
    echo "Usage: $0 <V1_MIGRATIONS_SOURCE> <V2_PROJECT_ROOT> <DB_NAME>"
    exit 1
fi

# Locate the directory where this script and helper python scripts reside
SCRIPT_DIR=$(dirname "$(realpath "$0")")

APP_NAME=$(basename "$V2_ROOT")
V2_MIGRATIONS_DIR="$V2_ROOT/migrations"

echo "========================================================"
echo "🚀 Starting Full End-to-End Migration Workflow"
echo "========================================================"
echo "V1 Source: $V1_SOURCE"
echo "V2 Target: $V2_MIGRATIONS_DIR"
echo "DB Name:   $DB_NAME_ARG"
echo "App Name:  $APP_NAME"

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

# 1. Copy V1 Files
echo "--------------------------------------------------------"
echo "📂 Step 1: Copying V1 Migration Files..."
echo "--------------------------------------------------------"

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

mkdir -p "$V2_MIGRATIONS_DIR"
# Copy all .py files
cp "$V1_MIGRATIONS_SOURCE"/*.py "$V2_MIGRATIONS_DIR/"
echo "✅ Copied $count migration files from $V1_MIGRATIONS_SOURCE"

# 2. Fix Imports
echo "--------------------------------------------------------"
echo "🛠️  Step 2: Fixing V1 Migration Imports..."
echo "--------------------------------------------------------"
# Pass the V2 migrations directory to the python script
python "$SCRIPT_DIR/fix_v1_migration.py" "$V2_MIGRATIONS_DIR"

# 3. Generate New Migrations (V2 Differences)
echo "--------------------------------------------------------"
echo "📦 Step 3: Running makemigrations for $APP_NAME..."
echo "--------------------------------------------------------"
# Ensure we are in the src root for manage.py/lex execution context? 
# "lex" wrapper usually handles cwd.
cwd=$(pwd)
# We need to run lex/manage.py from the correct location. 
# Usually ./src/lex-app is where manage.py might be, or lex wrapper does it.
# Assuming 'lex' command works from anywhere or we CD to V2 root?
# The user state has cwd /home/syscall/LUND_IT/ArmiraCashflowDB/.venv/src/lex-app so 'lex' works.

lex makemigrations $APP_NAME

# 4. Apply Migrations (Schema)
echo "--------------------------------------------------------"
echo "🔄 Step 4: Applying Schema Migrations..."
echo "--------------------------------------------------------"
lex migrate $APP_NAME
lex migrate audit_logging
lex migrate authentication
lex migrate oauth2_authcodeflow

# 5. Initialize History (Backfill)
echo "--------------------------------------------------------"
echo "⏳ Step 5: Backfilling Bitemporal History..."
echo "--------------------------------------------------------"
# backfill script auto-detects DB from env
python "$SCRIPT_DIR/backfill_history_sql.py"

echo "========================================================"
echo "✅ Workflow Complete!"
echo "========================================================"
