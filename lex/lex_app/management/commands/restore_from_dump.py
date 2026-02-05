import os
import json
import django
from django.conf import settings
from django.db import transaction

# Setup Django standalone
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
django.setup()

from audit_logging.models import CalculationLog as NewCalculationLog
from django.contrib.contenttypes.models import ContentType

def restore_from_dump(dump_file):
    if not os.path.exists(dump_file):
        print(f"❌ Dump file not found: {dump_file}")
        return

    with open(dump_file, 'r') as f:
        data = json.load(f)

    print(f"Loaded dump with {len(data.keys())} tables.")

    # Migration Logic: generic_app_calculationlog -> audit_logging_calculationlog
    legacy_logs = data.get('generic_app_calculationlog', [])
    print(f"Found {len(legacy_logs)} legacy calculation logs to migrate.")

    new_logs = []
    skipped = 0
    
    # Map legacy fields to new model
    # Assumption: Legacy had 'timestamp', 'message', 'calculation_id'
    # New has 'created_at', 'message', 'calculation_id', etc.
    
    with transaction.atomic():
        # Clear existing logs in V2 if any (since we are recreating)
        NewCalculationLog.objects.all().delete()
        
        for row in legacy_logs:
            try:
                # Transform Logic
                new_log = NewCalculationLog(
                    # ID preservation is optional, but good for history
                    # id=row.get('id'), 
                    created_at=row.get('timestamp') or row.get('created_at'),
                    message=row.get('message', ''),
                    calculation_id=row.get('calculation_id') or row.get('calculationId'),
                    # Defaulting fields that didn't exist
                    log_level='INFO', 
                )
                
                # Handle Generic Foreign Key if possible (Requires parsing 'related_object' if it existed)
                # For now, leaving GFK null as per "Scenario B" in migration doc
                
                new_logs.append(new_log)
            except Exception as e:
                print(f"Skipping row {row.get('id')}: {e}")
                skipped += 1

        if new_logs:
            NewCalculationLog.objects.bulk_create(new_logs)
            print(f"✅ Successfully inserted {len(new_logs)} logs into V2 table.")
        else:
            print("No logs to insert.")
            
    print(f"Migration Complete. Skipped: {skipped}")

if __name__ == "__main__":
    restore_from_dump("legacy_v1_full_dump.json")
