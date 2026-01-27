from django.utils import timezone
from .bitemporal_sync import BitemporalSynchronizer
from datetime import timedelta
from django.apps import apps
import logging

logger = logging.getLogger(__name__)

class TemporalReconciler:
    """
    Periodically checks for records that have 'become valid' due to the passage of time
    and triggers synchronization.
    """
    
    @staticmethod
    def reconcile_changes_since(start_time, end_time=None):
        """
        Scans all registered historical models for records where `valid_from` falls
        within the window [start_time, end_time].
        
        This is efficient because we only touch records that potentially changed state.
        """
        if end_time is None:
            end_time = timezone.now()
            
        # Iterate all models? Or how do we know which ones are bitemporal?
        # We can look for models with 'history' attribute or iterate our registry if we had one.
        # For now, let's scan all models in 'lex_app' or rely on calling it per model.
        # Scanning all models is safer for a generic utility.
        
        # For this implementation, we will iterate all models and check if they have history.
        synced_count = 0
        
        from django.db import connection
        tables = connection.introspection.table_names()
        
        for model in apps.get_models():
            if hasattr(model, 'history'):
                history_model = model.history.model
                
                # Check if table exists (Crucial for isolated tests)
                if history_model._meta.db_table not in tables:
                    continue

                # Query: Records where valid_from is in [start, end]
                # These are the records that "Activated" in this window.
                candidates = history_model.objects.filter(
                    valid_from__gte=start_time,
                    valid_from__lte=end_time
                ).values_list(model._meta.pk.name, flat=True).distinct()
                
                if candidates:
                    # logger.info(f"Reconciling {len(candidates)} records for {model.__name__}")
                    print(f"Reconciling {len(candidates)} records for {model.__name__}")
                    for pk in candidates:
                        BitemporalSynchronizer.sync_record_for_model(model, pk, history_model)
                        synced_count += 1
                        
    @staticmethod
    def reconcile_model_window(model, start_time, end_time):
        """
        Reconcile a specific model for a specific window.
        """
        if hasattr(model, 'history'):
            history_model = model.history.model
            
            # Check table existence (safety)
            # from django.db import connection
            # if history_model._meta.db_table not in connection.introspection.table_names():
            #     return 0
                
            candidates = history_model.objects.filter(
                valid_from__gte=start_time,
                valid_from__lte=end_time
            ).values_list(model._meta.pk.name, flat=True).distinct()
            
            synced_count = 0
            if candidates:
                # logger.info(f"Reconciling {len(candidates)} records for {model.__name__}")
                for pk in candidates:
                    BitemporalSynchronizer.sync_record_for_model(model, pk, history_model)
                    synced_count += 1
            return synced_count
        return 0
