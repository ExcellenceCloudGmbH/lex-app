#!/usr/bin/env python3
"""
Restore Legacy Logs AFTER Running V2 Migrations

Run this AFTER `lex init` to restore extracted logs into audit_logging_* tables.

Usage:
    python scripts/restore_legacy_logs.py

Reads:
    legacy_logs_backup.json (created by extract_legacy_logs.py)
"""
import json
import os
import sys
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'settings'))

import django
django.setup()

from django.db import connection
from django.contrib.contenttypes.models import ContentType

INPUT_FILE = "legacy_logs_backup.json"

def restore_legacy_logs():
    print("=" * 60)
    print("📥 Restoring Legacy Logs to V2 Tables")
    print("=" * 60)
    
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} not found!")
        print("   Run extract_legacy_logs.py first (before lex init)")
        return False
    
    with open(INPUT_FILE, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded: {len(data.get('calculation_logs', []))} calc logs, "
          f"{len(data.get('user_change_logs', []))} change logs, "
          f"{len(data.get('calculation_ids', []))} ID mappings")
    
    with connection.cursor() as cursor:
        # Check V2 tables exist
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'audit_logging_calculationlog'
        """)
        if not cursor.fetchone():
            print("❌ audit_logging_calculationlog table not found!")
            print("   Run `lex init` first to create V2 schema.")
            return False
        
        # Check if already restored
        cursor.execute('SELECT COUNT(*) FROM "audit_logging_calculationlog"')
        existing = cursor.fetchone()[0]
        if existing > 0:
            print(f"⚠️ audit_logging_calculationlog already has {existing} rows.")
            response = input("   Overwrite? (y/N): ").strip().lower()
            if response != 'y':
                print("   Skipping calculation logs.")
                data['calculation_logs'] = []
        
        # Build content type mapping from calculation_ids
        calc_id_map = {}
        for mapping in data.get('calculation_ids', []):
            calc_id = mapping.get('calculation_id')
            record_str = mapping.get('calculation_record')
            if calc_id and record_str:
                parts = record_str.rsplit('_', 1)
                if len(parts) == 2:
                    model_name = parts[0].lower()
                    try:
                        obj_id = int(parts[1])
                        ct = ContentType.objects.filter(model=model_name).first()
                        if ct:
                            calc_id_map[calc_id] = (ct.id, obj_id)
                    except (ValueError, TypeError):
                        pass
        
        print(f"📊 Built semantic map for {len(calc_id_map)} contexts")
        
        # Restore calculation logs
        if data.get('calculation_logs'):
            linked = 0
            for log in data['calculation_logs']:
                timestamp = log.get('timestamp')
                if timestamp and isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp)
                    except:
                        timestamp = datetime.now()
                
                calc_id = log.get('calculationId')
                content_type_id = None
                object_id = None
                if calc_id and calc_id in calc_id_map:
                    content_type_id, object_id = calc_id_map[calc_id]
                    linked += 1
                
                cursor.execute('''
                    INSERT INTO "audit_logging_calculationlog" 
                    ("timestamp", "calculation_log", "calculationId", "content_type_id", "object_id", "parent_log_id", "audit_log_id")
                    VALUES (%s, %s, %s, %s, %s, NULL, NULL)
                ''', [timestamp, log.get('message', ''), calc_id or 'legacy', content_type_id, object_id])
            
            connection.connection.commit()
            print(f"✅ Restored {len(data['calculation_logs'])} calculation logs ({linked} semantically linked)")
        
        # Restore user change logs
        if data.get('user_change_logs'):
            cursor.execute('SELECT COUNT(*) FROM "audit_logging_auditlog"')
            existing_audit = cursor.fetchone()[0]
            if existing_audit > 0:
                print(f"⚠️ audit_logging_auditlog already has {existing_audit} rows. Skipping.")
            else:
                for log in data['user_change_logs']:
                    timestamp = log.get('timestamp')
                    if timestamp and isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp)
                        except:
                            timestamp = datetime.now()
                    
                    cursor.execute('''
                        INSERT INTO "audit_logging_auditlog" 
                        ("date", "author", "resource", "action", "payload", "calculation_id")
                        VALUES (%s, %s, %s, %s, %s, %s)
                    ''', [
                        timestamp,
                        str(log.get('user', 'Unknown')),
                        'Legacy',
                        'update',
                        log.get('message', '{}'),
                        log.get('calculationId')
                    ])
                
                connection.connection.commit()
                print(f"✅ Restored {len(data['user_change_logs'])} user change logs")
        
        # Reset sequences
        cursor.execute("""
            DO $$
            DECLARE seq_name text;
            BEGIN
                seq_name := pg_get_serial_sequence('"audit_logging_calculationlog"', 'id');
                IF seq_name IS NOT NULL THEN
                    EXECUTE format('SELECT setval(%L, COALESCE((SELECT MAX(id) FROM "audit_logging_calculationlog"), 1))', seq_name);
                END IF;
                seq_name := pg_get_serial_sequence('"audit_logging_auditlog"', 'id');
                IF seq_name IS NOT NULL THEN
                    EXECUTE format('SELECT setval(%L, COALESCE((SELECT MAX(id) FROM "audit_logging_auditlog"), 1))', seq_name);
                END IF;
            END $$;
        """)
        connection.connection.commit()
        print("✅ Sequences reset")
    
    print("\n" + "=" * 60)
    print("✅ Restore Complete!")
    print("   Next: python scripts/backfill_history_sql.py")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    success = restore_legacy_logs()
    sys.exit(0 if success else 1)
