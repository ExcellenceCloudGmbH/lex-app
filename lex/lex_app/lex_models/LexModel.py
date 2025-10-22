import logging
from abc import ABCMeta

import streamlit as st
from typing import Set, Dict, Any
from dataclasses import dataclass
from typing import FrozenSet, Optional, Mapping, Any, Literal

from django.db import models, transaction
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE, BEFORE_SAVE, AFTER_SAVE
from lex.lex_app.rest_api.context import operation_context

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom validation error for rollback mechanism"""
    def __init__(self, message, original_exception=None, model_class=None):
        self.original_exception = original_exception
        self.model_class = model_class
        super().__init__(message)


Op = Literal["read", "edit", "export", "create", "delete", "list"]

@dataclass(frozen=True)
class PermissionResult:
    allowed: bool
    fields: FrozenSet[str] = frozenset()  # empty = no fields; for boolean ops stays empty
    reason: Optional[str] = None          # optional diagnostic
    explain: Optional[Mapping[str, Any]] = None  # for debugging/auditing


class LexModel(LifecycleModel):
    """
    An abstract base model that provides a flexible, override-driven permission system.

    Key Architectural Changes:
    - **`can_read` Returns Fields**: The `can_read` method is the source of truth
      for field-level security, returning a set of visible field names.
    - **`can_export` Returns Fields**: This method mirrors the `can_read` logic
      for data exports, returning a set of fields the user is allowed to export.
    - **Override Pattern**: All `can_*` methods are designed to be
      overridden in subclasses for custom business logic, with a fallback to
      Keycloak permissions.
    """

    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pre_validation_snapshot = None
        self._validation_in_progress = False

    def _capture_snapshot(self) -> Dict[str, Any]:
        """Capture current model field state for rollback"""
        snapshot = {}
        for field in self._meta.fields:
            snapshot[field.name] = getattr(self, field.name, None)
        return snapshot

    def _restore_from_snapshot(self, snapshot: Dict[str, Any]):
        """Restore model state from snapshot"""
        for field_name, value in snapshot.items():
            if hasattr(self, field_name):
                setattr(self, field_name, value)

    def post_validation(self):
        """
        Base post-validation method - called first in post_validation_hook.
        Override in subclasses for additional post-validation logic.
        Raise exception to trigger rollback.
        """
        pass

    def pre_validation(self):
        """
        Base pre-validation method - called first in pre_validation_hook.
        Override in subclasses for additional pre-validation logic.
        Raise exception to cancel save.
        """
        pass

    @hook(BEFORE_SAVE)
    def pre_validation_hook(self):
        """
        Execute pre-validation with cancel mechanism.
        Call order: LexModel.pre_validation() -> subclass.pre_validation()
        """
        if self._validation_in_progress:
            return  # Prevent recursion

        # Capture state before any validation
        self._pre_validation_snapshot = self._capture_snapshot()

        try:
            self._validation_in_progress = True

            # Always call base class pre_validation first
            LexModel.pre_validation(self)

            # Then call the overridden pre_validation in subclass
            self.pre_validation()

            logger.debug(f"Pre-validation successful for {self.__class__.__name__}")

        except Exception as e:
            logger.error(f"Pre-validation failed for {self.__class__.__name__}: {e}")
            # Cancel mechanism: prevent save operation
            raise ValidationError(
                f"Save cancelled - pre-validation failed: {e}",
                original_exception=e,
                model_class=self.__class__.__name__
            ) from e
        finally:
            self._validation_in_progress = False

    @hook(AFTER_SAVE)
    def post_validation_hook(self):
        """
        Execute post-validation with rollback mechanism.
        Call order: LexModel.post_validation() -> subclass.post_validation()
        """
        if self._validation_in_progress:
            return  # Prevent recursion during rollback

        try:
            self._validation_in_progress = True

            # Always call base class post_validation first
            LexModel.post_validation(self)

            # Then call the overridden post_validation in subclass
            self.post_validation()

            logger.debug(f"Post-validation successful for {self.__class__.__name__}")

        except Exception as e:
            logger.error(f"Post-validation failed for {self.__class__.__name__}: {e}")

            # Execute rollback mechanism
            self._execute_rollback(e)

            # Re-raise as ValidationError
            raise ValidationError(
                f"Post-validation failed and model was rolled back: {e}",
                original_exception=e,
                model_class=self.__class__.__name__
            ) from e
        finally:
            self._validation_in_progress = False

    def _execute_rollback(self, original_error):
        """Execute rollback to pre-validation state"""
        if not self._pre_validation_snapshot:
            logger.warning("No pre-validation snapshot available for rollback")
            return

        try:
            with transaction.atomic():
                savepoint = transaction.savepoint()

                try:
                    # Restore to pre-validation state
                    self._restore_from_snapshot(self._pre_validation_snapshot)

                    # Re-save with original state (skip hooks to prevent recursion)
                    self.save(skip_hooks=True)

                    # Commit the rollback
                    transaction.savepoint_commit(savepoint)
                    logger.info(f"Successfully rolled back {self.__class__.__name__} to pre-validation state")

                except Exception as rollback_error:
                    transaction.savepoint_rollback(savepoint)
                    logger.error(f"Rollback operation failed: {rollback_error}")
                    raise ValidationError(
                        f"Rollback failed: {rollback_error}. Original error: {original_error}"
                    ) from rollback_error

        except Exception as transaction_error:
            logger.error(f"Transaction error during rollback: {transaction_error}")
            raise ValidationError(
                f"Transaction error during rollback: {transaction_error}"
            ) from transaction_error

    @hook(AFTER_UPDATE)
    def update_edited_by(self):
        context = operation_context.get()
        # from lex_app.celery_tasks import print_context_state
        # print_context_state()
        if context and hasattr(context['request_obj'], 'user'):
            # self.edited_by = f"{context['request_obj'].user.first_name} {context['request_obj'].user.last_name} - {context['request_obj'].user.email}"
            self.edited_by = str(context['request_obj'].user)
        else:
            self.edited_by = 'Initial Data Upload'
        self.save(skip_hooks=True)

    @hook(AFTER_CREATE)
    def update_created_by(self):
        context = operation_context.get()
        logger.info(f"Request object: {context['request_obj']}")
        if context and hasattr(context['request_obj'], 'user'):
            # self.created_by = f"{context['request_obj'].user.first_name} {context['request_obj'].user.last_name} - {context['request_obj'].user.email}"
            self.created_by = str(context['request_obj'].user)
        else:
            self.created_by = 'Initial Data Upload'
        self.save(skip_hooks=True)


    def track(self):
        del self.skip_history_when_saving


    def untrack(self):
        self.skip_history_when_saving = True

    def authorize(self, op: Op, request) -> PermissionResult:
        scopes = self._get_keycloak_permissions(request)
        all_fields = frozenset(f.name for f in self._meta.fields)

        if op in ("read", "export", "edit"):
            key = {"read": "read", "export": "export", "edit": "edit"}[op]
            if key in scopes:
                return PermissionResult(True, all_fields)
            return PermissionResult(False, frozenset())
        elif op in ("create", "delete", "list"):
            key = {"create": "create", "delete": "delete", "list": "list"}[op]
            return PermissionResult(key in scopes)
        else:
            return PermissionResult(False)
    
    
    def allow_read(self, request) -> bool:
        return self.authorize("read", request).allowed

    def readable_fields(self, request) -> FrozenSet[str]:
        return self.authorize("read", request).fields

    def allow_edit(self, request) -> bool:
        return self.authorize("edit", request).allowed

    def editable_fields(self, request) -> FrozenSet[str]:
        return self.authorize("edit", request).fields

    def allow_export(self, request) -> bool:
        return self.authorize("export", request).allowed

    def exportable_fields(self, request) -> FrozenSet[str]:
        return self.authorize("export", request).fields

    def allow_create(self, request) -> bool:
        return self.authorize("create", request).allowed

    def allow_delete(self, request) -> bool:
        return self.authorize("delete", request).allowed

    def allow_list(self, request) -> bool:
        return self.authorize("list", request).allowed

    def _get_keycloak_permissions(self, request):
        """
        Private helper to get the cached UMA permissions for this model/instance
        from the request object.
        """
        if not request or not hasattr(request, 'user_permissions'):
            return set()

        resource_name = f"{self._meta.app_label}.{self.__class__.__name__}"
        all_perms = request.user_permissions

        model_scopes = set()
        record_scopes = set()

        for perm in all_perms:
            if perm.get("rsname") == resource_name:
                if self.pk and str(self.pk) == perm.get("resource_set_id"):
                    record_scopes.update(perm.get("scopes", []))
                elif perm.get("resource_set_id") is None:
                    model_scopes.update(perm.get("scopes", []))

        return record_scopes if record_scopes else model_scopes

    # --- Field-Level Permission Methods ---


    def can_read(self, request) -> Set[str]:
        """
        Determines which fields of this instance are visible to the current user.
        Consumed by the serializer to control API output.

        Returns: A set of visible field names.
        """
        record_scopes = self._get_keycloak_permissions(request)
        if "read" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()

    def can_export(self, request) -> Set[str]:
        """
        Determines which fields of this instance are exportable for the current user.
        Should be called by your data export logic.

        Returns: A set of exportable field names.
        """
        record_scopes = self._get_keycloak_permissions(request)
        if "export" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()

    # --- Action-Based Permission Methods ---

    def can_create(self, request) -> bool:
        """Checks for the 'create' scope in Keycloak."""
        # return True
        return "create" in self._get_keycloak_permissions(request)

    def can_edit(self, request) -> Set[str]:
        record_scopes = self._get_keycloak_permissions(request)
        if "edit" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()


    def can_delete(self, request) -> bool:
        """Checks for the 'delete' scope in Keycloak."""
        return "delete" in self._get_keycloak_permissions(request)
    #
    def can_list(self, request) -> bool:
        """Checks for the 'list' scope in Keycloak."""
        return "list" in self._get_keycloak_permissions(request)

    
    def streamlit_main(self, user=None):
        """
        Instance-level Streamlit visualization method.
        Override in subclasses for custom visualizations.
        """
        st.info("No instance-level visualization available for this model.")

    @classmethod
    def streamlit_class_main(cls):
        """
        Class-level Streamlit visualization method.
        Override in subclasses for aggregate visualizations, statistics, etc.
        """
        st.info("No class-level visualization available for this model.")
