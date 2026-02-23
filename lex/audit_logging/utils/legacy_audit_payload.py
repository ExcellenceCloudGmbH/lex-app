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


def _coerce_model_pk(model_class, raw_value: str):
    """
    Coerce a raw suffix into the model's PK python value.

    Returns None when conversion fails.
    """
    try:
        coerced = model_class._meta.pk.to_python(raw_value)
    except Exception:
        return None

    if isinstance(coerced, UUID):
        return str(coerced)
    return coerced


def _pk_is_textual(model_class) -> bool:
    try:
        internal = model_class._meta.pk.get_internal_type()
    except Exception:
        return False
    return internal in {"CharField", "TextField", "SlugField"}


def _resolve_with_model_lookup(raw: str, model_lookup: dict | None):
    """
    Resolve <resource>_<pk> using registered models when numeric/UUID parsing
    is insufficient (e.g. textual PKs).
    """
    if not model_lookup:
        return None

    raw_lower = raw.lower()
    # Longest-first avoids ambiguous prefix matches.
    aliases = sorted(
        {str(alias).strip().lower() for alias in model_lookup.keys() if alias},
        key=len,
        reverse=True,
    )

    for alias in aliases:
        model_class = model_lookup.get(alias)
        if model_class is None:
            continue

        if raw_lower == alias:
            return model_class._meta.model_name.lower(), None

        prefix = f"{alias}_"
        if not raw_lower.startswith(prefix):
            continue

        raw_pk = raw[len(prefix):].strip()
        if not raw_pk:
            continue

        coerced_pk = _coerce_model_pk(model_class, raw_pk)
        if coerced_pk is None:
            continue

        instance = None
        try:
            instance = model_class._default_manager.filter(pk=coerced_pk).first()
        except Exception:
            instance = None

        # For textual PKs, require an existing record to avoid false positives
        # from strings like "legacy_user_change".
        if instance is not None:
            instance_pk = instance.pk
            if isinstance(instance_pk, UUID):
                instance_pk = str(instance_pk)
            return model_class._meta.model_name.lower(), instance_pk
        if not _pk_is_textual(model_class):
            return model_class._meta.model_name.lower(), coerced_pk

    return None


def extract_resource_and_record_id(
    calculation_record: str | None, model_lookup: dict | None = None
):
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

    if "_" in raw:
        resource_candidate, suffix = raw.rsplit("_", 1)
        parsed_id = parse_record_id(suffix)
        if parsed_id is not None and resource_candidate:
            return resource_candidate.lower(), parsed_id

    resolved = _resolve_with_model_lookup(raw, model_lookup)
    if resolved is not None:
        return resolved

    return raw.lower(), None


def build_legacy_calculation_payload(
    row, reason: str, model_lookup: dict | None = None
):
    resource, record_id = extract_resource_and_record_id(
        row.calculation_record, model_lookup=model_lookup
    )
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


def build_legacy_user_change_payload(
    row, reason: str, model_lookup: dict | None = None
):
    resource, record_id = extract_resource_and_record_id(
        row.calculation_record, model_lookup=model_lookup
    )
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
