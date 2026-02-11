from django.db import models
from django.db.models import Model, ForeignKey
from django.db.models.fields import DateTimeField, DateField, TimeField
from django.apps import apps
from rest_framework import serializers, viewsets

from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal

from lex.core.models.LexModel import LexModel, UserContext

# Field-names that React-Admin expects
ID_FIELD_NAME = "id_field"
SHORT_DESCR_NAME = "short_description"
LEX_SCOPES_NAME = "lex_reserved_scopes"

# --- MODULE-LEVEL CACHES (populated lazily, persist for process lifetime) ---

# Cache: LexModel base field names (identical for every LexModel subclass)
_lexmodel_fields: set | None = None

def _get_lexmodel_fields() -> set:
    """Return the cached set of LexModel base field names."""
    global _lexmodel_fields
    if _lexmodel_fields is None:
        try:
            _lexmodel_fields = {f.name for f in LexModel._meta.fields}
        except Exception:
            _lexmodel_fields = set()
    return _lexmodel_fields


# Cache: model-name -> model-class lookup for _resolve_target_model
_model_lookup: dict | None = None

def _get_model_lookup() -> dict:
    """Return a lazily-built { lower_name: model_class } dict."""
    global _model_lookup
    if _model_lookup is None:
        _model_lookup = {}
        for m in apps.get_models():
            _model_lookup[m._meta.model_name.lower()] = m
            _model_lookup[m.__name__.lower()] = m
    return _model_lookup


# Cache: model_class -> capability flags (avoids repeated hasattr per record)
_capability_cache: dict = {}

def _get_capabilities(model_class: type) -> dict:
    """Return cached capability flags for a model class."""
    caps = _capability_cache.get(model_class)
    if caps is None:
        caps = {
            'has_permission_edit': hasattr(model_class, 'permission_edit'),
            'has_permission_delete': hasattr(model_class, 'permission_delete'),
            'has_permission_export': hasattr(model_class, 'permission_export'),
            'has_permission_read': hasattr(model_class, 'permission_read'),
            'has_can_read': hasattr(model_class, 'can_read'),
            'has_can_edit': hasattr(model_class, 'can_edit'),
            'has_can_delete': hasattr(model_class, 'can_delete'),
            'has_can_export': hasattr(model_class, 'can_export'),
        }
        _capability_cache[model_class] = caps
    return caps


# --- NEW FILTERING LIST SERIALIZER ---
class FilteredListSerializer(serializers.ListSerializer):
    """
    A custom ListSerializer that filters out items that, after serialization,
    result in an empty dictionary.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, models.Manager) else data
        return [r for r in (self.child.to_representation(item) for item in iterable) if r]


# --- UPDATED PERMISSION-AWARE BASE SERIALIZER ---
class LexSerializer(serializers.ModelSerializer):
    """
    A custom ModelSerializer that controls field visibility and adds a
    `scopes` field to the output for each record.
    """
    # Define a new field to hold the scopes for each record.
    lex_reserved_scopes = serializers.SerializerMethodField()

    # ------------------------------------------------------------------
    # Per-serializer caches (populated once, reused across all records)
    # ------------------------------------------------------------------
    _base_user_context = None      # Cached UserContext without keycloak scopes
    _meta_fields_cache: dict = {}  # { model_class: set_of_field_names }

    def _get_base_user_context(self, request):
        """Get or create a base UserContext cached on this serializer instance."""
        if self._base_user_context is None:
            self._base_user_context = UserContext.from_request_base(request)
        return self._base_user_context

    def _get_user_context(self, request, target_instance):
        """Create an instance-specific UserContext from the cached base."""
        base = self._get_base_user_context(request)
        return base.with_instance(request, target_instance)

    @classmethod
    def _get_cached_field_names(cls, model_class) -> set:
        """Return cached set of field names for a model class."""
        fields = cls._meta_fields_cache.get(model_class)
        if fields is None:
            fields = {f.name for f in model_class._meta.fields}
            cls._meta_fields_cache[model_class] = fields
        return fields

    # ------------------------------------------------------------------
    # Helper: unwrap history/meta wrappers to get the real model instance
    # ------------------------------------------------------------------
    @staticmethod
    def _unwrap_instance(instance):
        """Unwrap History / MetaHistory wrappers to reach the concrete model."""
        target = instance

        # 1. Unwrap Meta wrapper if present (Level 2 -> Level 1)
        if hasattr(target, 'history_object') and target.history_object:
            target = target.history_object

        # 2. Unwrap History wrapper (Level 1 -> Main)
        unwrapped = False
        try:
            possible = getattr(target, 'instance', None)
            if possible:
                target = possible
                unwrapped = True
        except Exception:
            pass

        if not unwrapped and hasattr(target, 'instance_type'):
            try:
                ModelClass = target.instance_type
                init_kwargs = {}
                for field in ModelClass._meta.fields:
                    if hasattr(target, field.attname):
                        init_kwargs[field.attname] = getattr(target, field.attname)
                target = ModelClass(**init_kwargs)
            except Exception:
                pass

        return target

    # ------------------------------------------------------------------
    # Scopes computation
    # ------------------------------------------------------------------
    def get_lex_reserved_scopes(self, instance):
        """
        Compute per-record scopes using the new permission system.
        """
        request = self.context.get('request')
        if not request:
            return {}

        try:
            if instance.__class__.__name__.startswith('MetaHistorical'):
                return {
                    "edit": [],
                    "delete": False,
                    "export": False,
                }

            caps = _get_capabilities(type(instance))

            # Resolve the underlying model instance for permission checks
            if not caps['has_permission_edit']:
                target_instance = self._unwrap_instance(instance)
                target_caps = _get_capabilities(type(target_instance))

                if not target_caps['has_permission_edit'] and not target_caps['has_can_edit']:
                    return {}
            else:
                target_instance = instance
                target_caps = caps

            # Create user context (reuses cached base)
            user_context = self._get_user_context(request, target_instance)

            # Get all field names (cached per model class)
            all_fields = self._get_cached_field_names(type(target_instance))

            # Get permissions
            if target_caps['has_permission_edit']:
                edit_result = target_instance.permission_edit(user_context)
                edit_fields = edit_result.get_fields(all_fields)
            elif target_caps['has_can_edit']:
                edit_fields = target_instance.can_edit(request)
            else:
                edit_fields = set()

            if target_caps['has_permission_delete']:
                delete_allowed = target_instance.permission_delete(user_context)
            elif target_caps['has_can_delete']:
                delete_allowed = target_instance.can_delete(request)
            else:
                delete_allowed = True

            if target_caps['has_permission_export']:
                export_result = target_instance.permission_export(user_context)
                export_allowed = export_result.allowed if hasattr(export_result, 'allowed') else bool(export_result)
            elif target_caps['has_can_export']:
                export_allowed = bool(target_instance.can_export(request))
            else:
                export_allowed = True

            # Remove internal LexModel fields and id
            lexmodel_fields = _get_lexmodel_fields()
            edit_fields -= (lexmodel_fields | {'id'})

            # History records: make valid_from/valid_to editable
            if hasattr(instance, 'history_type') or hasattr(instance, 'history_id'):
                for f in ('valid_from', 'valid_to'):
                    if hasattr(instance, f):
                        edit_fields.add(f)

            return {
                "edit": sorted(edit_fields),
                "delete": bool(delete_allowed),
                "export": bool(export_allowed),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Shadow instance / audit-log helpers
    # ------------------------------------------------------------------
    @classmethod
    def _build_shadow_instance(cls, model_class: type[Model], payload: dict) -> Model | None:
        try:
            field_map = {f.name: f for f in model_class._meta.concrete_fields}
            init_kwargs = {}
            for key, val in (payload or {}).items():
                if key in field_map:
                    field = field_map[key]
                    parsed_val = cls._parse_value_for_field(field, val)
                    if isinstance(field, ForeignKey) and not key.endswith('_id'):
                        init_kwargs[f"{key}_id"] = parsed_val
                    else:
                        init_kwargs[key] = parsed_val
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
        # Fallback: O(1) lookup from cached dict
        resource = getattr(audit_log, "resource", None)
        if resource:
            return _get_model_lookup().get(resource.lower())
        return None

    @staticmethod
    def _parse_value_for_field(field, value):
        if value is None:
            return None
        
        # Handle foreign key relationships stored as dictionaries
        if isinstance(field, ForeignKey) and isinstance(value, dict):
            if 'id' in value:
                return value['id']
            return None
        
        try:
            if isinstance(field, DateTimeField):
                return datetime.fromisoformat(value)
            if isinstance(field, DateField):
                return date.fromisoformat(value)
            if isinstance(field, TimeField):
                return time.fromisoformat(value)
        except Exception:
            return None
        return value


    # System fields always allowed through visibility filtering
    _SYSTEM_FIELDS = frozenset({
        'history_id', 'history_date', 'history_type', 'history_user', 'history_change_reason',
        'valid_from', 'valid_to',
        'calculation_record', 'lex_reserved_scopes', 'id', 'id_field', SHORT_DESCR_NAME
    })

    def to_representation(self, instance):
        request = self.context.get('request')

        # Resolve the target instance for permission checks
        caps = _get_capabilities(type(instance))

        if not caps['has_can_read'] and not caps['has_permission_read']:
            target_instance = self._unwrap_instance(instance)
            target_caps = _get_capabilities(type(target_instance))
        else:
            target_instance = instance
            target_caps = caps

        # Normal visible fields for concrete models
        visible_fields = None
        
        # 1. Try Legacy 'can_read'
        if target_caps['has_can_read']:
            visible_fields = target_instance.can_read(request)
            
        # 2. Try New System 'permission_read'
        elif target_caps['has_permission_read']:
             user_context = self._get_user_context(request, target_instance)
             result = target_instance.permission_read(user_context)
             if not result.allowed:
                 return {}  # Hide entirely
             
             all_fields = self._get_cached_field_names(type(target_instance))
             visible_fields = result.get_fields(all_fields)

        # 3. Fallback: All fields
        if visible_fields is None:
             visible_fields = self._get_cached_field_names(type(instance))

        if not visible_fields:
            return {}

        representation = super().to_representation(instance)
        
        # Filter non-AuditLog outputs by visible fields
        system_fields = self._SYSTEM_FIELDS
        
        for field_name in list(representation.keys()):
            if field_name not in visible_fields and field_name not in system_fields:
                representation.pop(field_name, None)

        # AuditLog payload filtering using target model can_read
        try:
            if instance.__class__._meta.model_name.lower() == 'auditlog':
                payload = representation.get('payload') or getattr(instance, 'payload', None)
                if isinstance(payload, dict):
                    model_class = self._resolve_target_model(instance)
                    if model_class is not None:
                        filtered_payload = self._filter_foreign_key_relations(request, model_class, payload)
                        
                        shadow = self._build_shadow_instance(model_class, filtered_payload)
                        if shadow is not None and hasattr(shadow, 'can_read'):
                            target_visible = shadow.can_read(request) or set()
                            keep_always = {'id', 'id_field', SHORT_DESCR_NAME}
                            pruned = {k: v for k, v in filtered_payload.items() if k in target_visible or k in keep_always}
                            if "updates" in filtered_payload:
                                pruned_updates = {k: v for k, v in filtered_payload['updates'].items() if k in target_visible or k in keep_always}
                                pruned['updates'] = pruned_updates

                            representation['payload'] = pruned
                        else:
                            representation['payload'] = filtered_payload
        except Exception:
            pass

        return representation

# --- UPDATED BASE TEMPLATE ---
class RestApiModelSerializerTemplate(LexSerializer):
    """
    The base template for all auto-generated and wrapped serializers.
    It inherits the new nested permission structure from LexSerializer.
    """
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
    all_fields = list(fields) + [ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME]

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
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME):
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
