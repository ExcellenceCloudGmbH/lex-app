from django.db.models import (
    ForeignKey,
    IntegerField,
    FloatField,
    DecimalField,
    BooleanField,
    DateField,
    DateTimeField,
    FileField,
    ImageField,
    AutoField,
    JSONField
)
from lex.api.serializers import ID_FIELD_NAME, SHORT_DESCR_NAME
from lex.api.views.permissions.UserPermission import UserPermission
from lex.core.fields import PDFField, XLSXField
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import APIException
from rest_framework.fields import empty
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

DEFAULT_TYPE_NAME = "string"

# ModelField → API type
DJANGO_FIELD2TYPE_NAME = {
    ForeignKey: "foreign_key",
    IntegerField: "int",
    FloatField: "float",
    DecimalField: "float",
    BooleanField: "boolean",
    DateField: "date",
    DateTimeField: "date_time",
    FileField: "file",
    PDFField: "pdf_file",
    XLSXField: "xlsx_file",
    ImageField: "image_file",
    JSONField: "json",
}


def resolve_type_name(ftype):
    """Map a Django field class to its API type name, subclasses included."""
    # Walk the MRO instead of testing isinstance: it keeps the most derived
    # match, which matters because DateTimeField subclasses DateField and
    # ImageField subclasses FileField. An exact hit is still the first hit.
    for klass in ftype.__mro__:
        type_name = DJANGO_FIELD2TYPE_NAME.get(klass)
        if type_name is not None:
            return type_name
    return DEFAULT_TYPE_NAME


def normalise_choices(raw):
    """Flatten Django/DRF choices into the ``[{id, name}]`` the FE reads.

    Two shapes arrive here: Django hands a sequence of ``(value, label)``
    pairs, DRF's ``ChoiceField`` hands a mapping. Django also allows grouped
    choices — ``[("Group", [(value, label), ...])]`` — which the SelectInput
    has no optgroup for, so groups are flattened to their members.

    Labels are forced through ``str`` because a translated label is a lazy
    proxy, which does not survive JSON serialisation.
    """
    if not raw:
        return None

    pairs = raw.items() if hasattr(raw, "items") else raw
    choices = []
    for value, label in pairs:
        if isinstance(label, (list, tuple)):
            choices.extend(
                {"id": sub_value, "name": str(sub_label)}
                for sub_value, sub_label in label
            )
        else:
            choices.append({"id": value, "name": str(label)})
    return choices or None


# DRF Field → API type (for serializer-only fields)
DRF_FIELD2TYPE_NAME = {
    drf_serializers.IntegerField: "int",
    drf_serializers.FloatField: "float",
    drf_serializers.BooleanField: "boolean",
    drf_serializers.DecimalField: "float",
    drf_serializers.DateField: "date",
    drf_serializers.DateTimeField: "date_time",
    drf_serializers.CharField: "string",
    drf_serializers.EmailField: "string",
    drf_serializers.URLField: "string",
    drf_serializers.PrimaryKeyRelatedField: "foreign_key",
    drf_serializers.JSONField: "json",
    drf_serializers.ListField: "json",
    drf_serializers.DictField: "json",
}


def create_field_info(field):
    """Turn a Django ModelField into the dict the FE expects."""
    default = None
    try:
        default = field.get_default()
    except (AttributeError, NotImplementedError):
        pass

    ftype = type(field)

    additional_info = {}
    # isinstance, not ==: OneToOneField subclasses ForeignKey and resolves to
    # "foreign_key", so it needs the target the FK renderer reads. An exact
    # check gave it the type without the target, which renders nothing.
    if isinstance(field, ForeignKey):
        additional_info['target'] = field.remote_field.model._meta.model_name
        additional_info['limit_choices_to'] = field.remote_field.limit_choices_to

    # BUG-F-006: without this the FE has nothing to build a SelectInput from,
    # falls through to a free-text input, and lets users persist values the
    # column does not allow.
    choices = normalise_choices(getattr(field, "choices", None))
    if choices is not None:
        additional_info['choices'] = choices

    info = {
        "name": field.name,
        "readable_name": field.verbose_name.title(),
        "type": resolve_type_name(ftype),
        "editable": field.editable and not isinstance(field, AutoField),
        # `blank`, not just `null`: Django decides form-required from `blank`
        # and DB-nullability from `null`, and DRF's ModelSerializer requires a
        # field only when none of blank/null/has_default apply. Reading
        # `default is not None` instead of `has_default()` was the actual
        # BUG-F-008: Django synthesises a `""` default for any CharField, so
        # that term was always true and EVERY char column reported optional,
        # blank=False included.
        "required": not (field.blank or field.null or field.has_default()),
        "default_value": default,
        'is_pk': bool(field.primary_key),
        **additional_info
    }

    return info


def create_list_ui_info(serializer):
    """Expose serializer-level list UI configuration to the frontend."""
    serializer_class = serializer if isinstance(serializer, type) else serializer.__class__
    getter = getattr(serializer_class, "get_list_ui_options", None)

    if callable(getter):
        return getter()

    meta = getattr(serializer_class, "Meta", None)
    return {
        "hide_actions_column": bool(getattr(meta, "hide_actions_column", False)),
        "filter_bar": [],
    }


class Fields(APIView):
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def get(self, request, *args, **kwargs):
        container = kwargs["model_container"]
        model = container.model_class
        serializer_name = request.query_params.get("serializer", "default")
        serializers_map = (
            container.get_serializers_map()
            if hasattr(container, "get_serializers_map")
            else container.serializers_map
        )

        if serializer_name not in serializers_map:
            raise APIException(
                {
                    "error": f"Unknown serializer '{serializer_name}' for model '{model._meta.model_name}'",
                    "available": list(serializers_map.keys()),
                }
            )

        serializer = serializers_map[serializer_name]()
        fields_info = []

        # hide internal-only fields
        excluded = {ID_FIELD_NAME, SHORT_DESCR_NAME}

        # Check for explicit type overrides on the serializer Meta
        meta = getattr(serializer, 'Meta', None)
        field_type_overrides = getattr(meta, 'lex_field_type_overrides', {}) or {}

        for fname, drf_field in serializer.fields.items():
            if fname in excluded:
                continue

            source = drf_field.source or fname

            # 1) Try Django model field first
            try:
                mfield = model._meta.get_field(source)
                info = create_field_info(mfield)
                # Real DB-backed columns can be used as AG Grid row-group
                # / pivot keys (the backend SSRM endpoint runs
                # ``qs.values(field).annotate(...)`` against them).
                info["is_groupable"] = True

            except Exception:
                # 2) Fallback: derive entirely from the DRF field
                # Determine type
                ftype = DEFAULT_TYPE_NAME
                for cls, api_type in DRF_FIELD2TYPE_NAME.items():
                    if isinstance(drf_field, cls):
                        ftype = api_type
                        break

                # Sanitize default
                raw_def = getattr(drf_field, "default", None)
                if raw_def is empty or isinstance(raw_def, type):
                    default_value = None
                elif isinstance(raw_def, (str, int, float, bool)):
                    default_value = raw_def
                else:
                    default_value = None

                info = {
                    "name": fname,
                    "readable_name": getattr(drf_field, "label", fname).title(),
                    "type": field_type_overrides.get(fname, ftype),
                    "editable": not getattr(drf_field, "read_only", False),
                    "required": getattr(drf_field, "required", False),
                    "default_value": default_value,
                    # Serializer-only fields (e.g. ``SerializerMethodField``,
                    # computed properties) have no underlying Django column,
                    # so the SSRM ``_execute_group_level`` /
                    # ``_execute_pivot_mode`` paths cannot group/pivot on
                    # them — flag them so the frontend can disable
                    # ``enableRowGroup`` / ``enablePivot`` on the column.
                    "is_groupable": False,
                }

                # A serializer-declared ChoiceField deserves the same
                # SelectInput as a model one; leaving it out here would
                # reproduce BUG-F-006 in the fallback branch. DRF hands
                # a mapping rather than pairs, which normalise_choices
                # takes as-is.
                drf_choices = normalise_choices(
                    getattr(drf_field, "choices", None)
                )
                if drf_choices is not None:
                    info["choices"] = drf_choices

                # Related-field target
                if isinstance(drf_field, drf_serializers.PrimaryKeyRelatedField):
                    try:
                        info["target"] = drf_field.queryset.model._meta.model_name
                    except Exception:
                        pass

            fields_info.append(info)

        return Response(
            {
                "fields": fields_info,
                "id_field": model._meta.pk.name,
                "list_ui": create_list_ui_info(serializer),
            }
        )
