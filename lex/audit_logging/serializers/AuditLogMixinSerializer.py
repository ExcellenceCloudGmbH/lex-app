from django.utils.functional import Promise  # Lazy translation objects
from uuid import UUID

from django.core.files.uploadedfile import UploadedFile
from django.db.models import Model
from django.db.models.fields.files import FieldFile
from django.forms.models import model_to_dict
from django.utils.functional import Promise  # Lazy translation objects


# Strict ISO 8601 without microseconds; strip tz to match 'YYYY-MM-DDTHH:MM:SS'
def _iso_seconds(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


def _serialize_file_reference(value) -> dict:
    name = getattr(value, "name", None)
    try:
        url = value.url if name else None
    except (AttributeError, ValueError):
        url = None
    return {"name": name, "url": url}

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
            data[k] = _serialize_file_reference(v)
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

    elif isinstance(data, Promise):  # Lazy translation strings
        return str(data)

        # 4. Handle Files (Base class catches both InMemory and Temporary)
    elif isinstance(data, UploadedFile):
        return {
            'name': getattr(data, 'name', 'unknown'),
            'size': getattr(data, 'size', 0),
            'content_type': getattr(data, 'content_type', 'unknown')
        }
    elif isinstance(data, FieldFile):
        return _serialize_file_reference(data)
    elif hasattr(data, "name") and hasattr(type(data), "url"):
        return _serialize_file_reference(data)

    elif isinstance(data, Model):
        return {'id': data.pk, 'display': str(data)}
    elif isinstance(data, Promise):
        return str(data)
    elif hasattr(data, 'all') and callable(data.all):
        # Possibly a QuerySet or related manager, return a serialized list.
        return [_serialize_payload(item) for item in data.all()]
    elif isinstance(data, set):
        return list(data)

    try:
        return str(data)
    except Exception:
        # In the extremely rare case __str__ fails, use repr or a placeholder
        return f"<Unserializable: {type(data).__name__}>"
