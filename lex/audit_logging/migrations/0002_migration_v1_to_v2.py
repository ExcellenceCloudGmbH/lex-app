from django.db import migrations, connection
import json
from datetime import datetime

# Helper function to parse "Model_ID" string
# Returns (model_name_lower, object_id)
# e.g., "investor_1" -> ("investor", 1)
# e.g., "init_upload" -> (None, None)
def parse_calculation_record(record_str):
    if not record_str or record_str.lower() in ["legacy", "init_upload", "test_id"]:
        return None, None
    
    # Try to split by underscore, starting from right to handle model names with underscores
    parts = record_str.rsplit('_', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0].lower(), int(parts[1])
    
    return None, None

def migrate_v1_to_v2(apps, schema_editor):
    AuditLog = apps.get_model("audit_logging", "AuditLog")
    CalculationLog = apps.get_model("audit_logging", "CalculationLog")
    ContentType = apps.get_model("contenttypes", "ContentType")
    
    # Pre-cache ContentTypes to avoid N+1 queries
    ct_cache = {}
    for ct in ContentType.objects.all():
        ct_cache[ct.model] = ct

    # Get list of all tables in the database
    all_tables = connection.introspection.table_names()

    # --- 1. Migrate UserChangeLogs to AuditLogs ---
    print("Migrating UserChangeLogs...")
    
    if 'generic_app_userchangelog' not in all_tables:
        print("Table generic_app_userchangelog not found, skipping.")
    else:
        with connection.cursor() as cursor:
            # Select ID for idempotency
            cursor.execute('SELECT "id", "user_name", "timestamp", "message", "calculation_record" FROM "generic_app_userchangelog"')
            rows = cursor.fetchall()
            
            audit_logs = []
            for row in rows:
                pk, user_name, timestamp, message, calculation_record = row
                
                payload = {"message": message}
                
                audit_logs.append(AuditLog(
                    id=pk, # Preserve ID
                    date=timestamp,
                    author=user_name,
                    resource=calculation_record,
                    action='update', 
                    payload=payload
                ))
            
            # Idempotent insert
            AuditLog.objects.bulk_create(audit_logs, batch_size=1000, ignore_conflicts=True)
            print(f"Migrated {len(audit_logs)} AuditLogs.")

    # --- 2. Build CalculationID Lookup Map ---
    print("Building CalculationID Map...")
    calc_id_map = {} # calculationId -> (ContentType, object_id)
    
    if 'generic_app_calculationids' not in all_tables:
         print("Table generic_app_calculationids not found, skipping map build.")
    else:
        with connection.cursor() as cursor:
            # Fixed column name from calculationId to calculation_id based on error
            cursor.execute('SELECT "calculation_id", "calculation_record" FROM "generic_app_calculationids"')
            rows = cursor.fetchall()
            
            for row in rows:
                calc_id, calc_record = row
                model_name, obj_id = parse_calculation_record(calc_record)
                
                if model_name and model_name in ct_cache:
                    calc_id_map[calc_id] = (ct_cache[model_name], obj_id)

    # --- 3. Migrate CalculationLogs ---
    print("Migrating CalculationLogs...")
    
    if 'generic_app_calculationlog' not in all_tables:
        print("Table generic_app_calculationlog not found, skipping.")
    else:
        with connection.cursor() as cursor:
            # Dynamically check columns because of schema discrepancy reports
            cursor.execute('SELECT * FROM "generic_app_calculationlog" LIMIT 0')
            columns = [col[0] for col in cursor.description]
            print(f"Columns in generic_app_calculationlog: {columns}")
            
            msg_col = "log_message" if "log_message" in columns else "message"
            calc_id_col = "calculationId" if "calculationId" in columns else "calculation_id"
            
            print(f"Using message column: {msg_col}, calc_id column: {calc_id_col}")
            
            # Using dynamic column names + ID
            cursor.execute(f'SELECT "id", "{calc_id_col}", "{msg_col}" FROM "generic_app_calculationlog"')
            rows = cursor.fetchall()
            
            calc_logs = []
            now = datetime.now()
            
            for row in rows:
                pk, calc_id, message = row
                
                ct = None
                obj_id = None
                
                # Resolve smart link
                if calc_id in calc_id_map:
                    ct, obj_id = calc_id_map[calc_id]
                
                calc_logs.append(CalculationLog(
                    id=pk, # Preserve ID
                    timestamp=now, # Default since original data lost it
                    calculationId=calc_id,
                    calculation_log=message,
                    content_type=ct,
                    object_id=obj_id,
                ))
            
            # Idempotent insert
            CalculationLog.objects.bulk_create(calc_logs, batch_size=1000, ignore_conflicts=True)
            print(f"Migrated {len(calc_logs)} CalculationLogs.")


class Migration(migrations.Migration):

    dependencies = [
        ('audit_logging', '0001_initial'),
        # We don't depend on generic_app migrations because we use raw SQL
    ]

    operations = [
        migrations.RunPython(migrate_v1_to_v2, migrations.RunPython.noop),
    ]