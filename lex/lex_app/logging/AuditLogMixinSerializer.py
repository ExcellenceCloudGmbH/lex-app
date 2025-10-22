import datetime
from django.utils.functional import Promise  # Lazy translation objects
from django.core.files.uploadedfile import InMemoryUploadedFile


import datetime
from decimal import Decimal
from uuid import UUID
from django.forms.models import model_to_dict
from django.db.models.fields.files import FieldFile
from django.db.models import Model

# Strict ISO 8601 without microseconds; strip tz to match 'YYYY-MM-DDTHH:MM:SS'
def _iso_seconds(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()

def generic_instance_payload(instance: Model) -> dict:
    # Concrete DB fields as base
    field_names = [f.name for f in instance._meta.concrete_fields]
    data = model_to_dict(instance, fields=field_names)
    data["id"] = instance.pk

    # Normalize types
    for k, v in list(data.items()):
        if isinstance(v, datetime.datetime):
            data[k] = _iso_seconds(v)
        elif isinstance(v, datetime.date):
            data[k] = v.isoformat()
        elif isinstance(v, datetime.time):
            data[k] = v.replace(microsecond=0).isoformat()
        elif isinstance(v, Decimal):
            data[k] = str(v)
        elif isinstance(v, UUID):
            data[k] = str(v)
        elif isinstance(v, FieldFile):
            data[k] = {"name": v.name, "url": getattr(v, "url", None)}
        # ForeignKeys are already pk values via model_to_dict

    # Common computed attribute if present
    if "name" not in data and hasattr(instance, "name"):
        try:
            val = getattr(instance, "name")
            if isinstance(val, (str, int, float)):
                data["name"] = val
        except Exception:
            pass

    return data




def _serialize_payload(data):
    """
    Recursively process the data so it becomes JSON serializable.

    Handles:
      - dictionaries, lists
      - datetime, date, and time objects
      - Decimal and UUID fields
      - Django model instances
      - FieldFile and InMemoryUploadedFile (and similar file-type objects)
      - Lazy translation strings
      - QuerySets and sets
    """
    if isinstance(data, dict):
        return {key: _serialize_payload(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [_serialize_payload(item) for item in data]
    elif isinstance(data, datetime.datetime):
        return data.isoformat()
    elif isinstance(data, datetime.date):
        return data.isoformat()
    elif isinstance(data, datetime.time):
        return data.isoformat()
    elif isinstance(data, Decimal):
        return str(data)  # or float(data) if that fits your needs
    elif isinstance(data, UUID):
        return str(data)
    elif isinstance(data, FieldFile):
        return {'name': data.name, 'url': data.url if hasattr(data, 'url') else None}
    elif isinstance(data, InMemoryUploadedFile):
        # Serialize in-memory files by recording key metadata
        return {
            'name': data.name,
            'size': data.size,
            'content_type': data.content_type
        }
    elif isinstance(data, Model):
        return {'id': data.pk, 'display': str(data)}
    elif isinstance(data, Promise):
        return str(data)
    elif hasattr(data, 'all') and callable(data.all):
        # Possibly a QuerySet or related manager, return a serialized list.
        return [_serialize_payload(item) for item in data.all()]
    elif isinstance(data, set):
        return list(data)
    return data