#!/usr/bin/env python3
"""
Extract Legacy Logs BEFORE Running V2 Migrations

CRITICAL: Run this BEFORE `lex init` on V2 codebase!
Django migrations will DROP generic_app_* tables since V2 doesn't have that app.

Usage:
    python scripts/extract_legacy_logs.py

Output:
    legacy_logs_backup.json (in current directory)
"""
import json
import os
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'settings'))

import django
django.setup()

from django.db import connection

OUTPUT_FILE = "legacy_logs_backup.json"

def extract_legacy_logs():
    print("=" * 60)
    print("📦 Extracting Legacy Logs (BEFORE V2 Migration)")
    print("=" * 60)
    
    data = {
        "calculation_logs": [],
        "user_change_logs": [],
        "calculation_ids": [],
    }
    
    with connection.cursor() as cursor:
        # Check if legacy tables exist
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name LIKE 'generic_app_%'
        """)
        tables = [r[0] for r in cursor.fetchall()]
        
        if not tables:
            print("⚠️ No generic_app_* tables found!")
            print("   Either already migrated or V1 code never created them.")
            return False
        
        print(f"Found legacy tables: {tables}")
        
        # Extract calculation logs
        if 'generic_app_calculationlog' in tables:
            try:
                cursor.execute('SELECT "id", "timestamp", "message", "calculationId" FROM "generic_app_calculationlog"')
                for row in cursor.fetchall():
                    data["calculation_logs"].append({
                        "id": row[0],
                        "timestamp": row[1].isoformat() if row[1] else None,
                        "message": row[2],
                        "calculationId": row[3],
                    })
                print(f"✅ Extracted {len(data['calculation_logs'])} calculation logs")
            except Exception as e:
                print(f"❌ Failed to extract calculation logs: {e}")
        
        # Extract user change logs
        if 'generic_app_userchangelog' in tables:
            try:
                # Get actual columns first
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'generic_app_userchangelog'
                """)
                cols = [r[0] for r in cursor.fetchall()]
                
                # Build query based on available columns
                select_cols = []
                if 'user_name' in cols:
                    select_cols.append('"user_name"')
                elif 'user' in cols:
                    select_cols.append('"user"')
                else:
                    select_cols.append('NULL')
                
                cursor.execute(f'''
                    SELECT "id", "timestamp", {select_cols[0]}, "message", "calculationId"
                    FROM "generic_app_userchangelog"
                ''')
                for row in cursor.fetchall():
                    data["user_change_logs"].append({
                        "id": row[0],
                        "timestamp": row[1].isoformat() if row[1] else None,
                        "user": row[2],
                        "message": row[3],
                        "calculationId": row[4],
                    })
                print(f"✅ Extracted {len(data['user_change_logs'])} user change logs")
            except Exception as e:
                print(f"❌ Failed to extract user change logs: {e}")
        
        # Extract calculation IDs mapping (for semantic linking)
        if 'generic_app_calculationids' in tables:
            try:
                cursor.execute('SELECT "calculation_id", "calculation_record" FROM "generic_app_calculationids"')
                for row in cursor.fetchall():
                    data["calculation_ids"].append({
                        "calculation_id": row[0],
                        "calculation_record": row[1],
                    })
                print(f"✅ Extracted {len(data['calculation_ids'])} calculation ID mappings")
            except Exception as e:
                print(f"❌ Failed to extract calculation IDs: {e}")
    
    # Write to file
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    
    print(f"\n💾 Saved to: {OUTPUT_FILE}")
    print("=" * 60)
    print("✅ Extraction Complete!")
    print("   Now safe to run: lex init")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    success = extract_legacy_logs()
    sys.exit(0 if success else 1)
