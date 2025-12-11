import logging
from abc import ABCMeta

import streamlit as st
from typing import Set, Dict, Any, Union, Optional
from dataclasses import dataclass
from typing import FrozenSet, Optional, Mapping, Any, Literal

from django.db import models, transaction
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE, BEFORE_SAVE, AFTER_SAVE
from lex.api.utils import operation_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserContext:
    """Clean user context for authorization methods"""
    user: Any  # Django User instance
    email: str
    is_authenticated: bool
    is_superuser: bool
    groups: Set[str]
    keycloak_scopes: Set[str]  # Available Keycloak scopes for this resource
    
    @classmethod
    def from_request(cls, request, instance=None):
        """Create UserContext from Django request"""
        if not request or not hasattr(request, 'user'):
            return cls.anonymous()
        
        user = request.user
        keycloak_scopes = set()
        
        # Extract Keycloak scopes if available
        if hasattr(request, 'user_permissions') and instance:
            resource_name = f"{instance._meta.app_label}.{instance.__class__.__name__}"
            for perm in request.user_permissions:
                if perm.get("rsname") == resource_name:
                    if instance.pk and str(instance.pk) == perm.get("resource_set_id"):
                        keycloak_scopes.update(perm.get("scopes", []))
                    elif perm.get("resource_set_id") is None:
                        keycloak_scopes.update(perm.get("scopes", []))
        
        return cls(
            user=user,
            email=getattr(user, 'email', ''),
            is_authenticated=user.is_authenticated,
            is_superuser=getattr(user, 'is_superuser', False),
            groups=set(user.groups.values_list('name', flat=True)) if hasattr(user, 'groups') else set(),
            keycloak_scopes=keycloak_scopes
        )
    
    @classmethod
    def anonymous(cls):
        """Create anonymous user context"""
        return cls(
            user=None,
            email='',
            is_authenticated=False,
            is_superuser=False,
            groups=set(),
            keycloak_scopes=set()
        )


@dataclass(frozen=True)
class PermissionResult:
    """Result of permission check with flexible field-level granularity"""
    allowed: bool
    fields: Optional[Set[str]] = None  # None means all fields, empty set means no fields
    excluded_fields: Optional[Set[str]] = None  # Fields to exclude from selection
    reason: Optional[str] = None
    
    @classmethod
    def allow_all(cls, reason: str = None):
        """Allow access to all fields"""
        return cls(allowed=True, fields=None, excluded_fields=None, reason=reason)
    
    @classmethod
    def allow_fields(cls, fields: Union[Set[str], list], reason: str = None):
        """Allow access to specific fields"""
        field_set = set(fields) if isinstance(fields, list) else fields
        return cls(allowed=True, fields=field_set, excluded_fields=None, reason=reason)
    
    @classmethod
    def allow_all_except(cls, excluded_fields: Union[Set[str], list], reason: str = None):
        """Allow access to all fields except specified ones"""
        excluded_set = set(excluded_fields) if isinstance(excluded_fields, list) else excluded_fields
        return cls(allowed=True, fields=None, excluded_fields=excluded_set, reason=reason)
    
    @classmethod
    def deny(cls, reason: str = None):
        """Deny access"""
        return cls(allowed=False, fields=set(), excluded_fields=None, reason=reason)
    
    @classmethod
    def deny_all(cls, reason: str = None):
        """Explicitly deny all fields (same as deny but more explicit)"""
        return cls(allowed=False, fields=set(), excluded_fields=None, reason=reason)
    
    def get_fields(self, all_field_names: Set[str]) -> Set[str]:
        """Get the actual field names, resolving patterns to concrete fields"""
        if not self.allowed:
            return set()
        
        # If specific fields are set, use those
        if self.fields is not None:
            return self.fields & all_field_names  # Intersection to ensure valid fields
        
        # If no specific fields but we have exclusions, return all except excluded
        if self.excluded_fields is not None:
            # Safety check: ensure excluded_fields is a set
            if isinstance(self.excluded_fields, str):
                excluded_set = {self.excluded_fields}
            elif isinstance(self.excluded_fields, (list, tuple)):
                excluded_set = set(self.excluded_fields)
            else:
                excluded_set = self.excluded_fields
            return all_field_names - excluded_set
        
        # Default: all fields
        return all_field_names
    
    def __str__(self):
        """String representation for debugging"""
        if not self.allowed:
            return f"DENIED: {self.reason or 'No reason given'}"
        
        if self.fields is not None:
            field_list = sorted(self.fields) if self.fields else []
            return f"ALLOWED fields {field_list}: {self.reason or 'No reason given'}"
        
        if self.excluded_fields is not None:
            excluded_list = sorted(self.excluded_fields) if self.excluded_fields else []
            return f"ALLOWED all except {excluded_list}: {self.reason or 'No reason given'}"
        
        return f"ALLOWED all fields: {self.reason or 'No reason given'}"


class LexModel(LifecycleModel):
    """
    Abstract base model with clean, user-friendly authorization system.
    
    ## Authorization Methods (Override these in your models):
    
    **Field-Level Methods** (return PermissionResult):
    - `permission_read(user_context)` - Controls which fields user can view
    - `permission_edit(user_context)` - Controls which fields user can modify  
    - `permission_export(user_context)` - Controls which fields user can export
    
    **Action-Level Methods** (return bool):
    - `permission_create(user_context)` - Can user create new instances?
    - `permission_delete(user_context)` - Can user delete this instance?
    - `permission_list(user_context)` - Can user list instances of this model?
    
    ## Example Usage:
    ```python
    class MyModel(LexModel):
        sensitive_field = models.CharField(max_length=100)
        
        def permission_read(self, user_context):
            if user_context.is_superuser:
                return PermissionResult.allow_all("Superuser access")
            
            if 'admin' in user_context.groups:
                return PermissionResult.allow_all("Admin group access")
            
            # Regular users can't see sensitive data
            allowed_fields = {'id', 'name', 'created_at'}
            return PermissionResult.allow_fields(allowed_fields, "Regular user")
        
        def permission_delete(self, user_context):
            return user_context.is_superuser or 'admin' in user_context.groups
    ```
    
    ## Keycloak Integration:
    If you don't override these methods, they fall back to Keycloak scopes.
    You can also use `user_context.keycloak_scopes` in your custom logic.
    """

    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True
        app_label = 'core'

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
            from lex.core.exceptions import ValidationError
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
            from lex.core.exceptions import ValidationError
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
                    from lex.core.exceptions import ValidationError
                    raise ValidationError(
                        f"Rollback failed: {rollback_error}. Original error: {original_error}"
                    ) from rollback_error

        except Exception as transaction_error:
            logger.error(f"Transaction error during rollback: {transaction_error}")
            from lex.core.exceptions import ValidationError
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

    # =============================================================================
    # AUTHORIZATION METHODS - Override these in your models for custom logic
    # =============================================================================
    
    def permission_read(self, user_context: UserContext) -> PermissionResult:
        """
        Override this method to control which fields users can read.
        
        Args:
            user_context: Clean user information and Keycloak scopes
            
        Returns:
            PermissionResult with allowed fields
            
        Default: Uses Keycloak 'read' scope for all fields
        """

        if "read" in user_context.keycloak_scopes:
            return PermissionResult.allow_all("Keycloak read scope")
        return PermissionResult.deny("No read permission")

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        """
        Override this method to control which fields users can edit.

        Args:
            user_context: Clean user information and Keycloak scopes

        Returns:
            PermissionResult with editable fields

        Default: Uses Keycloak 'edit' scope for all fields
        """
        if "edit" in user_context.keycloak_scopes:
            return PermissionResult.allow_all("Keycloak edit scope")
        return PermissionResult.deny("No edit permission")

    def permission_export(self, user_context: UserContext) -> PermissionResult:
        """
        Override this method to control which fields users can export.

        Args:
            user_context: Clean user information and Keycloak scopes

        Returns:
            PermissionResult with exportable fields

        Default: Uses Keycloak 'export' scope for all fields
        """
        if "export" in user_context.keycloak_scopes:
            return PermissionResult.allow_all("Keycloak export scope")
        return PermissionResult.deny("No export permission")

    def permission_create(self, user_context: UserContext) -> bool:
        """
        Override this method to control if users can create instances.

        Args:
            user_context: Clean user information and Keycloak scopes

        Returns:
            True if user can create instances

        Default: Uses Keycloak 'create' scope
        """
        return "create" in user_context.keycloak_scopes

    def permission_delete(self, user_context: UserContext) -> bool:
        """
        Override this method to control if users can delete this instance.

        Args:
            user_context: Clean user information and Keycloak scopes

        Returns:
            True if user can delete this instance

        Default: Uses Keycloak 'delete' scope
        """
        return "delete" in user_context.keycloak_scopes

    def permission_list(self, user_context: UserContext) -> bool:
        """
        Override this method to control if users can list instances.

        Args:
            user_context: Clean user information and Keycloak scopes

        Returns:
            True if user can list instances

        Default: Uses Keycloak 'list' scope
        """
        return "list" in user_context.keycloak_scopes

    # =============================================================================
    # INTERNAL METHODS - Used by framework, don't override these
    # =============================================================================

    def _get_all_field_names(self) -> Set[str]:
        """Get all field names for this model"""
        return {f.name for f in self._meta.fields}

    def _create_user_context(self, request) -> UserContext:
        """Create UserContext from request"""
        return UserContext.from_request(request, self)

    # =============================================================================
    # CONVENIENCE METHODS - Shortcuts for common permission patterns
    # =============================================================================

    def allow_all_if_superuser(self, user_context: UserContext, reason: str = "Superuser access") -> Optional[PermissionResult]:
        """Helper: Allow all fields if user is superuser, otherwise return None"""
        if user_context.is_superuser:
            return PermissionResult.allow_all(reason)
        return None

    def allow_all_if_in_groups(self, user_context: UserContext, groups: Union[str, Set[str]], reason: str = None) -> Optional[PermissionResult]:
        """Helper: Allow all fields if user is in specified groups, otherwise return None"""
        if isinstance(groups, str):
            groups = {groups}

        if user_context.groups & groups:  # Intersection check
            reason = reason or f"User in groups: {', '.join(groups)}"
            return PermissionResult.allow_all(reason)
        return None

    def allow_fields_if_owner(self, user_context: UserContext, owner_field: str = 'owner', fields: Union[Set[str], list] = None, excluded_fields: Union[Set[str], list] = None, reason: str = None) -> Optional[PermissionResult]:
        """Helper: Allow fields if user owns this record, otherwise return None"""
        if not user_context.is_authenticated:
            return None

        owner = getattr(self, owner_field, None)
        if owner == user_context.user:
            reason = reason or "Record owner"
            if fields is not None:
                return PermissionResult.allow_fields(fields, reason)
            elif excluded_fields is not None:
                return PermissionResult.allow_all_except(excluded_fields, reason)
            else:
                return PermissionResult.allow_all(reason)
        return None

    def keycloak_fallback(self, user_context: UserContext, scope: str) -> PermissionResult:
        """Helper: Use Keycloak scope as fallback permission"""
        if scope in user_context.keycloak_scopes:
            return PermissionResult.allow_all(f"Keycloak {scope} scope")
        return PermissionResult.deny(f"No {scope} permission")

    def allow_all_except_sensitive(self, user_context: UserContext, sensitive_fields: Union[Set[str], list] = None, reason: str = None) -> PermissionResult:
        """Helper: Allow all fields except sensitive ones (common pattern)"""
        if sensitive_fields is None:
            sensitive_fields = {'password', 'social_security', 'ssn', 'credit_card', 'bank_account'}
        return PermissionResult.allow_all_except(sensitive_fields, reason or "Excluding sensitive fields")

    def allow_public_fields(self, user_context: UserContext, reason: str = None) -> PermissionResult:
        """Helper: Allow only commonly public fields"""
        public_fields = {'id', 'name', 'title', 'description', 'created_at', 'updated_at'}
        return PermissionResult.allow_fields(public_fields, reason or "Public fields only")

    def allow_basic_fields(self, user_context: UserContext, reason: str = None) -> PermissionResult:
        """Helper: Allow basic identifying fields"""
        basic_fields = {'id', 'name', 'email', 'created_at'}
        return PermissionResult.allow_fields(basic_fields, reason or "Basic fields only")

    # Legacy methods for backward compatibility - these call the new permission methods
    def can_read(self, request) -> Set[str]:
        """Legacy method - use permission_read instead"""
        user_context = self._create_user_context(request)
        result = self.permission_read(user_context)
        return result.get_fields(self._get_all_field_names())

    def can_edit(self, request) -> Set[str]:
        """Legacy method - use permission_edit instead"""
        user_context = self._create_user_context(request)
        result = self.permission_edit(user_context)
        return result.get_fields(self._get_all_field_names())

    def can_export(self, request) -> Set[str]:
        """Legacy method - use permission_export instead"""
        user_context = self._create_user_context(request)
        result = self.permission_export(user_context)
        return result.get_fields(self._get_all_field_names())

    def can_create(self, request) -> bool:
        """Legacy method - use permission_create instead"""
        user_context = self._create_user_context(request)
        return self.permission_create(user_context)

    def can_delete(self, request) -> bool:
        """Legacy method - use permission_delete instead"""
        user_context = self._create_user_context(request)
        return self.permission_delete(user_context)

    def can_list(self, request) -> bool:
        """Legacy method - use permission_list instead"""
        user_context = self._create_user_context(request)
        return self.permission_list(user_context)

    
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