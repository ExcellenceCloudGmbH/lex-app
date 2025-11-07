import json
from io import BytesIO
from typing import List, Set

import pandas as pd
from django.http import FileResponse
from django.db.models import QuerySet
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.filters import UserReadRestrictionFilterBackend, ForeignKeyFilterBackend
from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
from lex.process_admin.models.utils import get_relation_fields


class ModelExportView(GenericAPIView):
    filter_backends = [UserReadRestrictionFilterBackend, PrimaryKeyListFilterBackend, ForeignKeyFilterBackend]
    model_collection = None
    http_method_names = ['post']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get_exportable_fields_for_object(self, obj, request):
        """Get the set of exportable fields for a single object"""
        try:
            # Use new permission system if available
            if hasattr(obj, 'permission_export'):
                from lex.core.models.base import UserContext
                user_context = UserContext.from_request(request, obj)
                result = obj.permission_export(user_context)
                if result.allowed:
                    all_fields = {f.name for f in obj._meta.fields}
                    exportable_fields = result.get_fields(all_fields)
                else:
                    exportable_fields = set()
            # Fallback to legacy method
            elif hasattr(obj, 'can_export'):
                exportable_fields = obj.can_export(request)
                if exportable_fields is None:
                    exportable_fields = set()
                elif not isinstance(exportable_fields, set):
                    exportable_fields = set(exportable_fields)
            else:
                # Default to all fields if no permission method
                exportable_fields = {f.name for f in obj._meta.fields}
        except Exception:
            # Default to all fields on error
            exportable_fields = {f.name for f in obj._meta.fields}
        
        # Always include basic identifying fields
        return exportable_fields.union({'id', 'created_by', 'edited_by'})

    def filter_and_mask_data_for_export(self, queryset: QuerySet, request) -> pd.DataFrame:
        """
        Create DataFrame with field-level export permissions applied.
        Only includes rows that have at least some exportable fields.
        """

        # Get all field names for the model
        model = queryset.model
        all_fields = [field.name for field in model._meta.fields]

        # Process objects and build data with field-level masking
        export_data = []

        # Process in batches to avoid memory issues
        batch_size = 1000
        total_count = queryset.count()

        for i in range(0, total_count, batch_size):
            batch = queryset[i:i + batch_size]

            for obj in batch:
                exportable_fields = self.get_exportable_fields_for_object(obj, request)

                # Only include rows that have at least some exportable fields
                if exportable_fields:
                    # Create row data with field masking
                    row_data = {}
                    obj_values = {field.name: getattr(obj, field.name) for field in model._meta.fields}

                    for field_name in all_fields:
                        if field_name in exportable_fields:
                            # Field is exportable, include its value
                            row_data[field_name] = obj_values[field_name]
                        else:
                            # Field is not exportable, mask it (empty/None)
                            row_data[field_name] = None  # or '' for empty string

                    export_data.append(row_data)

        # Create DataFrame from the processed data
        if export_data:
            df = pd.DataFrame(export_data)
        else:
            # No exportable data - create empty DataFrame
            df = pd.DataFrame(columns=all_fields)

        return df

    def post(self, request, *args, **kwargs):
        model_container = kwargs['model_container']
        model = model_container.model_class
        queryset = ForeignKeyFilterBackend().filter_queryset(request, model.objects.all(), None)
        queryset = UserReadRestrictionFilterBackend()._filter_queryset(request, queryset, model_container)

        # Fix: Use request.data instead of request.body
        json_data = request.data

        if json_data.get("filtered_export") is not None:
            queryset = PrimaryKeyListFilterBackend().filter_for_export(json_data, queryset, self)

        # Apply field-level export permission filtering and masking
        df = self.filter_and_mask_data_for_export(queryset, request)

        # Check if there's any data to export
        if df.empty:
            from django.http import JsonResponse
            return JsonResponse({'error': 'No data available for export'}, status=404)

        # Handle foreign key relationships for non-masked fields
        relationfields = get_relation_fields(model)

        for field in relationfields:
            fieldName = field.attname

            # Only process foreign key fields that have non-null values (not masked)
            if fieldName in df.columns and not df[fieldName].isna().all():
                fieldObjects = field.remote_field.model.objects.all()
                fieldObjectsDict = {v.pk: str(v) for v in fieldObjects}

                # Map foreign keys, preserving None/masked values
                df[fieldName] = df[fieldName].map(lambda x: fieldObjectsDict.get(x) if pd.notna(x) else None)

        excel_file = BytesIO()
        writer = pd.ExcelWriter(excel_file, engine='xlsxwriter')

        df.to_excel(writer, sheet_name=model.__name__, merge_cells=False, freeze_panes=(1, 1), index=True)

        writer.close()
        excel_file.seek(0)

        return FileResponse(excel_file)