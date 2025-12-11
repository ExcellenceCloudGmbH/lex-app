from rest_framework import serializers as drf_serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import APIException
from rest_framework.fields import empty
from django.db.models import (
    ForeignKey,
    IntegerField,
    FloatField,
    BooleanField,
    DateField,
    DateTimeField,
    FileField,
    ImageField,
    AutoField,
    JSONField
)
from lex.api.fields import BokehField, HTMLField, PDFField, XLSXField
from lex.api.views.permissions.UserPermission import UserPermission
from lex.api.serializers import ID_FIELD_NAME, SHORT_DESCR_NAME


DEFAULT_TYPE_NAME = "string"

# ModelField → API type
DJANGO_FIELD2TYPE_NAME = {
    ForeignKey: "foreign_key",
    IntegerField: "int",
    FloatField: "float",
    BooleanField: "boolean",
    DateField: "date",
    DateTimeField: "date_time",
    FileField: "file",
    PDFField: "pdf_file",
    XLSXField: "xlsx_file",
    HTMLField: "html",
    BokehField: "bokeh",
    ImageField: "image_file",
    JSONField: "json",
}

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
    if ftype == ForeignKey:
        additional_info['target'] = field.remote_field.model._meta.model_name
        additional_info['limit_choices_to'] = field.remote_field.limit_choices_to

    info = {
        "name": field.name,
        "readable_name": field.verbose_name.title(),
        "type": DJANGO_FIELD2TYPE_NAME.get(ftype, DEFAULT_TYPE_NAME),
        "editable": field.editable and not isinstance(field, AutoField),
        "required": not (field.null or default is not None),
        "default_value": default,
        'is_pk': bool(field.primary_key),
        **additional_info
    }

    return info


class Fields(APIView):
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def get(self, request, *args, **kwargs):
        container = kwargs["model_container"]
        model = container.model_class
        serializer_name = request.query_params.get("serializer", "default")
        serializers_map = container.serializers_map

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

        for fname, drf_field in serializer.fields.items():
            if fname in excluded:
                continue

            source = drf_field.source or fname

            # 1) Try Django model field first
            try:
                mfield = model._meta.get_field(source)
                info = create_field_info(mfield)

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
                    "type": ftype,
                    "editable": not getattr(drf_field, "read_only", False),
                    "required": getattr(drf_field, "required", False),
                    "default_value": default_value,
                }

                # Related-field target
                if isinstance(drf_field, drf_serializers.PrimaryKeyRelatedField):
                    try:
                        info["target"] = drf_field.queryset.model._meta.model_name
                    except Exception:
                        pass

            fields_info.append(info)

        return Response({"fields": fields_info, "id_field": model._meta.pk.name})
