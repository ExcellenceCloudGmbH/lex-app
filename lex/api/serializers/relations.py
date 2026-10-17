"""Relations by public id."""
from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.exceptions import NotFound


class LexLookupRelatedField(serializers.SlugRelatedField):
    """A foreign key read and written as the related record's public id (its ``lex_lookup_field``).

    ``LexLookupRelatedField(queryset=Fund.objects.all())`` or, read-only,
    ``LexLookupRelatedField(model=Fund, read_only=True)``. The queryset is
    evaluated per request, so a record the caller may not see (row-level
    security, ``permission_read_queryset``) is not found: the request answers
    404, as for any record outside the caller's access, unless
    ``missing="invalid"`` keeps DRF's 400 field error.
    """

    def __init__(self, model=None, missing="not_found", **kwargs):
        queryset = kwargs.get("queryset")
        target = model or (queryset.model if queryset is not None else None)
        if target is None:
            raise ImproperlyConfigured("LexLookupRelatedField needs a queryset or a model")
        if missing not in ("not_found", "invalid"):
            raise ImproperlyConfigured("LexLookupRelatedField(missing=...) must be 'not_found' or 'invalid'")
        if queryset is None and not kwargs.get("read_only"):
            kwargs["queryset"] = target._default_manager.all()
        self.target_model = target
        self.missing = missing
        super().__init__(slug_field=getattr(target, "lex_lookup_field", None) or "pk", **kwargs)

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            codes = exc.get_codes()
            if self.missing == "not_found" and "does_not_exist" in (codes if isinstance(codes, list) else [codes]):
                raise NotFound() from exc
            raise

    def to_representation(self, obj):
        value = super().to_representation(obj)
        return str(value) if value is not None else None
