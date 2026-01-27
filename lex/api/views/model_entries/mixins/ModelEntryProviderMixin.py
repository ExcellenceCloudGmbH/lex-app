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
        from lex.core.services.bitemporal import get_queryset_as_of
        from django.utils.dateparse import parse_datetime
        from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer
        from lex.process_admin.utils.temporal_reconciler import TemporalReconciler
        from django.utils import timezone
        
        model_class = self.kwargs["model_container"].model_class
        queryset = model_class.objects.all()
        
        as_of_param = self.request.query_params.get("as_of")
        if as_of_param:
            as_of_date = parse_datetime(as_of_param)
            if as_of_date:
                # Use the bitemporal helper to get the historical snapshot
                queryset = get_queryset_as_of(model_class, as_of_date)
        else:
             pass
            # "Current Time" Request - Opportunity for Read Repair / Reconciliation
            
            # 1. Detail View: Check if we are requesting a specific ID
            # lookup_url_kwarg = getattr(self, 'lookup_url_kwarg', None) or getattr(self, 'lookup_field', 'pk')
            # pk = self.kwargs.get(lookup_url_kwarg)
            
            # if pk:
            #     # Sync SPECIFIC ID (Read-Repair)
            #     # Ensure main table is up to date for this record
            #     BitemporalSynchronizer.sync_record_for_model(model_class, pk)
            # else:
            #     # 2. List View: "Reconcile changes upon get request"
            #     # Doing full table scan is expensive. 
            #     # Strategy: Reconcile records that became valid in the last X minutes?
            #     # This covers the "I just waited for it to become valid" test case.
            #     # Let's say last 1 hour for safety in this "test mode".
            #     now = timezone.now()
            #     start_window = now - timezone.timedelta(hours=1)
            #     TemporalReconciler.reconcile_model_window(model_class, start_window, now)
        
        return queryset

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