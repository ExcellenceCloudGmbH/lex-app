from io import BytesIO

import pandas as pd
from django.core.files.storage import default_storage
from pandas.api.types import is_datetime64_any_dtype as is_datetime

from lex.api.fields import XLSXField


def convert_dfs_in_excel(path, data_frames, sheet_names=None, merge_cells=False, formats={}, index=True):
    """
    :param path: string to storage location of xlsx file
    :param data_frames: list of dataframes that will be inserted into an Excel tab each
    :param sheet_names: list of sheet names corresponding to the data_frames
    :rtype: None
    """
    if sheet_names is None:
        sheet_names = ['Sheet']
    excel_file = BytesIO()
    writer = pd.ExcelWriter(excel_file, engine='xlsxwriter')
    df: pd.DataFrame
    for df, sheet_name in zip(data_frames, sheet_names):
        if df is not None:
            if index:
                idx_length = df.index.nlevels
            else:
                idx_length = 0
            df.to_excel(writer, sheet_name=sheet_name, merge_cells=merge_cells, freeze_panes=(1, idx_length), index=index)

            worksheet = writer.sheets[sheet_name]  # pull worksheet object
            cell_formats = {}
            for format in formats:
                cell_formats[format] = writer.book.add_format({'num_format': formats[format]})
            if index:
                index_frame = df.index.to_frame()
                for idx, col in enumerate(index_frame):  # loop through all columns
                    series = index_frame[col]
                    max_len = max((series.astype(str).map(len).max(), len(str(series.name)))) + 1  # adding a little extra space
                    if is_datetime(series):
                        max_len = 22
                    worksheet.set_column(idx, idx, max_len)  # set column width

            for idx, col in enumerate(df):  # loop through all columns
                series = df[col]
                max_len = max((
                    series.astype(str).map(len).max(), len(str(series.name)))) + 1  # adding a little extra space
                worksheet.set_column(idx + idx_length, idx + idx_length, max_len)  # set column width
                # set Cell format
                if col in formats:
                    worksheet.set_column(idx + idx_length, idx + idx_length, max_len, cell_format=cell_formats[col])
                elif is_datetime(df[col]):
                    pass
                else:
                    worksheet.set_column(idx + idx_length, idx + idx_length, max_len, cell_format=writer.book.add_format({'num_format': XLSXField.cell_format}))
            # Add autofilter:
            worksheet.autofilter(0, 0, len(df), idx_length + len(df.columns))

    writer.save()
    writer.close()

    # Extract the Excel file contents from BytesIO
    excel_file_contents = excel_file.getvalue()

    # Save the Excel file contents to the specified path
    with default_storage.open(path, 'wb') as output_excel_file:
        output_excel_file.write(excel_file_contents)



from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal
from django.apps import apps
from django.db.models import Model
from django.db.models.fields import DateTimeField, DateField, TimeField

ISO_PARSE = ("%Y-%m-%dT%H:%M:%S",)

def _parse_value(field, value):
    if value is None:
        return None
    
    # Handle foreign key relationships stored as dictionaries
    from django.db.models import ForeignKey
    if isinstance(field, ForeignKey) and isinstance(value, dict):
        # Extract the ID from foreign key dictionary representation
        if 'id' in value:
            return value['id']  # Return just the ID for foreign key assignment
        return None
    
    if isinstance(field, DateTimeField):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    if isinstance(field, DateField):
        try:
            return date.fromisoformat(value)
        except Exception:
            return None
    if isinstance(field, TimeField):
        try:
            return time.fromisoformat(value)
        except Exception:
            return None
    # Accept JSON primitives for other field types; Decimal/UUID as strings
    if isinstance(value, str):
        try:
            return UUID(value)
        except Exception:
            pass
        try:
            return Decimal(value)
        except Exception:
            pass
    return value

def resolve_target_model(audit_log):
    """
    Resolve the Django model class from an audit log entry.
    
    Tries multiple approaches to find the correct model:
    1. Use content_type if available (most reliable)
    2. Match resource name to model name (fallback)
    3. Match resource name to model class name (additional fallback)
    
    Args:
        audit_log: Audit log instance with content_type or resource field
        
    Returns:
        Django model class or None if not found
    """
    # Prefer content_type when present (most reliable)
    ct = getattr(audit_log, "content_type", None)
    if ct:
        try:
            return ct.model_class()
        except Exception:
            pass
    
    # Fallback: map resource string to model
    resource = getattr(audit_log, "resource", None)
    if resource:
        resource_lower = resource.lower()
        
        for model in apps.get_models():
            # Try matching model_name first
            if model._meta.model_name.lower() == resource_lower:
                return model
            
            # Try matching class name as fallback
            if model.__name__.lower() == resource_lower:
                return model
    
    return None

def build_shadow_instance(model_class: type[Model], payload: dict) -> Model | None:
    """
    Create a model instance from audit log payload data for permission checking.
    
    This creates a "shadow" instance that contains the field values from the audit log
    but is not saved to the database. It's used to check permissions against the
    data that was logged.
    
    Args:
        model_class: The Django model class to instantiate
        payload: Dictionary of field values from the audit log
        
    Returns:
        Model instance or None if creation fails
    """
    try:
        if not payload:
            return None
            
        field_map = {f.name: f for f in model_class._meta.concrete_fields}
        init_kwargs = {}
        
        for key, val in payload.items():
            if key in field_map:
                field = field_map[key]
                parsed_val = _parse_value(field, val)
                if parsed_val is not None or val is None:  # Allow explicit None values
                    # For foreign key fields, use the _id suffix
                    from django.db.models import ForeignKey
                    if isinstance(field, ForeignKey) and not key.endswith('_id'):
                        init_kwargs[f"{key}_id"] = parsed_val
                    else:
                        init_kwargs[key] = parsed_val
        
        # Ensure primary key if provided
        pk_name = model_class._meta.pk.name
        if pk_name in payload and pk_name not in init_kwargs:
            init_kwargs[pk_name] = payload[pk_name]
            
        return model_class(**init_kwargs)
        
    except Exception as e:
        # Log the error for debugging but don't raise
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Failed to build shadow instance for {model_class.__name__}: {e}")
        return None

def can_read_from_payload(request, audit_log) -> bool:
    """
    Check if user can read an audit log entry based on the target model's permissions.
    
    This function creates a shadow instance from the audit log payload and checks
    if the user has read permission for that type of record.
    
    Uses the new LexModel authorization system with fallback to legacy methods.
    """
    model_class = resolve_target_model(audit_log)
    if not model_class:
        return True  # preserve allow-by-default behavior
    
    instance = build_shadow_instance(model_class, getattr(audit_log, "payload", None))
    if instance is None:
        return True  # preserve allow-by-default behavior
    
    try:
        # Try new permission system first
        if hasattr(instance, 'permission_read'):
            from lex.core.models.base import UserContext
            user_context = UserContext.from_request(request, instance)
            result = instance.permission_read(user_context)
            return result.allowed
        
        # Fallback to legacy method
        elif hasattr(instance, 'can_read') and callable(instance.can_read):
            readable_fields = instance.can_read(request)
            # If can_read returns a set of fields, check if it's non-empty
            if isinstance(readable_fields, (set, list, tuple)):
                return len(readable_fields) > 0
            # If it returns a boolean, use that
            return bool(readable_fields)
        
        # No permission method found - allow by default
        return True
        
    except Exception:
        # Preserve allow-by-default behavior on any error
        return True
