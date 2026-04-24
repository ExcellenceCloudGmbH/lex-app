from django.db import models
from django.db.models import Model, ForeignKey
from django.db.models.fields import DateTimeField, DateField, TimeField
from django.apps import apps
from rest_framework import serializers, viewsets
import logging

from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal

from lex.api.utils.helpers import can_read_with_default_permission_scope
from lex.audit_logging.utils.content_types import safe_get_content_type
from lex.core.models.LexModel import LexModel, UserContext

logger = logging.getLogger(__name__)

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
    _base_user_context = None  # Cached UserContext without keycloak scopes
    _meta_fields_cache: dict = {}  # { model_class: set_of_field_names }
    _concrete_field_map_cache: dict = {}  # { model_class: { field_name: field } }

    def _get_base_user_context(self, request):
        """Get or create a base UserContext cached on this serializer instance."""
        if self._base_user_context is None:
            self._base_user_context = UserContext.from_request_base(request)
        return self._base_user_context

    def _get_user_context(self, request, target_instance, original_instance=None):
        """Create an instance-specific UserContext from the cached base.

        Args:
            request: The Django request.
            target_instance: The unwrapped LexModel instance.
            original_instance: The pre-unwrap instance (e.g. HistoricalQuarter)
                so keycloak scopes registered under either resource name are matched.
        """
        base = self._get_base_user_context(request)
        return base.with_instance(request, target_instance, original_instance=original_instance)

    @classmethod
    def _get_cached_field_names(cls, model_class) -> set:
        """Return cached set of field names for a model class."""
        fields = cls._meta_fields_cache.get(model_class)
        if fields is None:
            fields = {
                f.name
                for f in model_class._meta.get_fields()
                if not (f.auto_created and not f.concrete)
            }
            cls._meta_fields_cache[model_class] = fields
        return fields

    @classmethod
    def _get_cached_concrete_field_map(cls, model_class) -> dict:
        field_map = cls._concrete_field_map_cache.get(model_class)
        if field_map is None:
            field_map = {f.name: f for f in model_class._meta.concrete_fields}
            cls._concrete_field_map_cache[model_class] = field_map
        return field_map

    @staticmethod
    def _normalize_field_names(fields) -> set[str]:
        """
        Normalize field collections returned by legacy/new permission APIs.
        """
        if fields is None:
            return set()
        if isinstance(fields, str):
            return {fields}
        if isinstance(fields, (set, frozenset, list, tuple)):
            return {f for f in fields if isinstance(f, str)}
        return set()

    @classmethod
    def get_list_ui_options(cls) -> dict:
        """Expose serializer-level frontend list options via ``Meta``."""
        meta = getattr(cls, "Meta", None)
        return {
            "hide_actions_column": bool(getattr(meta, "hide_actions_column", False)),
        }

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

            # Resolve the underlying model instance for permission checks.
            # Keep a reference to the original instance so that keycloak
            # scopes registered under the historical resource name
            # (e.g. "core.HistoricalQuarter") are still matched after
            # unwrapping to the main model ("core.Quarter").
            original_instance = instance
            if not caps['has_permission_edit']:
                target_instance = self._unwrap_instance(instance)
                target_caps = _get_capabilities(type(target_instance))

                if not target_caps['has_permission_edit'] and not target_caps['has_can_edit']:
                    return {}
            else:
                target_instance = instance
                target_caps = caps

            # Create user context (reuses cached base)
            user_context = self._get_user_context(
                request, target_instance,
                original_instance=original_instance if original_instance is not target_instance else None,
            )

            # Get all field names (cached per model class)
            all_fields = self._get_cached_field_names(type(target_instance))

            # Get permissions
            if target_caps['has_permission_edit']:
                edit_result = target_instance.permission_edit(user_context)
                if hasattr(edit_result, "get_fields"):
                    edit_fields = self._normalize_field_names(edit_result.get_fields(all_fields))
                elif edit_result:
                    edit_fields = set(all_fields)
                else:
                    edit_fields = set()
            elif target_caps['has_can_edit']:
                raw_edit_fields = target_instance.can_edit(request)
                if raw_edit_fields is True:
                    edit_fields = set(all_fields)
                elif raw_edit_fields is False:
                    edit_fields = set()
                else:
                    edit_fields = self._normalize_field_names(raw_edit_fields)
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
            field_map = cls._get_cached_concrete_field_map(model_class)
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
    def _get_audit_log_related_permission_cache(cls, request) -> dict:
        if request is None:
            return {}

        cache = getattr(request, "_audit_log_related_permission_cache", None)
        if cache is None:
            cache = {}
            setattr(request, "_audit_log_related_permission_cache", cache)
        return cache

    @classmethod
    def _can_read_related_payload_reference(
            cls,
            request,
            related_model,
            value,
            base_user_context=None,
    ) -> bool:
        if not isinstance(value, dict):
            return True

        related_id = value.get("id")
        if related_id is None:
            return True

        if request is not None:
            fast_path_result = can_read_with_default_permission_scope(request, related_model, value)
            if fast_path_result is not None:
                return fast_path_result

        cache_key = (related_model, str(related_id))
        cache = cls._get_audit_log_related_permission_cache(request)
        if cache_key in cache:
            return cache[cache_key]

        allowed = True
        try:
            related_obj = related_model.objects.get(pk=related_id)

            if hasattr(related_obj, "permission_read"):
                if base_user_context is not None:
                    user_context = base_user_context.with_instance(request, related_obj)
                else:
                    user_context = UserContext.from_request(request, related_obj)
                result = related_obj.permission_read(user_context)
                allowed = result.allowed if hasattr(result, "allowed") else bool(result)
            elif hasattr(related_obj, "can_read"):
                readable_fields = related_obj.can_read(request)
                if isinstance(readable_fields, (set, list, tuple)):
                    allowed = len(readable_fields) > 0
                else:
                    allowed = bool(readable_fields)
        except Exception:
            allowed = True

        cache[cache_key] = allowed
        return allowed

    @classmethod
    def _filter_foreign_key_relations(
            cls,
            request,
            model_class,
            payload: dict,
            base_user_context=None,
    ) -> dict:
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
        field_map = cls._get_cached_concrete_field_map(model_class)

        for field_name, field_value in payload.items():
            if field_name in field_map:
                field = field_map[field_name]

                # Check if this is a foreign key field with dictionary representation
                if isinstance(field, ForeignKey) and isinstance(field_value, dict):
                    related_model = field.related_model
                    if not cls._can_read_related_payload_reference(
                            request,
                            related_model,
                            field_value,
                            base_user_context=base_user_context,
                    ):
                        filtered_payload.pop(field_name, None)
                        continue

        return filtered_payload

    @classmethod
    def _get_audit_log_payload_visible_fields(
            cls,
            request,
            model_class,
            payload,
            base_user_context=None,
    ) -> set[str] | None:
        if request is not None:
            fast_path_result = can_read_with_default_permission_scope(request, model_class, payload)
            if fast_path_result is not None:
                if not fast_path_result:
                    return set()
                return set(cls._get_cached_field_names(model_class))

        shadow = cls._build_shadow_instance(model_class, payload)
        if shadow is None:
            return None

        if hasattr(shadow, "permission_read"):
            if base_user_context is not None:
                user_context = base_user_context.with_instance(request, shadow)
            else:
                user_context = UserContext.from_request(request, shadow)
            result = shadow.permission_read(user_context)
            if hasattr(result, "allowed") and not result.allowed:
                return set()

            all_fields = cls._get_cached_field_names(type(shadow))
            if hasattr(result, "get_fields"):
                return cls._normalize_field_names(result.get_fields(all_fields))
            if result:
                return set(all_fields)
            return set()

        if hasattr(shadow, "can_read"):
            raw_visible_fields = shadow.can_read(request)
            if raw_visible_fields is True:
                return set(cls._get_cached_field_names(type(shadow)))
            if raw_visible_fields is False:
                return set()
            return cls._normalize_field_names(raw_visible_fields)

        return None

    @staticmethod
    def _resolve_target_model(audit_log) -> type[Model] | None:
        # Prefer content_type if present
        content_type_id = getattr(audit_log, "content_type_id", None)
        if content_type_id:
            try:
                ct = safe_get_content_type(
                    content_type_id=content_type_id,
                    using=getattr(getattr(audit_log, "_state", None), "db", None),
                )
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
            raw_visible_fields = target_instance.can_read(request)
            if raw_visible_fields is False:
                return {}
            if raw_visible_fields is True:
                visible_fields = self._get_cached_field_names(type(target_instance))
            else:
                visible_fields = self._normalize_field_names(raw_visible_fields)

        # 2. Try New System 'permission_read'
        elif target_caps['has_permission_read']:
            user_context = self._get_user_context(request, target_instance)
            result = target_instance.permission_read(user_context)
            if hasattr(result, "allowed") and not result.allowed:
                return {}  # Hide entirely

            all_fields = self._get_cached_field_names(type(target_instance))
            if hasattr(result, "get_fields"):
                visible_fields = self._normalize_field_names(result.get_fields(all_fields))
            elif result:
                visible_fields = set(all_fields)
            else:
                visible_fields = set()

        # 3. Fallback: All fields
        if visible_fields is None:
            visible_fields = self._get_cached_field_names(type(instance))
        else:
            visible_fields = self._normalize_field_names(visible_fields)

        if not visible_fields:
            return {}

        representation = super().to_representation(instance)

        # Filter non-AuditLog outputs by visible fields
        model_field_names = self._get_cached_field_names(type(target_instance))
        serializer_only_fields = set(self.fields.keys()) - model_field_names
        allowed_non_model_fields = self._SYSTEM_FIELDS | serializer_only_fields

        for field_name in list(representation.keys()):
            if field_name not in visible_fields and field_name not in allowed_non_model_fields:
                representation.pop(field_name, None)

        # AuditLog payload filtering using target model can_read
        try:
            if instance.__class__._meta.model_name.lower() == 'auditlog':
                payload = representation.get('payload') or getattr(instance, 'payload', None)
                if isinstance(payload, dict):
                    model_class = self._resolve_target_model(instance)
                    if model_class is not None:
                        base_user_context = self._get_base_user_context(request) if request else None
                        filtered_payload = self._filter_foreign_key_relations(
                            request,
                            model_class,
                            payload,
                            base_user_context=base_user_context,
                        )

                        target_visible = self._get_audit_log_payload_visible_fields(
                            request,
                            model_class,
                            filtered_payload,
                            base_user_context=base_user_context,
                        )
                        if target_visible is not None:
                            keep_always = {'id', 'id_field', SHORT_DESCR_NAME}
                            pruned = {k: v for k, v in filtered_payload.items() if
                                      k in target_visible or k in keep_always}
                            if "updates" in filtered_payload:
                                pruned_updates = {k: v for k, v in filtered_payload['updates'].items() if
                                                  k in target_visible or k in keep_always}
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
        hide_actions_column = False
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
    # Always declare ``id`` explicitly so the wrapped serializer's output
    # contains the model's primary-key value regardless of how the
    # developer's ``Meta.fields`` / ``Meta.exclude`` is shaped or whether
    # they declared a custom ``id`` field. The frontend SSRM datasource and
    # row-action handlers depend on this key to derive show/edit URLs and
    # to fire CRUD mutations (otherwise rows fall back to a synthetic
    # ``ssrm:groupPath:...`` id that breaks navigation and skips the
    # loading overlay).
    #
    # Edge case — non-``id`` primary key (e.g. django-simple-history's
    # ``HistoricalX`` whose PK is ``history_id`` while the source row's
    # ``id`` is preserved as a regular non-PK column): aliasing
    # ``id = ReadOnlyField(source=pk_attname)`` here would (a) replace the
    # natural ``id`` column from ``Meta.fields = "__all__"`` and (b) cause
    # ``Fields.py`` to resolve both ``id`` (now sourcing ``history_id``)
    # and the natural ``history_id`` field to the same Django column,
    # rendering the column twice and suppressing the source row's ``id``.
    # In that case we leave the natural ``id`` field intact and skip the
    # post-pass PK injection so the row's ``id`` value is the source row's
    # id, not the history PK. Frontends that need the history PK use
    # ``id_field`` (which already aliases to ``model._meta.pk.name``).
    pk_attname = model_class._meta.pk.attname
    pk_is_id = pk_attname == "id"

    attrs = {
        ID_FIELD_NAME: serializers.ReadOnlyField(default=model_class._meta.pk.name),
        SHORT_DESCR_NAME: serializers.SerializerMethodField(),
        "get_short_description": lambda self, obj: str(obj),
        "Meta": NewMeta,
    }
    # Use a bare ``ReadOnlyField()`` (DRF defaults ``source`` to the field
    # name) to avoid DRF's redundant-``source`` assertion when the field
    # name and source match. Only declared when the PK attname IS ``id``
    # so we don't collide with the natural ``id`` column on history-style
    # models whose PK attname is e.g. ``history_id`` (see comment above).
    if pk_is_id:
        attrs["id"] = serializers.ReadOnlyField()
    base_classes = (LexSerializer, custom_cls)
    wrapped_cls = type(
        f"{custom_cls.__name__}WithInternalFields", base_classes, attrs
    )

    # Post-pass: guarantee the row carries the model PK under ``id``. We do
    # this in addition to the declared ``id = ReadOnlyField(...)`` because
    # the developer's ``Meta.exclude`` or ``permission_read`` /
    # ``can_read`` result can drop declared fields during
    # ``LexSerializer.to_representation``'s visibility filter. The frontend
    # SSRM datasource and the show/edit/CRUD handlers rely on
    # ``record.id`` being the real PK — without it the row falls back to
    # a synthetic ``ssrm:groupPath:...`` id that breaks navigation and
    # suppresses the CRUD loading overlay.
    #
    # Skipped when the PK attname is not ``id`` (see comment above the
    # ``attrs`` block) — overwriting ``representation["id"]`` with the
    # non-``id`` PK value would clobber the natural ``id`` column carried
    # by history-style models that preserve the source row's id.
    if pk_is_id:
        _base_to_representation = wrapped_cls.to_representation

        def _to_representation_with_id(self, instance, _pk_attname=pk_attname):
            representation = _base_to_representation(self, instance)
            # Respect deny-all: ``LexSerializer.to_representation`` returns an
            # empty dict when ``can_read`` / ``permission_read`` denies the
            # row entirely. Injecting an ``id`` there would leak the PK.
            if isinstance(representation, dict) and representation and instance is not None:
                pk_value = getattr(instance, _pk_attname, None)
                if pk_value is not None:
                    representation["id"] = pk_value
            return representation

        wrapped_cls.to_representation = _to_representation_with_id
    return wrapped_cls


def _resolve_history_source_model(model_class):
    """Walk the ``instance_type`` chain to the originating non-history model.

    django-simple-history sets ``instance_type`` on every auto-generated
    historical model class (and ``MetaLevelHistoricalRecords`` chains it on
    the meta-history class), pointing back at the immediately tracked model.
    This helper walks that chain and returns the root non-history model so
    callers can mirror configuration (e.g. the ``api_serializers["default"]``
    override status) from the tracked source down to its history /
    meta-history tables.

    Returns ``None`` when ``model_class`` is itself the source (i.e. has no
    ``instance_type`` attribute or the chain does not advance past it).
    """
    if not hasattr(model_class, "instance_type"):
        return None
    seen = {model_class}
    current = model_class
    while True:
        nxt = getattr(current, "instance_type", None)
        if not isinstance(nxt, type) or nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return current if current is not model_class else None


def get_serializer_map_for_model(model_class, default_fields=None):
    serializers_map = {}

    auto_default = model2serializer(model_class, default_fields)

    custom = getattr(model_class, "api_serializers", None)
    has_custom_default_override = (
            isinstance(custom, dict) and "default" in custom
    )

    # History / meta-history tables of tracked models do not carry their own
    # ``api_serializers`` registration, but conceptually inherit the alias
    # decision from their tracked source model: if the developer overrides
    # ``api_serializers["default"]`` on the source, the framework
    # auto-generated serializer should also be exposed under the configured
    # alias on every history table that mirrors that source. This keeps
    # foreign-key / detail / history-snapshot lookups consistent across the
    # source model and its bitemporal tables.
    inherits_default_override_from_source = False
    if not has_custom_default_override:
        source_model = _resolve_history_source_model(model_class)
        if source_model is not None:
            source_custom = getattr(source_model, "api_serializers", None)
            if isinstance(source_custom, dict) and "default" in source_custom:
                inherits_default_override_from_source = True

    should_register_alias = (
        has_custom_default_override or inherits_default_override_from_source
    )

    if auto_default is not None:
        serializers_map["default"] = auto_default

        # If the project explicitly overrides the framework's auto-generated
        # "default" serializer for this model (or for the tracked source of
        # this history table), additionally expose the framework-generated
        # serializer under the project-configured alias (see
        # ``DEFAULT_SERIALIZER_NAME`` / ``default_serializer_name`` in
        # ``lex_config.py`` or ``_authentication_settings.py``). Models that
        # do not override "default" — and whose tracked source does not
        # either — keep the historical behavior: only the auto-generated
        # serializer is registered, under the "default" key.
        if should_register_alias:
            try:
                from lex.core.config import (
                    DEFAULT_SERIALIZER_NAME,
                    get_configured_default_serializer_name,
                )

                configured_name = get_configured_default_serializer_name()
            except Exception:
                configured_name = "default"
                DEFAULT_SERIALIZER_NAME = "default"

            alias_collides_with_custom = (
                isinstance(custom, dict) and configured_name in custom
            )

            if (
                configured_name
                and configured_name != DEFAULT_SERIALIZER_NAME
                and not alias_collides_with_custom
            ):
                serializers_map[configured_name] = auto_default

    if isinstance(custom, dict) and custom:
        for name, cls in custom.items():
            try:
                serializers_map[name] = _wrap_custom_serializer(cls, model_class)
            except Exception as exc:
                logger.warning(
                    "Skipping serializer '%s' for model '%s' due to wrapping error: %s",
                    name,
                    getattr(model_class, "__name__", model_class),
                    exc,
                )
    return serializers_map


def resolve_default_serializer_name(serializers_map):
    """Return the key under which the framework auto-generated serializer
    lives in ``serializers_map``.

    When a project has configured ``DEFAULT_SERIALIZER_NAME`` in
    ``lex_config.py`` / ``_authentication_settings.py`` and the developer
    has overridden the model's ``"default"`` serializer, the framework
    auto-generated serializer is registered under that configured alias
    by :func:`get_serializer_map_for_model`. In that case the alias is
    returned; otherwise ``"default"`` is returned for backward compatibility.

    This is the canonical lookup used by framework-internal endpoints
    (history snapshots, foreign-key reference loaders, model_info/Fields,
    obj_serializer wiring, etc.) that need the framework's full-fidelity
    serializer regardless of any developer override.
    """
    if not isinstance(serializers_map, dict) or not serializers_map:
        return "default"
    try:
        from lex.core.config import (
            DEFAULT_SERIALIZER_NAME,
            get_configured_default_serializer_name,
        )

        configured_name = get_configured_default_serializer_name()
    except Exception:
        return "default"

    if (
            configured_name
            and configured_name != DEFAULT_SERIALIZER_NAME
            and configured_name in serializers_map
    ):
        return configured_name
    return "default"


def resolve_requested_serializer_name(serializers_map, requested_name):
    """Resolve a serializer name requested by an API consumer.

    When the consumer asks for ``"default"`` (either explicitly via
    ``?serializer=default`` or implicitly by omitting the parameter), this
    routes the request to the framework auto-generated serializer using
    :func:`resolve_default_serializer_name`. Any other explicit name is
    returned unchanged so developers can still target their custom
    serializers (including their own ``api_serializers["default"]``
    override via the configured alias name).
    """
    if requested_name == "default":
        return resolve_default_serializer_name(serializers_map)
    return requested_name
