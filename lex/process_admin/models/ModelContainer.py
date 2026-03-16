from typing import Set, Dict, Any, Optional, Type, Union
from django.db.models import Model
from lex.core.mixins.ModelModificationRestriction import (
    ModelModificationRestriction,
)
from lex.api.serializers import get_serializer_map_for_model
import logging

logger = logging.getLogger(__name__)


class ModelContainer:
    """
    Container for model class and its admin configuration.
    
    This class wraps a Django model class along with its process admin configuration,
    providing a unified interface for accessing model metadata, serializers, and
    permission restrictions. It serves as the core component in the model collection
    system for organizing and managing model access patterns.
    
    Attributes:
        model_class: The Django model class being contained
        process_admin: The admin configuration for the model
        dependent_model_containers: Set of containers that depend on this one
        serializers_map: Dictionary mapping serializer names to serializer classes
        obj_serializer: Default serializer for list/detail endpoints
    """
    def __init__(self, model_class: Type[Model], process_admin: Any) -> None:
        """
        Initialize ModelContainer with enhanced validation and error handling.
        
        Args:
            model_class: Django model class to be contained
            process_admin: Admin configuration object for the model
            
        Raises:
            TypeError: If model_class is not a valid Django model class
            ValueError: If process_admin is None or invalid
        """
        if not model_class:
            raise ValueError("model_class cannot be None")
        
        if not process_admin:
            raise ValueError("process_admin cannot be None")
            
        # Validate that model_class is a Django model
        if not (hasattr(model_class, '_meta') or hasattr(model_class, '__name__')):
            raise TypeError(f"model_class must be a Django model class, got {type(model_class)}")
        
        self.model_class: Type[Model] = model_class
        self.process_admin: Any = process_admin
        self.dependent_model_containers: Set["ModelContainer"] = set()
        self.serializers_map: Optional[Dict[str, Any]] = None
        self.obj_serializer: Optional[Any] = None
        self._serializer_signature: Optional[tuple] = None

        if hasattr(model_class, "_meta"):
            self.get_serializers_map(force_refresh=True)
        else:
            # Ensure serializers_map is always a dict, never None
            self.serializers_map = {}
            self.obj_serializer = None

    @staticmethod
    def _custom_serializer_signature(custom_serializers: Any) -> Optional[tuple]:
        if not isinstance(custom_serializers, dict):
            return None
        return tuple(
            sorted(
                (str(name), id(serializer_cls))
                for name, serializer_cls in custom_serializers.items()
            )
        )

    def _build_serializer_signature(self, default_fields: Optional[list]) -> tuple:
        default_signature = None if default_fields is None else tuple(default_fields)
        custom_signature = self._custom_serializer_signature(
            getattr(self.model_class, "api_serializers", None)
        )
        return default_signature, custom_signature

    def get_serializers_map(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Return serializers for this model and refresh lazily when the model's
        custom serializer registration changed at runtime.
        """
        if not hasattr(self.model_class, "_meta"):
            self.serializers_map = {}
            self.obj_serializer = None
            return self.serializers_map

        default_fields = self.process_admin.get_fields_in_table_view(self.model_class)
        signature = self._build_serializer_signature(default_fields)

        if (
            not force_refresh
            and self.serializers_map is not None
            and self._serializer_signature == signature
        ):
            return self.serializers_map

        try:
            self.serializers_map = get_serializer_map_for_model(
                self.model_class, default_fields
            )
            self.obj_serializer = (
                self.serializers_map.get("default") if self.serializers_map else None
            )
            self._serializer_signature = signature
        except Exception as e:
            logger.warning(f"Failed to create serializers for {self.model_class}: {e}")
            self.serializers_map = {}
            self.obj_serializer = None

        return self.serializers_map

    @property
    def model_id(self) -> str:
        """
        Get the unique identifier for this model.
        
        Returns the model's meta model_name if available, otherwise falls back
        to the lowercase class name. This identifier is used throughout the
        system to reference this specific model container.
        
        Returns:
            str: Unique identifier for the model
        """
        return (
            self.model_class._meta.model_name
            if hasattr(self.model_class, "_meta")
            else self.model_class.__name__.lower()
        )

    @property
    def display_title(self) -> str:
        """
        Get the human-readable display title for this model.
        
        Returns the model's verbose name (properly capitalized) if available,
        otherwise falls back to the class name. This title is used in user
        interfaces and documentation.
        
        Returns:
            str: Human-readable display title for the model
        """
        if hasattr(self.model_class, "_meta"):
            from .utils import title_for_model

            return title_for_model(self.model_class)
        return self.model_class.__name__

    # Backward compatibility aliases - maintain identical external interface
    @property
    def id(self) -> str:
        """
        Legacy property alias for model_id.
        
        Deprecated: Use model_id instead for better clarity.
        This property is maintained for backward compatibility.
        
        Returns:
            str: Unique identifier for the model
        """
        return self.model_id

    @property
    def title(self) -> str:
        """
        Legacy property alias for display_title.
        
        Deprecated: Use display_title instead for better clarity.
        This property is maintained for backward compatibility.
        
        Returns:
            str: Human-readable display title for the model
        """
        return self.display_title

    @property
    def pk_name(self) -> Optional[str]:
        """
        Get the primary key field name for this model.
        
        Returns the name of the primary key field if the model has Django meta
        information, otherwise returns None.
        
        Returns:
            Optional[str]: Primary key field name, or None if not available
        """
        return (
            self.model_class._meta.pk.name
            if hasattr(self.model_class, "_meta")
            else None
        )

    def get_modification_restriction(self) -> ModelModificationRestriction:
        """
        Get the modification restriction configuration for this model.
        
        Returns the model's modification_restriction attribute if it exists,
        otherwise returns a default ModelModificationRestriction instance.
        
        Returns:
            ModelModificationRestriction: The restriction configuration for this model
        """
        return getattr(
            self.model_class, "modification_restriction", ModelModificationRestriction()
        )

    @staticmethod
    def _permission_result_to_bool(result: Any) -> bool:
        """
        Normalize both legacy and new permission return types to a boolean.
        """
        if hasattr(result, "allowed"):
            return bool(getattr(result, "allowed"))
        if isinstance(result, (set, list, tuple, frozenset, dict)):
            return len(result) > 0
        return bool(result)

    def _evaluate_request_permission(
        self,
        request: Any,
        instance: Any,
        user_context: Any,
        legacy_allowed: bool,
        *,
        new_method_names: tuple[str, ...],
        legacy_method_names: tuple[str, ...] = (),
    ) -> bool:
        """
        Combine legacy model restrictions with request-scoped permission methods.
        """
        if not legacy_allowed or instance is None:
            return bool(legacy_allowed)

        try:
            for method_name in new_method_names:
                method = getattr(instance, method_name, None)
                if callable(method):
                    return self._permission_result_to_bool(method(user_context))

            for method_name in legacy_method_names:
                method = getattr(instance, method_name, None)
                if callable(method):
                    return self._permission_result_to_bool(method(request))
        except Exception:
            return bool(legacy_allowed)

        return bool(legacy_allowed)

    def get_permission_restrictions_for_user(self, user: Any) -> Dict[str, bool]:
        """
        Get user permission restrictions for this model with better method naming.
        
        Evaluates the user's permissions against this model's modification restrictions
        and returns a dictionary indicating what operations the user can perform.
        
        Args:
            user: The user object to check permissions for
            
        Returns:
            Dict[str, bool]: Dictionary with permission flags:
                - can_read_in_general: Whether user can read model instances
                - can_modify_in_general: Whether user can modify model instances  
                - can_create_in_general: Whether user can create model instances
                - can_delete_in_general: Whether user can delete model instances
        """
        restriction = self.get_modification_restriction()
        return {
            "can_read_in_general": restriction.can_read_in_general(user, None),
            "can_modify_in_general": restriction.can_modify_in_general(user, None),
            "can_create_in_general": restriction.can_create_in_general(user, None),
            "can_delete_in_general": restriction.can_delete_in_general(user, None),
        }

    def get_permission_restrictions_for_request(self, request: Any) -> Dict[str, bool]:
        """
        Resolve general permissions using both legacy restrictions and request-scoped
        Keycloak/new permission methods when available.
        """
        user = getattr(request, "user", None)
        legacy_permissions = self.get_permission_restrictions_for_user(user)

        if request is None:
            return legacy_permissions

        try:
            instance = self.model_class()
        except Exception:
            return legacy_permissions

        try:
            from lex.core.models.LexModel import UserContext

            user_context = UserContext.from_request(request, instance)
        except Exception:
            return legacy_permissions

        return {
            "can_read_in_general": self._evaluate_request_permission(
                request,
                instance,
                user_context,
                legacy_permissions["can_read_in_general"],
                new_method_names=("permission_list", "permission_read"),
                legacy_method_names=("can_list", "can_read"),
            ),
            "can_modify_in_general": self._evaluate_request_permission(
                request,
                instance,
                user_context,
                legacy_permissions["can_modify_in_general"],
                new_method_names=("permission_edit",),
                legacy_method_names=("can_edit",),
            ),
            "can_create_in_general": self._evaluate_request_permission(
                request,
                instance,
                user_context,
                legacy_permissions["can_create_in_general"],
                new_method_names=("permission_create",),
                legacy_method_names=("can_create",),
            ),
            "can_delete_in_general": self._evaluate_request_permission(
                request,
                instance,
                user_context,
                legacy_permissions["can_delete_in_general"],
                new_method_names=("permission_delete",),
                legacy_method_names=("can_delete",),
            ),
        }

    def get_general_modification_restrictions_for_user(self, user: Any) -> Dict[str, bool]:
        """
        Legacy method alias for get_permission_restrictions_for_user.
        
        Deprecated: Use get_permission_restrictions_for_user instead for better clarity.
        This method is maintained for backward compatibility.
        
        Args:
            user: The user object to check permissions for
            
        Returns:
            Dict[str, bool]: Dictionary with permission flags
        """
        return self.get_permission_restrictions_for_user(user)
