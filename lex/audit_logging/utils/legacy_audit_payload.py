from uuid import UUID


def parse_record_id(value: str | None):
    """
    Parse a record identifier from calculation_record suffix.

    We only treat numeric IDs and UUIDs as reliable record IDs to avoid false
    positives (e.g. strings like "legacy_user_change").
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    try:
        UUID(raw)
        return raw
    except (ValueError, TypeError):
        return None


def extract_resource_and_record_id(calculation_record: str | None):
    """
    Extract resource(model name) and record id from calculation_record.

    Expected canonical form is "<model_name>_<pk>" (e.g. "invoice_42"), which is
    how current calculation contexts are built in runtime code.
    """
    if not calculation_record:
        return None, None

    raw = str(calculation_record).strip()
    if not raw:
        return None, None

    if "_" not in raw:
        return raw.lower(), None

    resource_candidate, suffix = raw.rsplit("_", 1)
    parsed_id = parse_record_id(suffix)
    if parsed_id is None or not resource_candidate:
        return raw.lower(), None

    return resource_candidate.lower(), parsed_id


def build_legacy_calculation_payload(row, reason: str):
    resource, record_id = extract_resource_and_record_id(row.calculation_record)
    payload = {
        "legacy_source": row._meta.db_table,
        "reason": reason,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "message_type": row.message_type,
        "message": row.message,
        "method": row.method,
        "is_notification": bool(row.is_notification),
        "calculation_record": row.calculation_record,
    }
    if record_id is not None:
        payload["id"] = record_id
    return payload, resource, record_id


def build_legacy_user_change_payload(row, reason: str):
    resource, record_id = extract_resource_and_record_id(row.calculation_record)
    payload = {
        "legacy_source": row._meta.db_table,
        "reason": reason,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "message": row.message,
        "traceback": row.traceback,
        "calculation_record": row.calculation_record,
    }
    if record_id is not None:
        payload["id"] = record_id
    return payload, resource, record_id


def merge_model_and_legacy_payload(
    model_payload: dict | None, legacy_payload: dict
) -> dict:
    """
    Merge model snapshot payload with legacy metadata payload.

    Model fields are kept authoritative to stay close to standard CRUD audit payloads.
    If a key already exists in model payload, the legacy value is preserved under
    `legacy_<key>` to avoid data loss.
    """
    merged = dict(model_payload or {})

    if "id" not in merged and "id" in legacy_payload:
        merged["id"] = legacy_payload["id"]

    for key, value in legacy_payload.items():
        if key == "id" and "id" in merged:
            continue
        if key in merged:
            merged[f"legacy_{key}"] = value
        else:
            merged[key] = value

    return merged
