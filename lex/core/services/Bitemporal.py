from django.db import models

class ValidTimeMixin(models.Model):
    """
    Mixin for Historical Models to track the END of validity.
    Valid From is handled by simple_history's `history_date`.
    """
    valid_to = models.DateTimeField(
        default=None,
        null=True,
        blank=True,
        help_text="The date/time when this fact ceased to be true in the real world."
    )

    class Meta:
        abstract = True

class SysTimeMixin(models.Model):
    """
    Mixin for Meta-Historical Models to track the END of system knowledge.
    Sys From is handled by `meta_history_date`.
    """
    sys_to = models.DateTimeField(
        default=None,
        null=True,
        blank=True,
        help_text="The date/time when this system record was superseded."
    )

    class Meta:
        abstract = True

class BiTemporalMixin(ValidTimeMixin, SysTimeMixin):
    """
    Composite mixin if something needs both (DEPRECATED for this specific architecture).
    """
    class Meta:
        abstract = True

from django.db import models

def get_queryset_as_of(model_class, as_of):
    """
    Returns a QuerySet of Historical Records for the given model_class
    that represents the system's knowledge at `as_of` time.
    
    Args:
        model_class: The Main Model (e.g. Invoice) or the Historical Model.
        as_of: Datetime (aware) representing the System Time to query.
    """
    # Determine if input is Main Model or History Model
    is_main_model = hasattr(model_class, 'history') and not hasattr(model_class, 'history_id')
    is_history_model = hasattr(model_class, 'history_id')
    
    if is_main_model:
        # MODE 1: Main Table Query -> Valid Time
        # "What was effectively true at this time?"
        # Target: History Model
        HistoryModel = model_class.history.model
        
        return HistoryModel.objects.filter(
            valid_from__lte=as_of
        ).filter(
            models.Q(valid_to__gt=as_of) | models.Q(valid_to__isnull=True)
        ).exclude(
            history_type='-'
        )
        
    elif is_history_model:
        # MODE 2: History Table Query -> System Time (Transaction Time)
        # "What did the system know about this history record at this time?"
        # Target: Meta History Model
        if not hasattr(model_class, 'meta_history'):
             # Fallback or Error? 
             # If no meta-history, we can't do system time travel effectively on just one table 
             # unless we used a different architecture.
             # Assume Meta exists as per our architecture.
             raise ValueError(f"History Model {model_class} does not have meta_history tracking.")
             
        MetaModel = model_class.meta_history.model
        
        return MetaModel.objects.filter(
            sys_from__lte=as_of
        ).filter(
            models.Q(sys_to__gt=as_of) | models.Q(sys_to__isnull=True)
        )
    else:
        raise ValueError(f"Model {model_class} is neither a Main Model with history nor a History Model.")

def resurrect_object(model_class, pk, valid_from, attributes=None, valid_to=None):
    """
    Resurrects (re-creates) a deleted object with a specific valid_from date.
    Optionally sets a fixed valid_to date by scheduling a future deletion in history.
    
    Args:
        model_class: The Main Model class (e.g. Invoice).
        pk: The primary key of the object to resurrect.
        valid_from: Datetime (aware) when validity starts.
        attributes: Dict of field values to set.
        valid_to: Optional Datetime (aware) when validity ends (Simulated by inserting a '-' record).
    """
    if attributes is None:
        attributes = {}
        
    # 1. Create/Update the Main Model Record
    # We use update_or_create logic or just save.
    # Check if exists to determine if we are resurrecting or just updating?
    # Django save() handles both (pk provided).
    
    instance = model_class(pk=pk, **attributes)
    
    # Set the History Date (start of validity)
    instance._history_date = valid_from
    instance.save()
    
    # 2. Handle valid_to (Optional Termination)
    if valid_to:
        # We must insert a Deletion Marker ('-') at valid_to in the HISTORY table.
        # This will effectively close the previous record at valid_to via Strict Chaining.
        if hasattr(model_class, 'history'):
            HistoryModel = model_class.history.model
            
            # We create a history record type '-'
            # Note: We don't delete the Main Model row here (it remains active "in reality" until valid_to?).
            # If valid_to is in the future, Main Model is active.
            # If valid_to is in the past, Main Model should technically not exist? 
            # But this is Bitemporal. Main Table = Current Latest State.
            # So we just record the history.
            
            HistoryModel.objects.create(
                id=pk, # Business Key
                history_type='-',
                valid_from=valid_to,
                history_user=None, # Or passing user?
                # Other fields are null for deletion usually? 
                # Or we can copy attributes? Simple History '-' usually copies attributes too.
                **attributes
            )
            
    return instance

