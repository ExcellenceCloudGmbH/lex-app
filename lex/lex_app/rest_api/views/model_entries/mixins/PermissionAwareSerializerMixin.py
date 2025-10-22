import re
from functools import lru_cache
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied


def _camel_to_snake(name: str) -> str:
    """
    Converts a camelCase string to snake_case.
    For example: 'reportDate' -> 'report_date'
    """
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


class PermissionAwareSerializerMixin:
    """
    A serializer mixin that injects field-level write-permission checks.

    This mixin intelligently handles permission validation with several key features:
    - Change Detection: Only checks permissions for fields where the incoming
      value is different from the value currently stored in the model instance.
    - Convention-Based Field Name Translation: Converts incoming camelCase field
      names to the snake_case names expected by the backend model.
    - Respects Model's Authority: Calls the `can_edit()` method on the model
      instance to get the authoritative set of editable fields.
    - Ignores Non-Editable & Special Fields: Automatically ignores fields that
      should not be part of a permission check, such as system-managed fields
      (`created_by`), reserved fields, and serializer method fields.
    - Pre-Validation Check: Hooks into `run_validation` to ensure permissions
      are checked before any other validation logic runs.
    """

    @lru_cache
    def _get_model_field_names(self) -> set:
        """
        Returns a set of all actual field names defined on the model.
        """
        if not hasattr(self.Meta, 'model'):
            return set()
        model = self.Meta.model
        return {f.name for f in model._meta.get_fields()}


    @lru_cache
    def _get_non_editable_fields(self) -> set:
        """
        Inspects the serializer's model and returns a set of field names that
        are not user-editable (e.g., marked with `editable=False`).
        """
        if not hasattr(self.Meta, 'model'):
            return set()
        model = self.Meta.model
        non_editable = {
            f.name for f in model._meta.get_fields()
            if hasattr(f, 'editable') and not f.editable
        }
        if model._meta.pk:
            non_editable.add(model._meta.pk.name)
        return non_editable

    def run_validation(self, data):
        """
        This is the main entry point for the permission check, overriding
        DRF's default to perform checks *before* field validation.
        """
        request = self.context.get('request')
        instance = self.instance

        if instance and request and data:
            if not hasattr(instance, 'can_edit'):
                return super().run_validation(data)

            model_field_names = self._get_model_field_names()
            non_editable_fields = self._get_non_editable_fields()
            editable_backend_fields = instance.can_edit(request)

            if not isinstance(editable_backend_fields, set):
                raise PermissionDenied(
                    f"You do not have permission to edit this {instance.__class__.__name__}."
                )

            for frontend_field_name, new_raw_value in data.items():
                if frontend_field_name.startswith('lexReserved'):
                    continue

                backend_field_name = _camel_to_snake(frontend_field_name)

                if backend_field_name not in model_field_names:
                    continue
                if backend_field_name in non_editable_fields:
                    continue

                # --- CHANGE DETECTION LOGIC ---
                field_obj = self.fields.get(frontend_field_name)
                if field_obj:
                    try:
                        # Convert incoming value to a Python object for accurate comparison
                        new_python_value = field_obj.to_internal_value(new_raw_value)
                        old_python_value = getattr(instance, backend_field_name)

                        # If values are the same, no change was made. Skip permission check.
                        if new_python_value == old_python_value:
                            continue
                    except (serializers.ValidationError, AttributeError):
                        # If conversion fails or attribute doesn't exist, validation
                        # will fail later. For now, we must assume it's an attempted
                        # change and check permission.
                        pass
                # --- END OF CHANGE DETECTION ---

                # If we reach here, it's a changed field. Now, check permission.
                if backend_field_name not in editable_backend_fields:
                    raise PermissionDenied(
                        f"You do not have permission to edit the '{frontend_field_name}' field."
                    )

        elif not instance and request:
            model_class = self.Meta.model
            if hasattr(model_class, 'can_create'):
                if not model_class().can_create(request):
                     raise PermissionDenied(
                        f"You do not have permission to create a {model_class.__name__}."
                    )

        return super().run_validation(data)


def add_permission_checks(serializer_class):
    """
    A class decorator that injects permission-checking capabilities into any
    ModelSerializer.
    """
    class PermissionAwareVersion(PermissionAwareSerializerMixin, serializer_class):
        pass

    PermissionAwareVersion.__name__ = serializer_class.__name__
    PermissionAwareVersion.__module__ = serializer_class.__module__
    return PermissionAwareVersion


# Metaclass approach for automatic permission injection
class PermissionAwareSerializerMetaclass(type(serializers.ModelSerializer)):
    """
    Metaclass that automatically adds permission checking to all model serializers.
    """

    def __new__(mcs, name, bases, attrs):
        # Don't modify the base classes themselves
        if name in ('PermissionAwareSerializerMixin', 'LexModelSerializer'):
            return super().__new__(mcs, name, bases, attrs)

        # Check if this is a ModelSerializer for a LexModel
        meta_class = attrs.get('Meta')
        if meta_class and hasattr(meta_class, 'model'):
            model = meta_class.model
            # Check if the model inherits from LexModel
            if hasattr(model, 'can_read') and hasattr(model, 'can_edit'):
                # Inject the permission mixin if not already present
                if PermissionAwareSerializerMixin not in bases:
                    bases = (PermissionAwareSerializerMixin,) + bases

        return super().__new__(mcs, name, bases, attrs)

