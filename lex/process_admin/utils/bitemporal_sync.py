from django.db import models
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

class BitemporalSynchronizer:
    """
    Service to synchronize the Main Table with the Effective History Record.
    """
    
    @staticmethod
    def sync_record_for_model(model_class, pk_val, history_model=None):
        """
        Synchronizes a specific record for a model based on the current system time.
        """
        if history_model is None:
            if hasattr(model_class, 'history'):
                history_model = model_class.history.model
            else:
                logger.error(f"Cannot sync {model_class.__name__}: No history model found.")
                return

        pk_name = model_class._meta.pk.name
        now = timezone.now()
        
        # 1. Find Effective Record
        effective_record = history_model.objects.filter(
            **{pk_name: pk_val}
        ).filter(
             valid_from__lte=now
        ).filter(
             models.Q(valid_to__gt=now) | models.Q(valid_to__isnull=True)
        ).order_by('-valid_from', '-history_id').first()
        
        # Check if valid (not a deletion marker)
        is_valid_record = effective_record and effective_record.history_type != '-'

        if is_valid_record:
             # Update/Restore Main Table
             try:
                 main_instance = model_class.objects.get(pk=pk_val)
             except model_class.DoesNotExist:
                 main_instance = model_class(pk=pk_val)
             else:
                 pass
                 
             changed = False
             for field in model_class._meta.fields:
                 field_name = field.attname
                 if field_name == model_class._meta.pk.attname: continue
                 
                 if hasattr(effective_record, field_name):
                     new_val = getattr(effective_record, field_name)
                     current_val = getattr(main_instance, field_name)
                     if new_val != current_val:
                         setattr(main_instance, field_name, new_val)
                         changed = True
                         
             if changed or main_instance._state.adding:
                 main_instance.skip_history_when_saving = True
                 main_instance.save()
                 
        else:
             # Cleanup Main Table
             try:
                 main_instance = model_class.objects.get(pk=pk_val)
                 main_instance.skip_history_when_saving = True
                 main_instance.delete()
             except model_class.DoesNotExist:
                 pass
