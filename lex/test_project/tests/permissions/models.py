"""
Shared models for Cluster 4 — Permissions.

Each model exercises one slice of the permission contract:

* :class:`ProtectedItem` — action-level overrides (``permission_create`` /
  ``permission_delete`` / ``permission_edit``). DELETE/POST must be
  rejected for non-admins; PATCH to the ``secret`` field must be ignored
  or rejected.
* :class:`FieldLevelItem` — field-level overrides using
  ``PermissionResult.allow_fields`` and ``allow_all_except``. API
  responses must only include fields the customer is actually allowed
  to read.
* :class:`KeycloakItem` — **no** overrides; the default implementation
  falls back to the Keycloak scope set on the :class:`UserContext`.

Rule #3: no cross-cluster imports — these models are defined once here
and imported by the sub-cluster test modules.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.LexModel import LexModel, PermissionResult


class ProtectedItem(LexModel):
    """Action-level permission overrides. Admins may create/delete; others may not."""

    name = models.CharField(max_length=200)
    secret = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    # Non-admins may still read everything, so permission tests can
    # observe the record after a restricted write attempt.
    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 4: protected-item read-open")

    def permission_edit(self, uc):
        # Admins may edit everything. Non-admins may edit ``name`` only.
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin may edit all fields")
        return PermissionResult.allow_fields(
            {"name"}, "non-admins may only edit name (not secret)",
        )

    def permission_create(self, uc):
        return uc.is_superuser or "admin" in uc.groups

    def permission_delete(self, uc):
        return uc.is_superuser or "admin" in uc.groups

    def permission_list(self, uc):
        return True


class FieldLevelItem(LexModel):
    """Field-level permission overrides using allow_fields / allow_all_except."""

    public_name = models.CharField(max_length=200)
    sensitive_salary = models.IntegerField(default=0)
    pii_ssn = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.public_name

    def permission_read(self, uc):
        if uc.is_superuser:
            return PermissionResult.allow_all("superuser sees everything")
        if "hr" in uc.groups:
            # HR sees salary but not SSN.
            return PermissionResult.allow_all_except(
                {"pii_ssn"}, "hr sees everything except ssn",
            )
        # Regular users see only the public name.
        return PermissionResult.allow_fields(
            {"id", "public_name"}, "regular user — public fields only",
        )

    def permission_edit(self, uc):
        if uc.is_superuser:
            return PermissionResult.allow_all("superuser may edit all")
        return PermissionResult.allow_fields(
            {"public_name"}, "regular users may only edit public_name",
        )

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return uc.is_superuser

    def permission_list(self, uc):
        return True

    # Legacy API — still used by some code paths (scenario 4.9).
    def can_read(self, request):
        from lex.core.models.LexModel import UserContext
        uc = UserContext.from_request(request, self)
        return self.permission_read(uc).get_fields(
            {f.name for f in self._meta.get_fields() if hasattr(f, "name")}
        )


class KeycloakItem(LexModel):
    """No permission overrides — falls back to Keycloak scopes on ``UserContext``."""

    label = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.label


ALL_MODELS = [ProtectedItem, FieldLevelItem, KeycloakItem]

# URL names expected by process_admin_rest_api — lowercased model name.
PROTECTED = "protecteditem"
FIELD_LEVEL = "fieldlevelitem"
KEYCLOAK = "keycloakitem"

