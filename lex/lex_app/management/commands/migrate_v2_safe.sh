#!/bin/bash
set -e

# migrate_v2_safe.sh - Safer Migration Strategy (Dump All -> Recreate -> Restore)
# Usage: ./migrate_v2_safe.sh

if [ ! -f .env ]; then
    echo "Error: .env file not found. Please run this script from the project root."
    exit 1
fi

echo "=================================================="
echo "Starting Safe Lex App V2 Migration"
echo "=================================================="

DUMP_FILE="legacy_v1_full_dump.json"

# 1. Full Dump
echo ""
echo "[Step 1/5] Dumping Full Legacy Database..."
python3 skills/lex/lex-app/scripts/dump_legacy_db.py

if [ ! -s "$DUMP_FILE" ]; then
    echo "❌ Error: Dump file '$DUMP_FILE' is missing or empty."
    exit 1
fi

# 2. Recreate Database (Drop & Create)
echo ""
echo "[Step 2/5] Recreating Database (Fresh Start)..."
python3 skills/lex/lex-app/scripts/recreate_db.py

# 3. Init Schema (V2)
echo ""
echo "[Step 3/5] Running Lex Init (V2 Schema)..."
lex Init

# 4. Restore Data (Transform & Import)
echo ""
echo "[Step 4/5] Restoring Data from Dump..."
python3 skills/lex/lex-app/scripts/restore_from_dump.py

# 5. History Init
echo ""
echo "[Step 5/5] Initializing History..."
python3 -m lex init_history

echo ""
echo "=================================================="
echo "✅ Safe Migration Completed!"
echo "=================================================="
