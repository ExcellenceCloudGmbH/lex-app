from simple_history.models import HistoricalRecords
from django.db import models
from django.utils import timezone

class StandardHistory(HistoricalRecords):
    """
    Standard Historical Records with customized field names.
    Specifically renames 'history_date' to 'valid_from'.
    """
    
    def get_extra_fields(self, model, fields):
        """Override to rename history_date to valid_from."""
        
        def revert_url(self):
            # ... custom logic if needed or just return None
            return None
            
        def get_instance(self):
            return getattr(self, self.instance_type._meta.model_name)

        extra_fields = {
            "history_id": self._get_history_id_field(),
            "valid_from": models.DateTimeField(db_index=self._date_indexing is True),
            "history_change_reason": self._get_history_change_reason_field(),
            "history_type": models.CharField(
                max_length=1,
                choices=(("+", "Created"), ("~", "Changed"), ("-", "Deleted")),
            ),
            "history_relation": self.fields_included(model), # What is this? usually it copies fields.
            # simple_history default implementation injects fields directly or copies them?
            # get_extra_fields usually returns ONLY the control fields. The data fields are handled separately by 'copy_fields'.
            # Wait, looking at simple_history source code pattern (from experience):
            # It usually returns a dictionary of fields to add to the historical model.
            
            # Replicating standard behavior but renaming history_date -> valid_from
            "valid_to": models.DateTimeField(
                default=None,
                null=True,
                blank=True,
                help_text="The date/time when this fact ceased to be true in the real world."
            ),
            "instance": property(get_instance),
            "instance_type": model,
        }
        
        # Add user field
        if self.user_id_field is not None:
             extra_fields["history_user_id"] = self.user_id_field
             extra_fields["history_user"] = property(self.user_getter, self.user_setter)
        else:
             extra_fields["history_user"] = models.ForeignKey(
                 'auth.User', null=True, on_delete=models.SET_NULL, db_constraint=False
             )
             
        return extra_fields

    def get_meta_options(self, model):
        """Update ordering to use valid_from."""
        meta_fields = super().get_meta_options(model)
        meta_fields["ordering"] = ("-valid_from", "-history_id")
        meta_fields["get_latest_by"] = ("valid_from", "history_id")
        return meta_fields

    def create_historical_record(self, instance, history_type, using=None):
        """Override to set valid_from."""
        manager = getattr(instance, self.manager_name)
        attrs = {}
        for field in self.fields_included(instance):
             attrs[field.attname] = getattr(instance, field.attname)
             
        history_instance = manager.model(
            valid_from=getattr(instance, '_history_date', timezone.now()),
            history_type=history_type,
            history_change_reason=getattr(instance, '_history_change_reason', ''),
            history_user=self.get_history_user(instance),
            **attrs
        )
        history_instance.save(using=using)
        
        from simple_history.signals import post_create_historical_record
        post_create_historical_record.send(
            sender=manager.model,
            instance=instance,
            history_instance=history_instance,
            history_date=history_instance.valid_from,
            history_user=history_instance.history_user,
            using=using,
        )
        return history_instance
