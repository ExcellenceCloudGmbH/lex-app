from django.db import models
from django.db.models import Model
from rest_framework import serializers, viewsets

from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal
from django.apps import apps
from django.db.models import Model
from django.db.models.fields import DateTimeField, DateField, TimeField
from lex.lex_app.lex_models.LexModel import LexModel

# Field‐names that React-Admin expects
ID_FIELD_NAME = "id_field"
SHORT_DESCR_NAME = "short_description"



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


    def get_lex_reserved_scopes(self, instance):
        """
        This method is called for each record to get its specific permissions.
        It calls the can_* methods on the model instance.
        """
        request = self.context.get('request')
        if not request:
            return {}

        lexmodel_fields =  set(map(lambda x: x.name, LexModel._meta.fields))
        edit = instance.can_edit(request) - lexmodel_fields - {'id'}
        delete = instance.can_delete(request)
        export = instance.can_export(request)
        if not edit:
            edit = []

        return {
            "edit": edit,
            "delete": delete,
            "export": export
        }
    @classmethod
    def _build_shadow_instance(cls, model_class: type[Model], payload: dict) -> Model | None:
        try:
            field_map = {f.name: f for f in model_class._meta.concrete_fields}
            init_kwargs = {}
            for key, val in (payload or {}).items():
                if key in field_map:
                    init_kwargs[key] = cls._parse_value_for_field(field_map[key], val)
            # Ensure pk mapping if present in payload
            pk_name = model_class._meta.pk.name
            if pk_name in payload:
                init_kwargs[pk_name] = payload[pk_name]
            return model_class(**init_kwargs)
        except Exception:
            return None

    
    @staticmethod
    def _resolve_target_model(auditlog) -> type[Model] | None:
        # Prefer content_type if present
        ct = getattr(auditlog, "content_type", None)
        if ct:
            try:
                return ct.model_class()
            except Exception:
                pass
        # Fallback: resolve from resource string
        resource = getattr(auditlog, "resource", None)
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
                        shadow = self._build_shadow_instance(model_class, payload)
                        if shadow is not None and hasattr(shadow, 'can_read'):
                            target_visible = shadow.can_read(request) or set()
                            # Prune payload by target model visibility; keep identifiers
                            keep_always = {'id', 'id_field', SHORT_DESCR_NAME}
                            pruned = {k: v for k, v in payload.items() if k in target_visible or k in keep_always}
                            if "updates" in payload:
                                pruned_updates = {k: v for k, v in payload['updates'].items() if k in target_visible or k in keep_always}
                                pruned['updates'] = pruned_updates

                            representation['payload'] = pruned
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

    all_fields = list(fields) + [ID_FIELD_NAME, SHORT_DESCR_NAME, "id"]

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
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id"):
            if extra not in existing:
                existing.append(extra)
        new_fields = existing
    else:
        new_fields = "__all__"
    NewMeta = type("Meta", (meta,),
                   {"model": model_class, "fields": new_fields, "list_serializer_class": FilteredListSerializer})
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
