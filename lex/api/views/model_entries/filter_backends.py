import base64
from collections.abc import Mapping
from urllib.parse import parse_qs

from django.core.exceptions import FieldDoesNotExist
from rest_framework import filters

from lex.api.serializers.base_serializers import LexSerializer, _get_capabilities
from lex.api.utils import can_read_from_payload
from lex.core.models.LexModel import LexModel, UserContext


class PrimaryKeyListFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        model_container = view.kwargs['model_container']
        ids = request.query_params.getlist('ids')
        if ids:
            ids_cleaned = [x for x in ids if x]
            return queryset.filter(**{f'{model_container.pk_name}__in': ids_cleaned})
        return queryset

    def filter_for_export(self, json_data, queryset, view):
        model_container = view.kwargs['model_container']
        decoded = base64.b64decode(json_data["filtered_export"]).decode("utf-8")
        params = parse_qs(decoded)
        ids = params.get('ids', [])
        if ids:
            ids_cleaned = [x for x in ids if x]
            return queryset.filter(**{f'{model_container.pk_name}__in': ids_cleaned})
        return queryset


class UserReadRestrictionFilterBackend(filters.BaseFilterBackend):
    ITERATOR_CHUNK_SIZE = 2000

    def filter_queryset(self, request, queryset, view):
        model_class = view.kwargs["model_container"].model_class
        name = model_class.__name__

        # AuditLogStatus and CalculationLog handlers always permit every row,
        # so skip the per-row loop entirely for them.
        if name == "AuditLogStatus":
            return queryset
        if name == "CalculationLog":
            return queryset
        if name == "AuditLog":
            return self._handle_auditlog(request, queryset)
        return self._handle_lexmodel_default(request, queryset)

    def _get_default_permission_target(self, model_class):
        target_model = None
        lookup_field = None

        if isinstance(model_class, type) and issubclass(model_class, LexModel):
            target_model = model_class
            lookup_field = model_class._meta.pk.name
        else:
            candidate = getattr(model_class, "instance_type", None)
            if isinstance(candidate, type) and issubclass(candidate, LexModel):
                target_model = candidate
                lookup_field = candidate._meta.pk.name
                try:
                    model_class._meta.get_field(lookup_field)
                except FieldDoesNotExist:
                    return None, None

        if target_model is None:
            return None, None

        # Fast-path is only valid for the default LexModel permission implementation.
        if getattr(target_model, "permission_read", None) is not LexModel.permission_read:
            return None, None

        return target_model, lookup_field

    def _apply_default_permission_read_filter(self, request, queryset, target_model, lookup_field):
        resource_name = f"{target_model._meta.app_label}.{target_model.__name__}"
        user_permissions = getattr(request, "user_permissions", ()) or ()

        has_global_read = False
        allowed_ids = set()

        for perm in user_permissions:
            if not isinstance(perm, Mapping):
                continue
            if perm.get("rsname") != resource_name:
                continue

            scopes = perm.get("scopes") or ()
            if "read" not in scopes:
                continue

            resource_set_id = perm.get("resource_set_id")
            if resource_set_id is None:
                has_global_read = True
                break
            allowed_ids.add(resource_set_id)

        if has_global_read:
            return queryset
        if not allowed_ids:
            return queryset.none()

        pk_field = target_model._meta.pk
        normalized_ids = []
        for raw_id in allowed_ids:
            try:
                normalized_ids.append(pk_field.to_python(raw_id))
            except Exception:
                normalized_ids.append(raw_id)

        return queryset.filter(**{f"{lookup_field}__in": normalized_ids})

    def _handle_auditlog(self, request, queryset):
        base_user_context = UserContext.from_request_base(request)
        excluded = []
        for row in queryset.iterator(chunk_size=self.ITERATOR_CHUNK_SIZE):
            try:
                if not can_read_from_payload(request, row, base_user_context=base_user_context):
                    excluded.append(row.pk)
            except Exception:
                pass  # allow-by-default on error
        if excluded:
            return queryset.exclude(pk__in=excluded)
        return queryset

    def _handle_lexmodel_default(self, request, queryset):
        target_model, lookup_field = self._get_default_permission_target(queryset.model)
        if target_model is not None and lookup_field:
            return self._apply_default_permission_read_filter(
                request=request,
                queryset=queryset,
                target_model=target_model,
                lookup_field=lookup_field,
            )

        # Build a cached base UserContext once for the entire queryset
        base_ctx = UserContext.from_request_base(request)
        model_caps = _get_capabilities(queryset.model)
        needs_unwrap = not model_caps["has_permission_read"] and not model_caps["has_can_read"]
        unwrapped_caps_cache = {}

        excluded = []
        for instance in queryset.iterator(chunk_size=self.ITERATOR_CHUNK_SIZE):
            try:
                # Resolve target instance (handle Historical Records)
                if needs_unwrap:
                    target_instance = LexSerializer._unwrap_instance(instance)
                    target_class = type(target_instance)
                    target_caps = unwrapped_caps_cache.get(target_class)
                    if target_caps is None:
                        target_caps = _get_capabilities(target_class)
                        unwrapped_caps_cache[target_class] = target_caps
                else:
                    target_instance = instance
                    target_caps = model_caps

                # Check new permission system
                if target_caps['has_permission_read']:
                    user_context = base_ctx.with_instance(request, target_instance)
                    result = target_instance.permission_read(user_context)
                    if not result.allowed:
                        excluded.append(instance.pk)
                # Fallback to legacy method
                elif target_caps['has_can_read'] and callable(target_instance.can_read):
                    if not target_instance.can_read(request):
                        excluded.append(instance.pk)
                # else: allow by default if no permission method
            except Exception:
                pass  # allow by default on any error

        if excluded:
            return queryset.exclude(pk__in=excluded)
        return queryset
