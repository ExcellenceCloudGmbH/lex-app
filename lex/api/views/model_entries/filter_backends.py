import base64
from urllib.parse import parse_qs
from rest_framework import filters
from lex.api.utils import can_read_from_payload
from lex.core.models.LexModel import UserContext
from lex.api.serializers.base_serializers import LexSerializer, _get_capabilities


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

    def _handle_auditlog(self, request, queryset):
        excluded = []
        for row in queryset.iterator():
            try:
                if not can_read_from_payload(request, row):
                    excluded.append(row.pk)
            except Exception:
                pass  # allow-by-default on error
        if excluded:
            return queryset.exclude(pk__in=excluded)
        return queryset

    def _handle_lexmodel_default(self, request, queryset):
        # Build a cached base UserContext once for the entire queryset
        base_ctx = UserContext.from_request_base(request)

        excluded = []
        for instance in queryset.iterator():
            try:
                # Resolve target instance (handle Historical Records)
                caps = _get_capabilities(type(instance))

                if not caps['has_permission_read'] and not caps['has_can_read']:
                    target_instance = LexSerializer._unwrap_instance(instance)
                    target_caps = _get_capabilities(type(target_instance))
                else:
                    target_instance = instance
                    target_caps = caps

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
