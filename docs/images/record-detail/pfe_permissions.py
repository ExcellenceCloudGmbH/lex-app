"""
PFE Environment-Aware Permissions
=================================

Four abstract base-model mixins that override the ``permission_*`` methods
from ``LexModel`` to enforce PFE's production / non-production access rules.

Roles
-----
- **Customer roles:** ``standard``, ``acp-hr``
- **Lund roles:**      ``admin``, ``support``

Environment
-----------
``is_prod()`` returns ``True`` when the environment variable ``INSTANCE_TYPE``
is **not** one of: unset (empty), ``"prod"`` or ``"prod-like"``.

When ``is_prod()`` is ``True``, Lund users are restricted.
When ``is_prod()`` is ``False``, Lund users receive the same access as
customers, so cloned production instances remain fully usable.

Usage
-----
Have your model inherit from the appropriate mixin **before**
``CalculationModel`` or ``LexModel``::

    from pfe_permissions import PeriodLevelPermissions
    from lex.core.models.CalculationModel import CalculationModel

    class SomeModel(PeriodLevelPermissions, CalculationModel):
        ...
"""

import os
from typing import Set

from lex.core.models.LexModel import UserContext, PermissionResult

# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------
LUND_ROLES: Set[str] = {"admin", "support"}
CUSTOMER_STANDARD_ROLES: Set[str] = {"standard"}
CUSTOMER_HR_ROLES: Set[str] = {"acp-hr"}


# ---------------------------------------------------------------------------
# Environment helper
# ---------------------------------------------------------------------------
def is_prod() -> bool:
    """
    Determine whether the current environment is production.

    ``IS_PROD`` is ``True`` when ``INSTANCE_TYPE`` is **not** one of:
    unset (empty string), ``"prod"``, or ``"prod-like"``.

    This means cloned instances marked ``"prod"`` / ``"prod-like"`` are treated
    as non-production and receive relaxed permissions.
    """
    instance_type = os.getenv("INSTANCE_TYPE", "").strip().lower()
    return instance_type not in ("", "prod", "prod-like")


# ---------------------------------------------------------------------------
# Role helper
# ---------------------------------------------------------------------------
def _has_any_role(user_context: UserContext, allowed_roles: Set[str]) -> bool:
    """Check if the user holds at least one of *allowed_roles*."""
    return bool(set(user_context.client_roles) & allowed_roles)


def _write_roles(base_roles: Set[str]) -> Set[str]:
    """
    Return the set of roles that are allowed to write.

    In production only *base_roles* can write.
    In non-production, Lund roles are added on top.
    """
    roles = base_roles.copy()
    if not is_prod():
        roles |= LUND_ROLES
    return roles


# =============================================================================
# Calculation-field exclusion helper (for blocking the Calculate button)
# =============================================================================
def exclude_calculation_for_lund(
    user_context: UserContext,
    base_result: PermissionResult,
) -> PermissionResult:
    """
    Layer ``is_calculated`` exclusion ON TOP of an existing PermissionResult.

    If the user holds a Lund role **and** we are in production, the
    ``is_calculated`` field is removed from whatever the base permission
    already grants.  All other restrictions from the base result are preserved.

    This prevents Lund users from writing ``IN_PROGRESS`` to the field,
    which in turn prevents ``CalculationModel.calculate_hook`` from firing.
    """
    if not is_prod() or not _has_any_role(user_context, LUND_ROLES):
        return base_result  # nothing to change

    if not base_result.allowed:
        return base_result

    reason = f"{base_result.reason or 'Base logic'}; is_calculated excluded for Lund in production"

    # Case 1: specific fields allowed → remove is_calculated from the set
    if base_result.fields is not None:
        return PermissionResult(
            allowed=True,
            fields=base_result.fields - {"is_calculated"},
            excluded_fields=None,
            reason=reason,
        )

    # Case 2: excluded_fields set → add is_calculated to the exclusions
    if base_result.excluded_fields is not None:
        return PermissionResult(
            allowed=True,
            fields=None,
            excluded_fields=base_result.excluded_fields | {"is_calculated"},
            reason=reason,
        )

    # Case 3: allow_all (fields=None, excluded_fields=None) → become allow_all_except
    return PermissionResult(
        allowed=True,
        fields=None,
        excluded_fields={"is_calculated"},
        reason=reason,
    )


# =============================================================================
# Mixin 1 — PeriodLevelPermissions
#
# ┌──────────┬───────────┬──────────────────────────┬──────────────────────────┬──────────────────────────┐
# │ IS_PROD  │ Read      │ Modify                   │ Create                   │ Delete                   │
# ├──────────┼───────────┼──────────────────────────┼──────────────────────────┼──────────────────────────┤
# │ TRUE     │ All users │ Standard                 │ Standard                 │ Standard                 │
# │ FALSE    │ All users │ Standard, Admin, Support  │ Standard, Admin, Support │ Standard, Admin, Support │
# └──────────┴───────────┴──────────────────────────┴──────────────────────────┴──────────────────────────┘
# =============================================================================


class PeriodLevelPermissions:
    """Mixin for period-level models."""

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        return PermissionResult.allow_all("PeriodLevel: all users can read")

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        allowed = _write_roles(CUSTOMER_STANDARD_ROLES)
        if _has_any_role(user_context, allowed):
            return PermissionResult.allow_all("PeriodLevel: write role")
        return PermissionResult.deny("PeriodLevel: no write role")

    def permission_create(self, user_context: UserContext) -> bool:
        return _has_any_role(user_context, _write_roles(CUSTOMER_STANDARD_ROLES))

    def permission_delete(self, user_context: UserContext) -> bool:
        return _has_any_role(user_context, _write_roles(CUSTOMER_STANDARD_ROLES))


# =============================================================================
# Mixin 2 — PlanLevelPermissions
#
# ┌──────────┬───────────┬──────────────────────────┬─────────┬─────────┐
# │ IS_PROD  │ Read      │ Modify                   │ Create  │ Delete  │
# ├──────────┼───────────┼──────────────────────────┼─────────┼─────────┤
# │ TRUE     │ All users │ Standard                 │ No user │ No user │
# │ FALSE    │ All users │ Standard, Admin, Support  │ No user │ No user │
# └──────────┴───────────┴──────────────────────────┴─────────┴─────────┘
# =============================================================================


class PlanLevelPermissions:
    """Mixin for plan-level models."""

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        return PermissionResult.allow_all("PlanLevel: all users can read")

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        allowed = _write_roles(CUSTOMER_STANDARD_ROLES)
        if _has_any_role(user_context, allowed):
            return PermissionResult.allow_all("PlanLevel: write role")
        return PermissionResult.deny("PlanLevel: no write role")

    def permission_create(self, user_context: UserContext) -> bool:
        return False

    def permission_delete(self, user_context: UserContext) -> bool:
        return False


# =============================================================================
# Mixin 3 — HRCalculationPermissions
#
# ┌──────────┬───────────┬───────────────────────────────────┬──────────────────────────────────┬──────────────────────────────────┐
# │ IS_PROD  │ Read      │ Modify                            │ Create                           │ Delete                           │
# ├──────────┼───────────┼───────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────┤
# │ TRUE     │ All users │ Standard, HR                      │ Standard, HR                     │ Standard, HR                     │
# │ FALSE    │ All users │ Standard, HR, Admin, Support      │ Standard, HR, Admin, Support     │ Standard, HR, Admin, Support     │
# └──────────┴───────────┴───────────────────────────────────┴──────────────────────────────────┴──────────────────────────────────┘
# =============================================================================


class HRCalculationPermissions:
    """Mixin for HR calculation models."""

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        return PermissionResult.allow_all("HRCalculation: all users can read")

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        allowed = _write_roles(CUSTOMER_STANDARD_ROLES | CUSTOMER_HR_ROLES)
        if _has_any_role(user_context, allowed):
            return PermissionResult.allow_all("HRCalculation: write role")
        return PermissionResult.deny("HRCalculation: no write role")

    def permission_create(self, user_context: UserContext) -> bool:
        return _has_any_role(user_context, _write_roles(CUSTOMER_STANDARD_ROLES | CUSTOMER_HR_ROLES))

    def permission_delete(self, user_context: UserContext) -> bool:
        return _has_any_role(user_context, _write_roles(CUSTOMER_STANDARD_ROLES | CUSTOMER_HR_ROLES))


# =============================================================================
# Mixin 4 — ViewOnlyPermissions
#
# ┌──────────┬───────────┬─────────┬─────────┬─────────┐
# │ IS_PROD  │ Read      │ Modify  │ Create  │ Delete  │
# ├──────────┼───────────┼─────────┼─────────┼─────────┤
# │ TRUE     │ All users │ No user │ No user │ No user │
# │ FALSE    │ All users │ No user │ No user │ No user │
# └──────────┴───────────┴─────────┴─────────┴─────────┘
# =============================================================================


class ViewOnlyPermissions:
    """Mixin for view-only models — no modifications for any user in any environment."""

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        return PermissionResult.allow_all("ViewOnly: all users can read")

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        return PermissionResult.deny("ViewOnly: modifications disabled")

    def permission_create(self, user_context: UserContext) -> bool:
        return False

    def permission_delete(self, user_context: UserContext) -> bool:
        return False
