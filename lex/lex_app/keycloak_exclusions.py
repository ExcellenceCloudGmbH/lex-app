"""
Lightweight module defining which models are excluded from Keycloak sync.

These models should not have resources in Keycloak and should always be
visible in the frontend regardless of Keycloak permissions.

This module is intentionally kept free of heavy imports so it can be
safely imported from views without pulling in the full init command.
"""

KEYCLOAK_SYNC_EXCLUDED_APPS = frozenset({"legacy_data"})

KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES = frozenset(
    {
        "audit_logging.AuditLog",
        "audit_logging.AuditLogStatus",
        "audit_logging.CalculationLog",
    }
)

KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES = ("historical", "metahistorical")


def is_keycloak_sync_excluded_model(app_label: str, model_name: str) -> bool:
    """Check if a model should be excluded from Keycloak sync."""
    if not model_name:
        return False

    if app_label in KEYCLOAK_SYNC_EXCLUDED_APPS:
        return True

    resource_name = f"{app_label}.{model_name}"
    if resource_name in KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES:
        return True

    return model_name.lower().startswith(KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES)


def is_keycloak_sync_excluded_resource_name(resource_name: str | None) -> bool:
    """Check if a resource name should be excluded from Keycloak sync."""
    if not resource_name:
        return False

    if "." not in resource_name:
        return resource_name.lower().startswith(KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES)

    app_label, model_name = resource_name.split(".", 1)
    return is_keycloak_sync_excluded_model(app_label, model_name)

