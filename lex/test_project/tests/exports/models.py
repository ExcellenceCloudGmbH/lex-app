"""
Shared models for Cluster 13 — Export Endpoint.

Three models, each carrying exactly one slice of the export
contract:

* :class:`ExportCategory` — FK target with a distinctive
  ``__str__`` so FK-display-name assertions in the exported
  spreadsheet are unambiguous (``Cat<...>`` rather than a default
  integer-looking repr).
* :class:`ExportItem` — main row under test. Default
  ``permission_export`` so :meth:`ModelExportView._has_default_
  export_permissions` returns ``True`` and the fast / uniform-mask
  paths activate.
* :class:`ExportMaskedItem` — same shape, but
  ``permission_export`` returns ``allow_fields({"id", "name"})``
  for non-admins. Pins the field-level export-mask contract.

Rule #3: no cross-cluster imports — stood up fresh here.
"""

from __future__ import annotations

from django.db import models
from lex.core.models.LexModel import LexModel, PermissionResult

EXPORT_STATUS_ACTIVE = "active"
EXPORT_STATUS_ARCHIVED = "archived"
EXPORT_STATUS_CHOICES = [
    (EXPORT_STATUS_ACTIVE, "Active"),
    (EXPORT_STATUS_ARCHIVED, "Archived"),
]


class ExportCategory(LexModel):
    """FK target; distinctive ``__str__`` for display-name assertions."""

    name = models.CharField(max_length=64)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return f"Cat<{self.name}>"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 13: category read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 13: category edit-open")

    def permission_export(self, uc):
        # Opt in to the default-export fast path — the framework's
        # default ``permission_export`` denies without a Keycloak
        # ``export`` scope, which our session-authenticated test user
        # does not carry. Customers configure this explicitly.
        return PermissionResult.allow_all("cluster 13: category export-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class ExportItem(LexModel):
    """Main export subject — default ``permission_export`` (uniform mask)."""

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=16, choices=EXPORT_STATUS_CHOICES, default=EXPORT_STATUS_ACTIVE,
    )
    category = models.ForeignKey(
        ExportCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 13: item read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 13: item edit-open")

    def permission_export(self, uc):
        # Opt in to the default-export fast path — see ExportCategory.
        return PermissionResult.allow_all("cluster 13: item export-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class ExportMaskedItem(LexModel):
    """Field-level masked export — ``permission_export`` allows ``{id, name}`` only."""

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=16, choices=EXPORT_STATUS_CHOICES, default=EXPORT_STATUS_ACTIVE,
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 13: masked read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 13: masked edit-open")

    def permission_export(self, uc):
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin exports everything")
        return PermissionResult.allow_fields(
            {"id", "name"},
            "non-admins may only export id + name (amount / status masked)",
        )

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class FastExportItem(LexModel):
    """Default-permissions export subject — triggers streaming fast paths.

    **Why this model exists separately from :class:`ExportItem`:**
    ``ModelExportView._has_default_export_permissions`` identity-
    compares ``permission_export`` / ``can_export`` against the
    :class:`~lex.core.models.LexModel.LexModel` defaults. The moment a
    subclass *overrides* ``permission_export`` (as ``ExportItem`` does —
    even to return ``allow_all``) that check returns ``False`` and the
    three streaming fast paths
    (``_try_stream_universal_fast_export``,
    ``_try_stream_flat_fast_export``,
    ``_try_build_flat_fast_export_dataframe``) all bail out.

    ``FastExportItem`` deliberately does **not** override
    ``permission_export`` so the identity check passes and the
    streaming paths run. Cluster-13e tests grant the
    ``export`` Keycloak scope to the test user at runtime (see
    ``test_13e_streaming_fast_export.py``) so the default
    ``LexModel.permission_export`` returns ``allow_all``.

    Field palette is deliberately varied (bool, int, Decimal, date,
    datetime, FK, JSON) so
    :meth:`ModelExportView._normalize_cell_value` is exercised across
    every type branch.
    """

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    count = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    happened_on = models.DateField(null=True, blank=True)
    happened_at = models.DateTimeField(null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)
    category = models.ForeignKey(
        ExportCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="fast_items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return f"Fast<{self.name}>"

    @property
    def display_label(self) -> str:
        """Computed column — exercises ``_make_attr_resolver``."""
        return f"{self.name}::{self.amount}"

    def compute_amount_times_count(self):
        """Zero-arg callable — exercises the callable branch in
        ``_make_attr_resolver``."""
        return float(self.amount) * int(self.count)

    def permission_read(self, uc):
        return PermissionResult.allow_all("fast: read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("fast: edit-open")

    # NOTE: ``permission_export`` is intentionally NOT overridden —
    # see the class docstring.

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [ExportCategory, ExportItem, ExportMaskedItem, FastExportItem]

# URL names expected by ``process_admin_rest_api`` — lowercased model name.
CATEGORY = "exportcategory"
ITEM = "exportitem"
MASKED = "exportmaskeditem"
FAST = "fastexportitem"


