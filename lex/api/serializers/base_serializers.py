from django.db import models
from django.db.models import Model
from rest_framework import serializers, viewsets

from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal
from django.apps import apps
from django.db.models import Model
from django.db.models.fields import DateTimeField, DateField, TimeField
from lex.core.models.base import LexModel

# Field‐names that React-Admin expects
ID_FIELD_NAME = "id_field"
SHORT_DESCR_NAME = "short_description"
LEX_SCOPES_NAME = "lex_reserved_scopes"



# --- NEW FILTERING LIST SERIALIZER ---
class FilteredListSerializer(serializers.ListSerializer):
    """
    A custom ListSerializer that filters out items that, after serialization,
    result in an empty dictionary.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, models.Manager) else data
        ret = []
        for item in iterable:
            representation = self.child.to_representation(item)
            # Only include non-empty results in the final list
            if representation:
                ret.append(representation)
        return ret


# --- UPDATED PERMISSION-AWARE BASE SERIALIZER ---
class LexSerializer(serializers.ModelSerializer):
    """
    A custom ModelSerializer that controls field visibility and adds a
    `scopes` field to the output for each record.
    """
    # Define a new field to hold thescopes for each record.
    lex_reserved_scopes = serializers.SerializerMethodField()

    # TODO: Just allow for Historical ?
    def get_lex_reserved_scopes(self, instance):
        """
        Compute per-record scopes using the new permission system.
        """
        request = self.context.get('request')
        if not request:
            return {}

        try:
            # Check if this is a LexModel instance
            if not hasattr(instance, 'permission_edit'):
                return {}

            # Create user context
            from lex.core.models.base import UserContext
            user_context = UserContext.from_request(request, instance)
            
            # Get all field names for this model
            all_fields = {f.name for f in instance._meta.fields}
            
            # Get permissions using new system
            edit_result = instance.permission_edit(user_context)
            delete_allowed = instance.permission_delete(user_context)
            export_result = instance.permission_export(user_context)
            
            # Get editable fields, excluding internal fields
            edit_fields = edit_result.get_fields(all_fields)
            
            # Remove internal LexModel fields and id
            try:
                from lex.core.models.base import LexModel
                lexmodel_fields = {f.name for f in LexModel._meta.fields}
            except Exception:
                lexmodel_fields = set()
            
            edit_fields -= (lexmodel_fields | {'id'})
            
            return {
                "edit": sorted(edit_fields),
                "delete": bool(delete_allowed),
                "export": bool(export_result.allowed),
            }
        except Exception:
            # Any unexpected error → hide scopes entirely
            return {}
    @classmethod
    def _build_shadow_instance(cls, model_class: type[Model], payload: dict) -> Model | None:
        try:
            field_map = {f.name: f for f in model_class._meta.concrete_fields}
            init_kwargs = {}
            for key, val in (payload or {}).items():
                if key in field_map:
                    field = field_map[key]
                    parsed_val = cls._parse_value_for_field(field, val)
                    # For foreign key fields, use the _id suffix
                    from django.db.models import ForeignKey
                    if isinstance(field, ForeignKey) and not key.endswith('_id'):
                        init_kwargs[f"{key}_id"] = parsed_val
                    else:
                        init_kwargs[key] = parsed_val
            # Ensure pk mapping if present in payload
            pk_name = model_class._meta.pk.name
            if pk_name in payload:
                init_kwargs[pk_name] = payload[pk_name]
            return model_class(**init_kwargs)
        except Exception:
            return None

    
    @classmethod
    def _filter_foreign_key_relations(cls, request, model_class, payload: dict) -> dict:
        """
        Filter foreign key relationships in payload based on individual permissions.
        
        Args:
            request: Django request object
            model_class: The main model class
            payload: The audit log payload dictionary
            
        Returns:
            Filtered payload with unauthorized foreign key relations removed
        """
        if not payload:
            return payload
            
        filtered_payload = payload.copy()
        
        # Get field map for the model
        field_map = {f.name: f for f in model_class._meta.concrete_fields}
        
        for field_name, field_value in payload.items():
            if field_name in field_map:
                field = field_map[field_name]
                
                # Check if this is a foreign key field with dictionary representation
                from django.db.models import ForeignKey
                if isinstance(field, ForeignKey) and isinstance(field_value, dict):
                    related_model = field.related_model
                    
                    # Try to get the related object ID
                    related_id = field_value.get('id')
                    if related_id is not None:
                        try:
                            # Get the actual related object
                            related_obj = related_model.objects.get(pk=related_id)
                            
                            # Check if user can read this related object
                            if hasattr(related_obj, 'permission_read'):
                                from lex.core.models.base import UserContext
                                user_context = UserContext.from_request(request, related_obj)
                                result = related_obj.permission_read(user_context)
                                
                                # If permission is denied, remove this field from payload
                                if not result.allowed:
                                    filtered_payload.pop(field_name, None)
                                    continue
                            elif hasattr(related_obj, 'can_read'):
                                # Fallback to legacy method
                                readable_fields = related_obj.can_read(request)
                                if isinstance(readable_fields, (set, list, tuple)) and len(readable_fields) == 0:
                                    filtered_payload.pop(field_name, None)
                                    continue
                                elif not readable_fields:
                                    filtered_payload.pop(field_name, None)
                                    continue
                                    
                        except (related_model.DoesNotExist, Exception):
                            # If we can't find or check the related object, keep it (preserve existing behavior)
                            pass
        
        return filtered_payload

    @staticmethod
    def _resolve_target_model(audit_log) -> type[Model] | None:
        # Prefer content_type if present
        ct = getattr(audit_log, "content_type", None)
        if ct:
            try:
                return ct.model_class()
            except Exception:
                pass
        # Fallback: resolve from resource string
        resource = getattr(audit_log, "resource", None)
        if resource:
            res = resource.lower()
            for model in apps.get_models():
                if model._meta.model_name.lower() == res or model.__name__.lower() == res:
                    return model
        return None

    @staticmethod
    def _parse_value_for_field(field, value):
        if value is None:
            return None
        
        # Handle foreign key relationships stored as dictionaries
        from django.db.models import ForeignKey
        if isinstance(field, ForeignKey) and isinstance(value, dict):
            # Extract the ID from foreign key dictionary representation
            if 'id' in value:
                return value['id']  # Return just the ID for foreign key assignment
            return None
        
        try:
            if isinstance(field, DateTimeField):
                # Accept ISO-like strings captured in payload
                from datetime import datetime
                return datetime.fromisoformat(value)
            if isinstance(field, DateField):
                from datetime import date
                return date.fromisoformat(value)
            if isinstance(field, TimeField):
                from datetime import time
                return time.fromisoformat(value)
        except Exception:
            return None
        return value



    def to_representation(self, instance):
        request = self.context.get('request')

        # Normal visible fields for concrete models
        visible_fields = (
            instance.can_read(request)
            if hasattr(instance, 'can_read') else
            {f.name for f in instance._meta.fields}
        )

        if not visible_fields:
            return {}

        representation = super().to_representation(instance)

        # Filter non-AuditLog outputs by visible fields (existing behavior)
        for field_name in list(representation.keys()):
            if field_name not in visible_fields and field_name not in ['history_id', 'calculation_record', 'lex_reserved_scopes', 'id', 'id_field', SHORT_DESCR_NAME]:
                representation.pop(field_name, None)

        # AuditLog payload filtering using target model can_read
        try:
            if instance.__class__._meta.model_name.lower() == 'auditlog':
                payload = representation.get('payload') or getattr(instance, 'payload', None)
                if isinstance(payload, dict):
                    model_class = self._resolve_target_model(instance)
                    if model_class is not None:
                        # First filter foreign key relationships based on individual permissions
                        filtered_payload = self._filter_foreign_key_relations(request, model_class, payload)
                        
                        shadow = self._build_shadow_instance(model_class, filtered_payload)
                        if shadow is not None and hasattr(shadow, 'can_read'):
                            target_visible = shadow.can_read(request) or set()
                            # Prune payload by target model visibility; keep identifiers
                            keep_always = {'id', 'id_field', SHORT_DESCR_NAME}
                            pruned = {k: v for k, v in filtered_payload.items() if k in target_visible or k in keep_always}
                            if "updates" in filtered_payload:
                                pruned_updates = {k: v for k, v in filtered_payload['updates'].items() if k in target_visible or k in keep_always}
                                pruned['updates'] = pruned_updates

                            representation['payload'] = pruned
                        else:
                            # If we can't build shadow instance, at least apply foreign key filtering
                            representation['payload'] = filtered_payload
        except Exception:
            # Preserve representation on any failure to match existing allow-by-default semantics
            pass

        return representation


# --- UPDATED BASE TEMPLATE ---
class RestApiModelSerializerTemplate(LexSerializer):
    """
    The base template for all auto-generated and wrapped serializers.
    It inherits the new nested permission structure from LexSerializer.
    """
    # Note: short_description is now implicitly handled by the parent's
    # to_representation method and will be nested like all other fields.
    short_description = serializers.SerializerMethodField()

    def get_short_description(self, obj):
        return str(obj)

    class Meta:
        model = None
        fields = "__all__"
        # Use our custom list serializer for all list views.
        list_serializer_class = FilteredListSerializer


class RestApiModelViewSetTemplate(viewsets.ModelViewSet):
    queryset = None
    serializer_class = None


# --- HELPER FUNCTIONS (Unchanged) ---

def model2serializer(model, fields=None, name_suffix=""):
    if not hasattr(model, "_meta"):
        return None
    if fields is None:
        fields = [f.name for f in model._meta.fields]
    model_name = model._meta.model_name.capitalize()
    class_name = (
        f"{model_name}{name_suffix.capitalize()}Serializer"
        if name_suffix
        else f"{model_name}Serializer"
    )

    # alias for model._meta.pk.name
    pk_alias = serializers.ReadOnlyField(default=model._meta.pk.name)

    # ensure our internal fields are always present
    all_fields = list(fields) + [ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME]  # <-- add LEX_SCOPES_NAME

    return type(
        class_name,
        (RestApiModelSerializerTemplate,),
        {
            ID_FIELD_NAME: pk_alias,
            "Meta": type(
                "Meta",
                (RestApiModelSerializerTemplate.Meta,),
                {"model": model, "fields": all_fields},
            ),
        },
    )



def _wrap_custom_serializer(custom_cls, model_class):
    meta = getattr(custom_cls, "Meta", type("Meta", (), {}))
    existing_fields = getattr(meta, "fields", "__all__")
    if existing_fields != "__all__":
        existing = list(existing_fields)
        # make sure all internal fields are present, including lex_reserved_scopes
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME):  # <-- add LEX_SCOPES_NAME
            if extra not in existing:
                existing.append(extra)
        new_fields = existing
    else:
        new_fields = "__all__"
    NewMeta = type(
        "Meta",
        (meta,),
        {
            "model": model_class,
            "fields": new_fields,
            "list_serializer_class": FilteredListSerializer
        }
    )
    attrs = {
        ID_FIELD_NAME: serializers.ReadOnlyField(default=model_class._meta.pk.name),
        SHORT_DESCR_NAME: serializers.SerializerMethodField(),
        "get_short_description": lambda self, obj: str(obj),
        "Meta": NewMeta,
    }
    base_classes = (LexSerializer, custom_cls)
    return type(f"{custom_cls.__name__}WithInternalFields", base_classes, attrs)


def get_serializer_map_for_model(model_class, default_fields=None):
    custom = getattr(model_class, "api_serializers", None)
    if isinstance(custom, dict) and custom:
        wrapped = {}
        for name, cls in custom.items():
            wrapped[name] = _wrap_custom_serializer(cls, model_class)
        return wrapped
    auto = model2serializer(model_class, default_fields)
    return {"default": auto}
