from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.models.calculation_log import CalculationLog
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Migrates V1 CalculationLog entries to V2 schema'

    def handle(self, *args, **options):
        self.stdout.write("Checking for legacy table 'generic_app_calculationlog'...")
        
        with connection.cursor() as cursor:
            # Check if table exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'generic_app_calculationlog'
                );
            """)
            exists = cursor.fetchone()[0]
            
            if not exists:
                self.stdout.write(self.style.WARNING("Legacy table not found. Skipping migration."))
                return

            self.stdout.write("Found legacy table. Starting migration...")
            
            # Count total rows for progress
            cursor.execute("SELECT COUNT(*) FROM generic_app_calculationlog")
            total_rows = cursor.fetchone()[0]
            self.stdout.write(f"Total rows to migrate: {total_rows}")

            # Use server-side cursor to fetch in chunks
            # Note: 'name' argument creates a server-side cursor
            with transaction.atomic():
                cursor.execute('DECLARE legacy_cursor CURSOR FOR SELECT id, "timestamp", "calculationId", "calculation_record", "message", "method" FROM generic_app_calculationlog')
                
                batch_size = 1000
                processed = 0
                
                while True:
                    cursor.execute(f"FETCH {batch_size} FROM legacy_cursor")
                    rows = cursor.fetchall()
                    if not rows:
                        break
                    
                    new_logs = []
                    for row in rows:
                        v1_id, timestamp, calc_id, calc_record, message, method = row
                        
                        # Parse calculation_record ("model_id")
                        ct = None
                        obj_id = None
                        
                        if calc_record and "_" in calc_record:
                            try:
                                # Start from the right to handle model names with underscores
                                parts = calc_record.rsplit('_', 1)
                                if len(parts) == 2:
                                    model_name = parts[0]
                                    obj_id_str = parts[1]
                                    
                                    if obj_id_str.isdigit():
                                        obj_id = int(obj_id_str)
                                        # Try to find ContentType
                                        # Note: This is best-effort. V1 model names might not match V2 exactly.
                                        # We assume standard lower-case mapping.
                                        ct = ContentType.objects.filter(model=model_name.lower()).first()
                            except Exception:
                                # If parsing fails, we migrate the log without the link
                                pass

                        # Construct V2 message (append method info if valuable?)
                        # V2 calculation_log field = V1 message
                        # We append method info to preserve stack trace context if needed
                        full_message = message
                        if method and method != "[]": # Check for empty list string from V1
                             full_message += f"\n[Legacy Trace]: {method}"

                        new_logs.append(CalculationLog(
                            timestamp=timestamp,
                            calculationId=calc_id,
                            calculation_log=full_message,
                            content_type=ct,
                            object_id=obj_id,
                            # parent_log and audit_log remain None
                        ))
                    
                    # Bulk create V2 logs
                    CalculationLog.objects.bulk_create(new_logs)
                    
                    processed += len(rows)
                    self.stdout.write(f"Migrated {processed}/{total_rows}...")

        self.stdout.write(self.style.SUCCESS("Migration completed successfully."))
