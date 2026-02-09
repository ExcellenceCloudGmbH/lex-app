#!/usr/bin/env python3
"""
Lex V1 to V2 In-Place Migration Script

For cloud instances where:
- Database name stays the SAME
- Business tables stay the SAME  
- Only migrate: generic_app_* → audit_logging_*
- Initialize history tables for existing data

Usage:
    cd /path/to/project
    source .venv/bin/activate
    python -m lex migrate_v1_to_v2_inplace
    
Or via the management command:
    lex migrate_v1_to_v2_inplace
"""
import os
import sys

def setup_django():
    """Setup Django environment."""
    # Try to find lex package and add to path
    try:
        import lex
        lex_pkg_path = os.path.dirname(lex.__file__)
        if lex_pkg_path not in sys.path:
            sys.path.append(lex_pkg_path)
    except ImportError:
        pass
    
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    django.setup()

def check_legacy_tables_exist(cursor):
    """Check if V1 legacy tables exist."""
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name LIKE 'generic_app_%'
    """)
    tables = [row[0] for row in cursor.fetchall()]
    return tables

def migrate_calculation_logs(cursor):
    """Migrate generic_app_calculationlog → audit_logging_calculationlog."""
    from django.contrib.contenttypes.models import ContentType
    
    # Check if source table exists
    cursor.execute("SELECT to_regclass('generic_app_calculationlog')")
    if not cursor.fetchone()[0]:
        print("  ⚠️ Source table 'generic_app_calculationlog' not found. Skipping.")
        return 0
    
    # Check if already migrated (target has data)
    cursor.execute('SELECT COUNT(*) FROM "audit_logging_calculationlog"')
    existing_count = cursor.fetchone()[0]
    if existing_count > 0:
        print(f"  ⚠️ Target already has {existing_count} rows. Skipping to avoid duplicates.")
        return existing_count
    
    # Build calculation_id → (content_type_id, object_id) mapping
    calc_id_map = {}
    cursor.execute("SELECT to_regclass('generic_app_calculationids')")
    if cursor.fetchone()[0]:
        cursor.execute('SELECT "calculation_id", "calculation_record" FROM "generic_app_calculationids"')
        for calc_id, record_str in cursor.fetchall():
            if not calc_id or not record_str:
                continue
            try:
                parts = record_str.rsplit('_', 1)
                if len(parts) == 2:
                    model_name = parts[0].lower()
                    obj_id = int(parts[1])
                    try:
                        ct = ContentType.objects.get(model=model_name)
                        calc_id_map[calc_id] = (ct.id, obj_id)
                    except ContentType.DoesNotExist:
                        pass
                    except ContentType.MultipleObjectsReturned:
                        ct = ContentType.objects.filter(model=model_name).exclude(app_label='lex_app').first()
                        if ct:
                            calc_id_map[calc_id] = (ct.id, obj_id)
            except:
                pass
        print(f"  📊 Built semantic map for {len(calc_id_map)} calculation contexts.")
    
    # Get source columns
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'generic_app_calculationlog'
    """)
    source_columns = [row[0] for row in cursor.fetchall()]
    
    # Read source data
    cursor.execute('SELECT * FROM "generic_app_calculationlog"')
    rows = cursor.fetchall()
    
    if not rows:
        print("  ℹ️ No legacy calculation logs to migrate.")
        return 0
    
    # Insert into target
    inserted = 0
    for row in rows:
        row_dict = dict(zip(source_columns, row))
        
        calc_id = row_dict.get('calculationId') or row_dict.get('calculation_id') or 'legacy'
        timestamp = row_dict.get('timestamp') or row_dict.get('created_at')
        message = row_dict.get('message', '')
        
        # Resolve semantic link
        content_type_id = None
        object_id = None
        if calc_id in calc_id_map:
            content_type_id, object_id = calc_id_map[calc_id]
        
        cursor.execute('''
            INSERT INTO "audit_logging_calculationlog" 
            ("timestamp", "calculation_log", "calculationId", "content_type_id", "object_id", "parent_log_id", "audit_log_id")
            VALUES (%s, %s, %s, %s, %s, NULL, NULL)
        ''', [timestamp, message, calc_id, content_type_id, object_id])
        inserted += 1
    
    return inserted

def migrate_user_change_logs(cursor):
    """Migrate generic_app_userchangelog → audit_logging_auditlog."""
    
    # Check if source table exists
    cursor.execute("SELECT to_regclass('generic_app_userchangelog')")
    if not cursor.fetchone()[0]:
        print("  ⚠️ Source table 'generic_app_userchangelog' not found. Skipping.")
        return 0
    
    # Check if already migrated
    cursor.execute('SELECT COUNT(*) FROM "audit_logging_auditlog"')
    existing_count = cursor.fetchone()[0]
    if existing_count > 0:
        print(f"  ⚠️ Target already has {existing_count} rows. Skipping to avoid duplicates.")
        return existing_count
    
    # Get source columns
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'generic_app_userchangelog'
    """)
    source_columns = [row[0] for row in cursor.fetchall()]
    
    # Read source data
    cursor.execute('SELECT * FROM "generic_app_userchangelog"')
    rows = cursor.fetchall()
    
    if not rows:
        print("  ℹ️ No legacy user change logs to migrate.")
        return 0
    
    inserted = 0
    for row in rows:
        row_dict = dict(zip(source_columns, row))
        
        date = row_dict.get('timestamp') or row_dict.get('date')
        author = str(row_dict.get('user', 'Unknown'))
        resource = str(row_dict.get('object_repr') or 'Legacy Object')
        action = row_dict.get('action', 'update')
        payload = row_dict.get('changes') or '{}'
        calc_id = row_dict.get('calculationId')
        
        cursor.execute('''
            INSERT INTO "audit_logging_auditlog" 
            ("date", "author", "resource", "action", "payload", "calculation_id")
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', [date, author, resource, action, payload, calc_id])
        inserted += 1
    
    return inserted

def initialize_history_tables():
    """Backfill history tables for models that have data but no history."""
    from django.apps import apps
    from simple_history.manager import HistoryManager
    from django.core.management import call_command
    
    print("\n🔄 Initializing History Tables...")
    
    models_backfilled = 0
    for model in apps.get_models():
        if hasattr(model, 'history') and isinstance(model.history, HistoryManager):
            try:
                if model.objects.exists() and not model.history.exists():
                    label = model._meta.label
                    print(f"  📝 Backfilling history for {label}...")
                    call_command('populate_history', label, auto=True, batchsize=500)
                    models_backfilled += 1
            except Exception as e:
                print(f"  ⚠️ Failed to backfill {model.__name__}: {e}")
    
    return models_backfilled

def reset_sequences(cursor):
    """Reset all sequences to max(id) + 1."""
    cursor.execute("""
        DO $$
        DECLARE
            r RECORD;
            seq_name text;
        BEGIN
            FOR r IN 
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            LOOP
                BEGIN
                    seq_name := pg_get_serial_sequence('"' || r.table_name || '"', 'id');
                    IF seq_name IS NOT NULL THEN
                        EXECUTE 'SELECT setval(''' || seq_name || ''', COALESCE(MAX(id), 1) + 1) FROM "' || r.table_name || '"';
                    END IF;
                EXCEPTION WHEN OTHERS THEN NULL; END;
            END LOOP;
        END $$;
    """)

def run_migration():
    """Main migration entry point."""
    setup_django()
    
    from django.db import connection, transaction
    
    print("=" * 60)
    print("🚀 Lex V1 → V2 In-Place Migration")
    print("=" * 60)
    
    with connection.cursor() as cursor:
        # Check for legacy tables
        legacy_tables = check_legacy_tables_exist(cursor)
        if not legacy_tables:
            print("\n✅ No legacy tables found. Database is already V2 or freshly initialized.")
            print("   Running history initialization only...")
        else:
            print(f"\n📋 Found {len(legacy_tables)} legacy tables: {', '.join(legacy_tables)}")
        
        # Migrate Calculation Logs
        print("\n--- Step 1: Migrate Calculation Logs ---")
        calc_count = migrate_calculation_logs(cursor)
        print(f"  ✅ {calc_count} calculation logs in target table.")
        
        # Migrate User Change Logs
        print("\n--- Step 2: Migrate User Change Logs ---")
        audit_count = migrate_user_change_logs(cursor)
        print(f"  ✅ {audit_count} audit logs in target table.")
        
        # Reset sequences
        print("\n--- Step 3: Reset Sequences ---")
        reset_sequences(cursor)
        print("  ✅ Sequences reset.")
        
        # Commit the transaction
        transaction.commit()
        print("\n  💾 Changes committed to database.")
    
    # Initialize history tables (uses ORM, separate transaction)
    print("\n--- Step 4: Initialize History Tables ---")
    history_count = initialize_history_tables()
    print(f"  ✅ {history_count} models backfilled with history.")
    
    print("\n" + "=" * 60)
    print("✅ Migration Complete!")
    print("=" * 60)
    
    # Summary
    print("\n📊 Summary:")
    print(f"   - Calculation Logs: {calc_count}")
    print(f"   - Audit Logs: {audit_count}")
    print(f"   - History Models Backfilled: {history_count}")
    
    if legacy_tables:
        print("\n⚠️ Legacy tables still exist. After verification, you can drop them:")
        for table in legacy_tables:
            print(f"   DROP TABLE IF EXISTS \"{table}\" CASCADE;")

if __name__ == "__main__":
    run_migration()
