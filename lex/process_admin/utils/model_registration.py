import asyncio
import os
from typing import List, Type, Optional

import nest_asyncio
from asgiref.sync import sync_to_async
from simple_history import register
from django.db import models
from django.contrib import admin
from simple_history.signals import post_create_historical_record

from lex.lex_app.simple_history_config import should_track_model, get_model_exclusion_reason
from django.utils import timezone
from django.db import models
from django.utils import timezone
from django.db import models

import logging
logger = logging.getLogger(__name__)
from simple_history.models import HistoricalRecords

from django.db.models import Subquery, OuterRef





class MetaLevelHistoricalRecords(HistoricalRecords):
    """
    A specialized History provider for 'History on History'.
    It renames the control fields to 'meta_...' to avoid collisions.
    """

    def get_extra_fields(self, model, fields):
        """Override definition of fields to use 'meta_' prefixes."""

        def revert_url(self):
            return None

        def get_instance(self):
            return None

            # 1. Define the Meta Control Fields with NEW names

        extra_fields = {
            "meta_history_id": self._get_history_id_field(),
            "sys_from": models.DateTimeField(db_index=self._date_indexing is True),
            "sys_to": models.DateTimeField(
                default=None,
                null=True,
                blank=True,
                help_text="The date/time when this system record was superseded."
            ),
            "meta_history_change_reason": self._get_history_change_reason_field(),
            "meta_history_type": models.CharField(
                max_length=1,
                choices=(("+", "Created"), ("~", "Changed"), ("-", "Deleted")),
            ),
            "history_object": models.ForeignKey(
                model,
                null=True,
                on_delete=models.SET_NULL,
                db_constraint=False
            ),
            "meta_task_name": models.CharField(
                max_length=255, 
                null=True, 
                blank=True, 
                unique=True,
                help_text="Name of the Celery PeriodicTask scheduled for this record."
            ),
            "meta_task_status": models.CharField(
                max_length=20,
                default="NONE",
                choices=(("NONE", "None"), ("SCHEDULED", "Scheduled"), ("DONE", "Done"), ("CANCELLED", "Cancelled"))
            ),
            "instance": property(get_instance),
            "instance_type": model,
        }

        # 2. Handle User Field manually to rename key
        if self.user_id_field is not None:
            extra_fields["meta_history_user_id"] = self.user_id_field
            extra_fields["meta_history_user"] = property(self.user_getter, self.user_setter)
        else:
            extra_fields["meta_history_user"] = models.ForeignKey(
                'auth.User',
                null=True,
                on_delete=models.SET_NULL,
                db_constraint=False
            )

        return extra_fields

    def get_meta_options(self, model):
        """Update ordering to use the new field names."""
        meta_fields = super().get_meta_options(model)
        meta_fields["ordering"] = ("-sys_from", "-meta_history_id")
        meta_fields["get_latest_by"] = ("sys_from", "meta_history_id")
        return meta_fields

    def create_historical_record(self, instance, history_type, using=None):
        """Override creation to inject data into 'meta_' fields."""
        manager = getattr(instance, self.manager_name)
        attrs = {}
        # Copy the data fields (this includes 'history_date' from Level 1)
        for field in self.fields_included(instance):
            attrs[field.attname] = getattr(instance, field.attname)

        # Check for Strict Chaining Update Flag
        is_structure_update = getattr(instance, '_strict_chaining_update', False)

        if is_structure_update:
            # TRY to find an existing OPEN meta record to update in-place
            # We want the LATEST one.
            latest = manager.all().order_by('-sys_from', '-meta_history_id').first()
            if latest and latest.sys_to is None:
                # Update attributes
                for field in self.fields_included(instance):
                    setattr(latest, field.attname, getattr(instance, field.attname))
                
                # Also update sys_from? 
                # User scenario shows: 
                # Row 2: "12:05 inf". (Created at 12:05).
                # Updates to "12:00 13:00".
                # sys_from stays 12:05!
                # So we DO NOT update sys_from.
                
                latest.save(using=using)
                return latest

        # Inject the Meta Control Fields
        history_instance = manager.model(
            sys_from=getattr(instance, '_history_date', timezone.now()),
            meta_history_type=history_type,
            meta_history_change_reason=getattr(instance, '_history_change_reason', ''),
            meta_history_user=self.get_history_user(instance),
            history_object=instance,
            **attrs
        )

        # --- FIX: SAVE THE RECORD ---
        history_instance.save(using=using)
        return history_instance


class ModelRegistration:
    """
    Handles registration of Django models with admin sites and history tracking.
    
    This class manages the registration of different types of models including:
    - HTML Report models
    - Process models  
    - Standard models with history tracking
    - CalculationModel instances with aborted calculation handling
    """
    
    @classmethod
    def register_models(cls, models: List[Type[models.Model]], untracked_models: Optional[List[str]] = None) -> None:
        """
        Register a list of Django models with appropriate admin sites and history tracking.
        
        Args:
            models: List of Django model classes to register
            untracked_models: Optional list of model names (lowercase) that should not have history tracking.
                            Defaults to empty list if None.
        
        Raises:
            ImportError: If required model classes cannot be imported
            AttributeError: If model registration fails due to missing attributes
        """
        from lex.process_admin.settings import processAdminSite, adminSite
        from lex.core.models.process import Process
        from lex.core.models.html_report import HTMLReport
        from lex.core.models.calculation_model import CalculationModel
        from django.contrib.auth.models import User

        # Initialize untracked_models to empty list if None provided
        if untracked_models is None:
            untracked_models = []

        # Configure User model display name
        def get_username(self):
            return f"{self.first_name} {self.last_name}"

        User.add_to_class("__str__", get_username)
        processAdminSite.register([User])

        # Process each model based on its type
        for model in models:
            try:
                if issubclass(model, HTMLReport):
                    cls._register_html_report(model)
                elif issubclass(model, Process):
                    cls._register_process_model(model)
                elif not issubclass(model, type) and not model._meta.abstract:
                    cls._register_standard_model(model, untracked_models)
                    
                    # Handle CalculationModel reset logic if applicable
                    if issubclass(model, CalculationModel):
                        cls._handle_calculation_model_reset(model)
            except Exception as e:
                raise RuntimeError(f"Failed to register model {model.__name__}: {str(e)}") from e

    @classmethod
    def _register_html_report(cls, model: Type[models.Model]) -> None:
        """
        Register an HTMLReport model with the process admin site.
        
        Args:
            model: HTMLReport model class to register
        """
        from lex.process_admin.settings import processAdminSite
        
        model_name = model.__name__.lower()
        processAdminSite.registerHTMLReport(model_name, model)
        processAdminSite.register([model])

    @classmethod
    def _register_process_model(cls, model: Type[models.Model]) -> None:
        """
        Register a Process model with the process admin site.
        
        Args:
            model: Process model class to register
        """
        from lex.process_admin.settings import processAdminSite
        
        model_name = model.__name__.lower()
        processAdminSite.registerProcess(model_name, model)
        processAdminSite.register([model])

    @classmethod
    def _register_standard_model(cls, model: Type[models.Model], untracked_models: List[str]) -> None:
        """
        Register a standard model with both admin sites and optional history tracking.
        """
        from lex.process_admin.settings import processAdminSite, adminSite
        from simple_history import register

        model_name = model.__name__.lower()

        # Register with process admin site
        processAdminSite.register([model])

        exclusion_reason = get_model_exclusion_reason(model)
        is_already_tracked = exclusion_reason == "Already has history tracking"
        should_track = (exclusion_reason is None or is_already_tracked) and model_name not in untracked_models

        if should_track:
            try:
                historical_model = None
                
                # --- LEVEL 1: Standard History ---
                from lex.core.services.standard_history import StandardHistory
                from simple_history.exceptions import MultipleRegistrationsError
                
                try:
                    register(model, records_class=StandardHistory)
                except MultipleRegistrationsError:
                    pass
                
                # Get the historical model (whether just created or existing)
                if hasattr(model, 'history'):
                    historical_model = model.history.model
                    processAdminSite.register([historical_model])
                else:
                    logger.error(f"Failed to retrieve history model for {model.__name__} after registration attempt.")
                    return

                # --- LEVEL 2: History on History (Meta-History) ---
                # 1. Instantiate the Custom Class
                history = MetaLevelHistoricalRecords(
                    app=model._meta.app_label,
                    table_name=f'{model._meta.db_table}_meta_history',
                    custom_model_name=lambda x: f'Meta{x}',
                )

                # 2. Attach it to the class (sets up the signal listener)
                try:
                    history.contribute_to_class(historical_model, 'meta_history')
                except MultipleRegistrationsError:
                    # Ignore if meta history is also already registered
                    pass
                except Exception as e:
                    # If it fails (e.g. already has meta_history), try to proceed implies it might be there.
                    # check if it exists
                    if not hasattr(historical_model, 'meta_history'):
                        logger.warning(f"Could not attach meta_history to {historical_model.__name__}: {e}")

                # 3. CRITICAL FIX: Force finalize immediately!
                try:
                    history.finalize(sender=historical_model)
                except Exception:
                    pass

                def trigger_meta_history(sender, instance, **kwargs):
                    # Renamed 'history_instance' to 'instance' to match post_save signal
                    history_instance = instance 
                    
                    # Ensure we only act for the specific model we are currently registering
                    if sender == historical_model:
                        try:
                            # 1. CANCEL PREVIOUS SCHEDULES (Mutation Handling)
                            # If this history record is being modified (updated), any previous schedule is invalid.
                            # We find Meta Records for THIS history_id that are SCHEDULED.
                            MetaModel = sender.meta_history.model
                            
                            # 2. GET OR CREATE META RECORD (Idempotency)
                            # Access the automatic record created by signal if possible
                            meta_instance = MetaModel.objects.filter(
                                history_object=history_instance
                            ).order_by('-sys_from', '-meta_history_id').first()

                            if not meta_instance:
                                 meta_instance = history.create_historical_record(history_instance, "+")
                            
                            # 3. SCHEDULE NEW (If Future)
                            # Check valid_from > Now
                            now = timezone.now()
                            # Use a small buffer (e.g. 5s) to avoid scheduling for immediate/past
                            if history_instance.valid_from > now + timezone.timedelta(seconds=5):
                                
                                # Check if already scheduled (idempotency)
                                if meta_instance.meta_task_status == "SCHEDULED" and meta_instance.meta_task_name:
                                    # CHECK IF TIME CHANGED!
                                    # If the logic is re-running, maybe valid_from changed?
                                    # We should verify if the existing task matches the current valid_from.
                                    # Ideally we rely on cancellation above? NO, cancellation is on MetaModel queries.
                                    # But we just got the meta_instance.
                                    
                                    # Simplest approach: If we are here (post_save), and we have a SCHEDULED task, 
                                    # we should probably revoke and re-schedule to be safe (ensure correct time),
                                    # OR check ClockedSchedule.
                                    
                                    # Since we didn't implement sophisticated checks, let's allow re-scheduling.
                                    # But we need to be careful not to spam.
                                    # For Bitemporal logic: If I update valid_from, I want the task to move.
                                    pass
                                
                                # Check environment variable for CELERY activation
                                if os.getenv("CELERY_ACTIVE", "false").lower() != "true":
                                    # Fallback: LocalSchedulerBackend (In-Process)
                                    from lex.process_admin.utils.local_scheduler import LocalSchedulerBackend
                                    from lex.lex_app.celery_tasks import activate_history_version
                                    
                                    scheduler = LocalSchedulerBackend()
                                    
                                    # Schedule the task directly
                                    # Note: We pass raw IDs as args just like the Celery task expects
                                    scheduler.schedule(
                                        run_at_time=history_instance.valid_from,
                                        func=activate_history_version,
                                        kwargs={
                                            "model_app_label": model._meta.app_label,
                                            "model_name": model_name,
                                            "history_id": history_instance.pk
                                        }
                                    )
                                    
                                    # We can still track it in MetaModel if we want, or at least mark as Scheduled
                                    # But since local backend is ephemeral, maybe we don't set a task_name?
                                    # OR we set a dummy one so we know it's handled.
                                    meta_instance.meta_task_status = "SCHEDULED"
                                    meta_instance.meta_task_name = f"local_thread_{int(now.timestamp())}"
                                    meta_instance.save(update_fields=["meta_task_status", "meta_task_name"])
                                    
                                else:
                                    from django_celery_beat.models import ClockedSchedule, PeriodicTask
                                    import json
                                    import uuid
                                    
                                    # Revoke any previous tasks for this meta record (just in case)
                                    if meta_instance.meta_task_name:
                                        PeriodicTask.objects.filter(name=meta_instance.meta_task_name).delete()
                                    
                                    clocked, _ = ClockedSchedule.objects.get_or_create(
                                        clocked_time=history_instance.valid_from
                                    )
                                    
                                    # Unique Task Name: activate_{app}_{model}_{hist_id}_{timestamp}_{uuid}
                                    task_unique_name = f"activate_{model._meta.app_label}_{model_name}_{history_instance.pk}_{int(now.timestamp())}_{uuid.uuid4()}"
                                    
                                    PeriodicTask.objects.create(
                                        clocked=clocked,
                                        name=task_unique_name,
                                        task="activate_history_version",
                                        # Args: label, model, history_id
                                        args=json.dumps([model._meta.app_label, model_name, history_instance.pk]),
                                        one_off=True
                                    )
                                    
                                    meta_instance.meta_task_name = task_unique_name
                                    meta_instance.meta_task_status = "SCHEDULED"
                                    meta_instance.save(update_fields=["meta_task_name", "meta_task_status"])
                                    
                                    logger.info(f"Scheduled Activation for {history_instance.valid_from} (Task: {task_unique_name})")

                        except ImportError:
                            logger.warning("django-celery-beat not installed. Skipping Event Scheduling.")
                        except Exception as e:
                             logger.error(f"Error creating meta history / scheduling for {sender.__name__}: {e}", exc_info=True)

                def maintain_valid_period(sender, instance=None, history_instance=None, **kwargs):
                    """
                    Maintain valid_to field for Historical Model using Strict Chaining.
                    valid_to is ALWAYS inferred as the valid_from of the next record.
                    """
                    history_instance = history_instance or instance
                    if sender == historical_model and history_instance:
                        HistoryModel = history_instance.__class__
                        pk_name = model._meta.pk.name
                        pk_val = getattr(history_instance, pk_name)
                        
                        # 1. Fetch ALL records for this Business Key (Standard History)
                        all_records = list(HistoryModel.objects.filter(
                            **{pk_name: pk_val}
                        ).order_by('valid_from', 'history_id'))
                        
                        # 2. Iterate and re-chain
                        for i, record in enumerate(all_records):
                            # The 'next' record defines the end of the 'current' record
                            next_record = all_records[i+1] if i < len(all_records) - 1 else None
                            
                            new_valid_to = next_record.valid_from if next_record else None
                            
                            # Update if changed
                            if record.valid_to != new_valid_to:
                                # Only perform IN-PLACE update if we are refining a known end date (Value -> Value)
                                # If we are closing an open record (None -> Value) or re-opening (Value -> None), use Standard History.
                                is_refinement = (record.valid_to is not None) and (new_valid_to is not None)
                                
                                record.valid_to = new_valid_to
                                # Prevent infinite recursion if we are saving the record triggering this signal
                                record._strict_chaining_update = is_refinement
                                record.save(update_fields=['valid_to'])
                                
                # Connect signals for Level 1 (Historical Model)
                # Disconnect first to avoid duplicates if re-registering
                post_create_historical_record.disconnect(trigger_meta_history, sender=historical_model)
                post_create_historical_record.disconnect(maintain_valid_period, sender=historical_model)
                
                # Use post_save for triggering meta history to catch updates (e.g. valid_from changes)
                from django.db.models.signals import post_save
                post_save.disconnect(trigger_meta_history, sender=historical_model)
                post_save.connect(trigger_meta_history, sender=historical_model, weak=False)
                
                post_save.disconnect(maintain_valid_period, sender=historical_model)
                post_save.connect(maintain_valid_period, sender=historical_model, weak=False)
                
                # Also support post_create_historical_record for standard flow
                post_create_historical_record.connect(maintain_valid_period, sender=historical_model, weak=False)

                # --- SYNCHRONIZATION: Update Main Table from History ---
                from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer
                
                def synchronize_main_model(sender, instance, **kwargs):
                    """
                    Ensure the Main Table reflects the History Record that is valid 'Right Now'.
                    Triggered when History is modified.
                    """
                    MainModel = model
                    pk_name = MainModel._meta.pk.name
                    pk_val = getattr(instance, pk_name)
                    
                    # Delegate to Service
                    BitemporalSynchronizer.sync_record_for_model(MainModel, pk_val, sender)
                              
                # Connect Synchronization Signal
                post_save.disconnect(synchronize_main_model, sender=historical_model)
                post_save.connect(synchronize_main_model, sender=historical_model, weak=False)
                # We might also want to hook into post_delete of history? 
                # If the effective record is deleted, main table should revert to previous? 
                # For now assume updates/inserts.
                from django.db.models.signals import pre_delete
                
                def cleanup_schedules_pre_delete(sender, instance, **kwargs):
                    """
                    Handle cleanup of schedules BEFORE deletion (while relationships exist).
                    """
                    HistoryModel = sender
                    if not issubclass(HistoryModel, models.Model): return
                    
                    # CANCEL SCHEDULE
                    try:
                        MetaModel = sender.meta_history.model
                        existing_schedules = MetaModel.objects.filter(
                            history_object_id=instance.pk,
                            meta_task_status="SCHEDULED"
                        )
                        from django_celery_beat.models import PeriodicTask
                        for meta_log in existing_schedules:
                             if meta_log.meta_task_name:
                                 PeriodicTask.objects.filter(name=meta_log.meta_task_name).delete()
                                 logger.info(f"Revoked Task (Deletion): {meta_log.meta_task_name}")
                             meta_log.meta_task_status = "CANCELLED"
                             meta_log.save(update_fields=["meta_task_status"])
                    except Exception:
                        pass 

                def repair_chain_post_delete(sender, instance, **kwargs):
                    """
                    Repair Validity Stubs AFTER deletion.
                    A -> B -> C. Delete B.
                    Post-Delete: A valid_to should extend to C valid_from.
                    """
                    HistoryModel = sender
                    if not issubclass(HistoryModel, models.Model): return

                    pk_name = model._meta.pk.name
                    pk_val = getattr(instance, pk_name)
                    
                    # 1. Find the PREVIOUS record
                    previous_record = HistoryModel.objects.filter(
                        **{pk_name: pk_val},
                        valid_from__lt=instance.valid_from
                    ).order_by('-valid_from').first()
                    
                    if previous_record:
                        # 2. Find the NEXT record
                        next_record = HistoryModel.objects.filter(
                            **{pk_name: pk_val},
                            valid_from__gt=instance.valid_from
                        ).order_by('valid_from').first()
                        
                        new_valid_to = next_record.valid_from if next_record else None
                        
                        # 3. Update Previous Record
                        # Since B is already gone, maintain_valid_period will see A->C and agree with this change.
                        previous_record.valid_to = new_valid_to
                        previous_record.save(update_fields=['valid_to'])
                            
                pre_delete.disconnect(cleanup_schedules_pre_delete, sender=historical_model)
                pre_delete.connect(cleanup_schedules_pre_delete, sender=historical_model, weak=False)
                
                from django.db.models.signals import post_delete
                post_delete.disconnect(repair_chain_post_delete, sender=historical_model)
                post_delete.connect(repair_chain_post_delete, sender=historical_model, weak=False)

                # 4. Meta History Model logic
                meta_historical_model = historical_model.meta_history.model

                # --- METADATA SYSTEM TIME MAINTENANCE ---
                from django.db.models.signals import post_save
                
                def maintain_sys_period(sender, instance, **kwargs):
                    """
                    Maintain sys_to field for Meta-History Model using Strict Chaining.
                    sys_to is ALWAYS inferred as the sys_from of the next record.
                    """
                    # Ensure we are handling the Meta Model
                    if sender == meta_historical_model:
                         MetaModel = instance.__class__
                         history_object_id = instance.history_object_id
                         
                         # 1. Fetch ALL meta records for this SPECIFIC History Row
                         all_meta = list(MetaModel.objects.filter(
                             history_object_id=history_object_id
                         ).order_by('sys_from', 'id')) # Using id as tiebreaker
                         
                         # 2. Iterate and re-chain
                         for i, record in enumerate(all_meta):
                             next_record = all_meta[i+1] if i < len(all_meta) - 1 else None
                             
                             new_sys_to = next_record.sys_from if next_record else None
                             
                             if record.sys_to != new_sys_to:
                                 record.sys_to = new_sys_to
                                 record.save(update_fields=['sys_to'])

                # Connect signal for Level 2 (Meta History Model)
                post_save.disconnect(maintain_sys_period, sender=meta_historical_model)
                post_save.connect(maintain_sys_period, sender=meta_historical_model, weak=False)

                processAdminSite.register([meta_historical_model])

                if is_already_tracked:
                    print(f"✓ History hooks attached to existing history for: {model.__name__}")
                else:
                    print(f"✓ History and Meta-History enabled for: {model.__name__}")
            except Exception as e:
                # Print exception type for clarity
                print(f"⚠ Failed to register history for {model.__name__}: {type(e).__name__} - {e}")
        else:
            exclusion_reason = get_model_exclusion_reason(model)
            if exclusion_reason:
                print(f"⊘ History tracking skipped for {model.__name__}: {exclusion_reason}")
            elif model_name in untracked_models:
                print(f"⊘ History tracking skipped for {model.__name__}: In untracked_models list")

        if not adminSite.is_registered(model):
            try:
                adminSite.register([model])
            except admin.exceptions.AlreadyRegistered:
                pass

    @classmethod
    def _handle_calculation_model_reset(cls, model: Type[models.Model]) -> None:
        """
        Handle resetting of CalculationModel instances with aborted calculations.
        
        This method resets any CalculationModel instances that were left in IN_PROGRESS state
        when the application starts, marking them as ABORTED if Celery is not active.
        
        Args:
            model: CalculationModel class to handle reset for
        """
        from lex.core.models.calculation_model import CalculationModel
        
        if not os.getenv("CALLED_FROM_START_COMMAND"):
            return
            
        @sync_to_async
        def reset_instances_with_aborted_calculations():
            """Reset calculation instances that were left in progress."""
            # if not os.getenv("CELERY_ACTIVE"):
            aborted_calc_instances = model.objects.filter(is_calculated=CalculationModel.IN_PROGRESS)
            aborted_calc_instances.update(is_calculated=CalculationModel.ABORTED)

        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(reset_instances_with_aborted_calculations())

    @classmethod
    def register_model_structure(cls, structure: dict):
        from lex.process_admin.settings import processAdminSite
        if structure: processAdminSite.register_model_structure(structure)

    @classmethod
    def register_model_styling(cls, styling: dict):
        from lex.process_admin.settings import processAdminSite
        if styling: processAdminSite.register_model_styling(styling)

    @classmethod
    def register_widget_structure(cls, structure):
        from lex.process_admin.settings import processAdminSite
        if structure: processAdminSite.register_widget_structure(structure)
