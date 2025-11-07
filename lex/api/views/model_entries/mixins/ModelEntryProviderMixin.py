from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.exceptions import APIException
from rest_framework import serializers
from django.contrib.auth.models import User
from lex.audit_logging.models.calculation_log import (
    CalculationLog,
)  # Import your CalculationLog model
from lex.api.views.permissions.UserPermission import UserPermission


class UserModelSerializer(serializers.ModelSerializer):
    id_field = serializers.ReadOnlyField(default=User._meta.pk.name)
    short_description = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = "__all__"

    def get_short_description(self, obj):
        return f"{obj.first_name} {obj.last_name} - {obj.email}"

class ModelEntryProviderMixin:
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def get_queryset(self):
        return self.kwargs["model_container"].model_class.objects.all()

    def get_serializer_class(self):
        """
        Chooses serializer based on `?serializer=<name>`, defaulting to 'default'.
        """
        container = self.kwargs["model_container"]
        choice = self.request.query_params.get("serializer", "default")
        mapping = container.serializers_map
        
        if issubclass(container.model_class, User):
            return UserModelSerializer

        if choice not in mapping:
            raise APIException(
                {
                    "error": f"Unknown serializer '{choice}' for model '{container.model_class._meta.model_name}'",
                    "available": list(mapping.keys()),
                }
            )

        return mapping[choice]