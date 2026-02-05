from django.core.management.base import BaseCommand
from django.apps import apps
from simple_history.models import HistoricalRecords
from simple_history.manager import HistoryManager
from simple_history.utils import bulk_create_with_history
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Initializes history for tracked models if history is empty but live data exists.'

    def handle(self, *args, **options):
        self.stdout.write("Starting Smart History Initialization...")
        
        # 1. Identify all models that have Simple History tracking
        tracked_models = []
        for model in apps.get_models():
            # Check for 'history' attribute which is a HistoryManager
            if hasattr(model, 'history') and isinstance(model.history, HistoryManager):
                tracked_models.append(model)
        
        self.stdout.write(f"Found {len(tracked_models)} tracked models.")
        
        for model in tracked_models:
            model_name = model._meta.label
            history_model = model.history.model
            
            # 2. Check counts
            live_count = model.objects.count()
            history_count = history_model.objects.count()
            
            self.stdout.write(f"Checking {model_name}: Live={live_count}, History={history_count}")
            
            # 3. Decision Logic: Populate ONLY if Live > 0 and History == 0
            if live_count > 0 and history_count == 0:
                self.stdout.write(self.style.WARNING(f"  -> Initializing history for {model_name}..."))
                
                try:
                    # 4. Bulk Populate
                    # simple_history's bulk_create_with_history is usually for creating NEW objects.
                    # For existing objects, we iterate and save? No, that's slow.
                    # We use simple_history's populate_history command logic or manual bulk insert.
                    
                    # Optimization: Use the manager's bulk_history_create if available, 
                    # or fall back to iterating efficiently.
                    # 'populate_history' command from simple_history does exactly this.
                    # We can call it programmatically or replicate its bulk logic.
                    
                    # Replicating logic for control:
                    # Fetch all live instances
                    instances = model.objects.all()
                    
                    # Manual Bulk Populate (Robust Fallback)
                    # Bypasses internal simple_history helpers to avoid attribute errors on custom history models
                    self.stdout.write("    (Using manual bulk creation)")
                    
                    history_objects = []
                    HistoryModel = model.history.model
                    
                    # Determine field mapping
                    # We need to copy fields from instance to history_instance
                    # Simple history usually maps fields by name.
                    history_fields = [f.name for f in HistoryModel._meta.fields]
                    
                    # Detect if we use 'valid_from' (StandardHistory) or 'history_date' (Default)
                    date_field = 'valid_from' if 'valid_from' in history_fields else 'history_date'
                    
                    from django.utils import timezone
                    now = timezone.now()
                    
                    batch_size = 1000
                    instances = model.objects.all().iterator()
                    
                    count = 0
                    for instance in instances:
                        history_data = {}
                        
                        # Copy tracked fields
                        for field in model._meta.fields:
                            # Skip if field not in history model (though usually they are)
                            if field.name in history_fields:
                                history_data[field.name] = getattr(instance, field.name)
                        
                        # Set control fields
                        history_data[date_field] = now
                        history_data['history_type'] = '+' # Initial create
                        history_data['history_user'] = None # System generated
                        
                        # Set history_change_reason if exists
                        if 'history_change_reason' in history_fields:
                            history_data['history_change_reason'] = 'Initial Data Migration'
                            
                        # Handle relationship id fields if needed? 
                        # ORM instantiation handles values.
                        
                        history_objects.append(HistoryModel(**history_data))
                        
                        if len(history_objects) >= batch_size:
                            HistoryModel.objects.bulk_create(history_objects)
                            count += len(history_objects)
                            history_objects = []
                            self.stdout.write(f"    ... processed {count}")
                            
                    if history_objects:
                        HistoryModel.objects.bulk_create(history_objects)
                        count += len(history_objects)
                        
                    self.stdout.write(self.style.SUCCESS(f"  -> Done. Created {count} history records."))
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  -> Failed to populate {model_name}: {e}"))
            
            elif live_count == 0:
                self.stdout.write(f"  -> Skipped (Live table empty).")
            else:
                self.stdout.write(f"  -> Skipped (History already exists).")

        self.stdout.write(self.style.SUCCESS("History Initialization Completed."))
