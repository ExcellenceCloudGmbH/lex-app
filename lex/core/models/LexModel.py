import logging
from datetime import datetime
from abc import ABCMeta
from contextlib import nullcontext

import streamlit as st
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Literal, Mapping, Optional, Set, Union

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE, BEFORE_SAVE, AFTER_SAVE, BEFORE_CREATE, \
    BEFORE_UPDATE
try:
    from django_lifecycle.mixins import LifecycleModelMixin, _bypass_state as lifecycle_bypass_state
except ImportError:
    from django_lifecycle.mixins import LifecycleModelMixin

    lifecycle_bypass_state = None

try:
    from api.utils import operation_context
except ImportError:
    from lex.api.utils import operation_context

logger = logging.getLogger(__name__)


def lex_datetime_now() -> datetime:
    """Return a datetime.now()-based timestamp compatible with Django timezone settings."""
    if getattr(settings, "USE_TZ", False):
        return timezone.now()

    return datetime.now(timezone.get_default_timezone()).replace(tzinfo=None)


def should_use_atomic_model_operations(model_or_class) -> bool:
    return not (
        hasattr(model_or_class, "is_atomic")
        and not getattr(model_or_class, "is_atomic")
    )


@dataclass(frozen=True)
class UserContext:
    """Clean user context for authorization methods.

    Note:
        `user_permissions` here represents Keycloak UMA permissions taken from
        `request.user_permissions` (middleware), not Django's built-in
        `user.user_permissions` many-to-many relation.
    """
    user: Any  # Django User instance
    email: str
    is_authenticated: bool
    is_superuser: bool
    groups: Set[str]
    keycloak_scopes: Set[str]  # Available Keycloak scopes for this resource
    user_permissions: tuple[Mapping[str, Any], ...] = tuple()
    client_roles: FrozenSet[str] = frozenset()

    @staticmethod
    def _normalize_permissions(raw_permissions: Any) -> tuple[Mapping[str, Any], ...]:
        if not raw_permissions:
            return tuple()
        if not isinstance(raw_permissions, (list, tuple, set, frozenset)):
            return tuple()
        normalized = []
        for perm in raw_permissions:
            if isinstance(perm, Mapping):
                normalized.append(dict(perm))
        return tuple(normalized)

    @staticmethod
    def _normalize_roles(raw_roles: Any) -> FrozenSet[str]:
        if raw_roles is None:
            return frozenset()
        if isinstance(raw_roles, str):
            return frozenset({raw_roles}) if raw_roles else frozenset()
        if isinstance(raw_roles, Mapping):
            role_set = set()
            for value in raw_roles.values():
                role_set.update(UserContext._normalize_roles(value))
            return frozenset(role_set)
        if isinstance(raw_roles, (list, tuple, set, frozenset)):
            role_set = set()
            for value in raw_roles:
                role_set.update(UserContext._normalize_roles(value))
            return frozenset(role_set)
        return frozenset()

    @classmethod
    def _extract_client_roles(cls, request, user) -> FrozenSet[str]:
        role_sources = []

        request_userinfo = getattr(request, "userinfo", None)
        if isinstance(request_userinfo, Mapping):
            role_sources.append(request_userinfo.get("client_roles"))

        session = getattr(request, "session", None)
        if session:
            session_userinfo = session.get("oidc_userinfo")
            if isinstance(session_userinfo, Mapping):
                role_sources.append(session_userinfo.get("client_roles"))

        role_sources.append(getattr(request, "client_roles", None))
        role_sources.append(getattr(user, "client_roles", None))
        role_sources.append(getattr(user, "roles", None))

        roles = set()
        for role_source in role_sources:
            roles.update(cls._normalize_roles(role_source))
        return frozenset(roles)

    @staticmethod
    def _resolve_keycloak_scopes(
        user_permissions: tuple[Mapping[str, Any], ...],
        instance=None,
        original_instance=None,
    ) -> Set[str]:
        keycloak_scopes = set()
        if not instance:
            return keycloak_scopes

        resource_name = f"{instance._meta.app_label}.{instance.__class__.__name__}"
        instance_pk = str(instance.pk) if getattr(instance, "pk", None) else None

        # Build set of resource names to match against.  When the instance
        # was unwrapped from a historical record the Keycloak permissions
        # may be registered under the *historical* resource name
        # (e.g. "core.HistoricalQuarter") rather than the main model name
        # ("core.Quarter").  Check both so scopes resolve either way.
        resource_names = {resource_name}
        if original_instance is not None and original_instance is not instance:
            original_name = (
                f"{original_instance._meta.app_label}."
                f"{original_instance.__class__.__name__}"
            )
            resource_names.add(original_name)

        for perm in user_permissions:
            if perm.get("rsname") not in resource_names:
                continue
            resource_set_id = perm.get("resource_set_id")
            if instance_pk and resource_set_id is not None and instance_pk == str(resource_set_id):
                keycloak_scopes.update(perm.get("scopes", []))
            elif resource_set_id is None:
                keycloak_scopes.update(perm.get("scopes", []))
        return keycloak_scopes

    @staticmethod
    def _enrich_user(user, user_permissions: tuple[Mapping[str, Any], ...], client_roles: FrozenSet[str]) -> None:
        # Keep this best-effort and non-fatal; user object mutability depends on backend.
        try:
            setattr(user, "keycloak_user_permissions", user_permissions)
        except Exception:
            pass
        try:
            setattr(user, "client_roles", sorted(client_roles))
        except Exception:
            pass
        try:
            setattr(user, "roles", sorted(client_roles))
        except Exception:
            pass
    
    @classmethod
    def _api_key_context(cls, request):
        if not request:
            return None

        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            return None

        try:
            from api.utils.api_key_requests import get_api_key_request_identity
        except ImportError:
            from lex.api.utils.api_key_requests import get_api_key_request_identity

        identity = get_api_key_request_identity(request)
        if not identity:
            return None

        return cls(
            user=identity.user,
            email=identity.email,
            is_authenticated=True,
            is_superuser=False,
            groups=set(),
            keycloak_scopes=set(identity.scopes),
            user_permissions=tuple(),
            client_roles=frozenset({"api_key"}),
        )

    @classmethod
    def from_request(cls, request, instance=None):
        """Create UserContext from Django request"""
        api_key_context = cls._api_key_context(request)
        if api_key_context is not None:
            return api_key_context

        if not request or not hasattr(request, 'user'):
            return cls.anonymous()
        
        user = request.user
        user_permissions = cls._normalize_permissions(getattr(request, "user_permissions", ()))
        keycloak_scopes = cls._resolve_keycloak_scopes(user_permissions, instance)
        client_roles = cls._extract_client_roles(request, user)
        cls._enrich_user(user, user_permissions, client_roles)
        
        return cls(
            user=user,
            email=getattr(user, 'email', ''),
            is_authenticated=user.is_authenticated,
            is_superuser=getattr(user, 'is_superuser', False),
            groups=set(user.groups.values_list('name', flat=True)) if hasattr(user, 'groups') else set(),
            keycloak_scopes=keycloak_scopes,
            user_permissions=user_permissions,
            client_roles=client_roles,
        )
    
    @classmethod
    def from_request_base(cls, request):
        """Create a base UserContext without instance-specific keycloak scopes.
        Use with_instance() to efficiently add scopes for each record."""
        api_key_context = cls._api_key_context(request)
        if api_key_context is not None:
            return api_key_context

        if not request or not hasattr(request, 'user'):
            return cls.anonymous()
        
        user = request.user
        user_permissions = cls._normalize_permissions(getattr(request, "user_permissions", ()))
        client_roles = cls._extract_client_roles(request, user)
        cls._enrich_user(user, user_permissions, client_roles)
        return cls(
            user=user,
            email=getattr(user, 'email', ''),
            is_authenticated=user.is_authenticated,
            is_superuser=getattr(user, 'is_superuser', False),
            groups=set(user.groups.values_list('name', flat=True)) if hasattr(user, 'groups') else set(),
            keycloak_scopes=frozenset(),
            user_permissions=user_permissions,
            client_roles=client_roles,
        )

    def with_instance(self, request, instance, original_instance=None):
        """Create a new UserContext with keycloak scopes resolved for the given instance.
        Reuses cached user/email/groups/is_superuser from the base context.

        Args:
            request: The Django request.
            instance: The (possibly unwrapped) model instance to resolve scopes for.
            original_instance: The pre-unwrap instance (e.g. HistoricalQuarter) so
                that keycloak scopes registered under the historical resource name
                are also matched.
        """
        keycloak_scopes = self._resolve_keycloak_scopes(
            self.user_permissions, instance, original_instance=original_instance,
        )
        
        return UserContext(
            user=self.user,
            email=self.email,
            is_authenticated=self.is_authenticated,
            is_superuser=self.is_superuser,
            groups=self.groups,
            keycloak_scopes=keycloak_scopes,
            user_permissions=self.user_permissions,
            client_roles=self.client_roles,
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
            keycloak_scopes=set(),
            user_permissions=tuple(),
            client_roles=frozenset(),
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

class LexManager(models.Manager):
    """Custom manager for LexModel that ensures history tracking.

    Overrides ``bulk_create`` to use individual ``save()`` calls wrapped
    in ``transaction.atomic()``.  This guarantees that Django's
    ``post_save`` signal fires for every object, so simple_history and
    the full bitemporal signal chain (meta-history, valid_to chaining,
    scheduling) are triggered automatically.

    Pass ``skip_history=True`` to fall back to Django's native
    ``bulk_create`` when raw performance is required and history
    tracking is not needed (e.g. large data migrations).
    """

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False,
                    update_conflicts=False, update_fields=None,
                    unique_fields=None, skip_history=False):
        if skip_history:
            return super().bulk_create(
                objs,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )

        created = []
        for obj in objs:
            with transaction.atomic():
                obj.save()
                created.append(obj)

        logger.info(
            "LexManager.bulk_create: saved %d %s objects with history tracking",
            len(created),
            self.model.__name__,
        )
        return created


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

    objects = LexManager()
    FALLBACK_AUDIT_ACTOR = 'Initial Data Upload'
    API_KEY_AUDIT_ACTOR = 'Technical User'

    created_at = models.DateTimeField(default=lex_datetime_now, editable=False)
    edited_at = models.DateTimeField(default=lex_datetime_now, editable=False)
    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True
        app_label = 'core'

    def __init__(self, *args, **kwargs):
        self._explicit_timestamp_overrides = {
            'created_at': kwargs.get('created_at') is not None,
            'edited_at': kwargs.get('edited_at') is not None,
        }
        super().__init__(*args, **kwargs)
        self._pre_validation_snapshot = None
        self._validation_in_progress = False

    def _should_use_atomic_save(self) -> bool:
        return should_use_atomic_model_operations(self)

    def save(self, *args, **kwargs):
        """
        Mirror django-lifecycle's hook-aware save flow while preserving the
        legacy ``is_atomic = False`` opt-out used by CalculationModel subclasses.
        """
        base_save = super(LifecycleModelMixin, self).save
        atomic_context = transaction.atomic() if self._should_use_atomic_save() else nullcontext()

        with atomic_context:
            skip_hooks = kwargs.pop("skip_hooks", False)
            skip_hooks_from_cm = (
                lifecycle_bypass_state.is_bypassed_for(self.__class__)
                if lifecycle_bypass_state is not None
                else False
            )
            if skip_hooks or skip_hooks_from_cm:
                return base_save(*args, **kwargs)

            self._clear_watched_fk_model_cache()
            is_new = self._state.adding

            if is_new:
                self._run_hooked_methods(BEFORE_CREATE, **kwargs)
            else:
                self._run_hooked_methods(BEFORE_UPDATE, **kwargs)

            self._run_hooked_methods(BEFORE_SAVE, **kwargs)
            result = base_save(*args, **kwargs)
            self._run_hooked_methods(AFTER_SAVE, **kwargs)

            if is_new:
                self._run_hooked_methods(AFTER_CREATE, **kwargs)
            else:
                self._run_hooked_methods(AFTER_UPDATE, **kwargs)

            transaction.on_commit(self._reset_initial_state)
            return result

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

    @staticmethod
    def _normalize_audit_actor(actor: Any) -> Optional[str]:
        if actor is None:
            return None
        actor_str = str(actor).strip()
        return actor_str or None

    @staticmethod
    def _get_operation_context() -> Dict[str, Any]:
        context = operation_context.get()
        return context if isinstance(context, dict) else {}

    def _resolve_request_audit_actor(self, request) -> Optional[str]:
        if not request:
            return None

        user = getattr(request, 'user', None)
        if user is not None:
            is_authenticated = getattr(user, 'is_authenticated', None)
            if is_authenticated is not False:
                return self._normalize_audit_actor(user)

        try:
            try:
                from api.utils.api_key_requests import get_api_key_request_identity
            except ImportError:
                from lex.api.utils.api_key_requests import get_api_key_request_identity

            api_key_identity = get_api_key_request_identity(request)
        except Exception:
            api_key_identity = None

        if api_key_identity is not None:
            return self.API_KEY_AUDIT_ACTOR

        header_names = {
            str(header_name).lower()
            for header_name in (getattr(request, 'headers', {}) or {})
        }
        if 'api-key' in header_names:
            return self.API_KEY_AUDIT_ACTOR
        return None

    def _resolve_audit_actor(self) -> str:
        context = self._get_operation_context()
        explicit_actor = self._normalize_audit_actor(context.get('actor'))
        if explicit_actor:
            return explicit_actor

        request_actor = self._resolve_request_audit_actor(context.get('request_obj'))
        if request_actor:
            return request_actor

        return self.FALLBACK_AUDIT_ACTOR

    def _has_explicit_created_by_override(self) -> bool:
        return self._normalize_audit_actor(self.created_by) is not None

    def _has_explicit_created_at_override(self) -> bool:
        return self._explicit_timestamp_overrides.get('created_at', False) and self.created_at is not None

    def _has_explicit_edited_by_override(self) -> bool:
        return self.has_changed('edited_by') and self._normalize_audit_actor(self.edited_by) is not None

    def _has_explicit_edited_at_override(self) -> bool:
        return self.has_changed('edited_at') and self.edited_at is not None

    @hook(BEFORE_CREATE)
    def update_timestamps_on_create(self):
        # Skip if we are syncing from history (bitemporal sync)
        if getattr(self, 'skip_history_when_saving', False):
            return

        current_timestamp = lex_datetime_now()

        if not self._has_explicit_created_at_override():
            self.created_at = current_timestamp

        if not self._explicit_timestamp_overrides.get('edited_at', False):
            self.edited_at = current_timestamp

    @hook(BEFORE_UPDATE)
    def update_edited_at(self):
        # Skip if we are syncing from history (bitemporal sync)
        if getattr(self, 'skip_history_when_saving', False):
            return

        if self._has_explicit_edited_at_override():
            return

        self.edited_at = lex_datetime_now()

    @hook(BEFORE_UPDATE)
    def update_edited_by(self):
        # Skip if we are syncing from history (bitemporal sync)
        if getattr(self, 'skip_history_when_saving', False):
            return

        if self._has_explicit_edited_by_override():
            return

        self.edited_by = self._resolve_audit_actor()

    @hook(BEFORE_CREATE)
    def update_created_by(self):
        # Skip if we are syncing from history (bitemporal sync)
        if getattr(self, 'skip_history_when_saving', False):
            return

        if self._has_explicit_created_by_override():
            return

        self.created_by = self._resolve_audit_actor()


    def track(self):
        if hasattr(self, 'skip_history_when_saving'):
            del self.skip_history_when_saving


    def untrack(self):
        self.skip_history_when_saving = True

    def save_without_historical_record(self, *args, **kwargs):
        self.skip_history_when_saving = True
        try:
            ret = self.save(*args, **kwargs)
        finally:
            del self.skip_history_when_saving
        return ret

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
        public_fields = {'id', 'name', 'title', 'description', 'created_at', 'edited_at', 'updated_at'}
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
