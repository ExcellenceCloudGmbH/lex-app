"""
Shared models for Cluster 14 — AG Grid Query Endpoint.

``QueryItem`` carries one field per "interesting" filter type so a
single model covers the full AG Grid filter contract:

    * ``name``              CharField            → text filter
    * ``amount``            DecimalField         → number filter + Decimal coercion
    * ``count``             IntegerField         → number filter + int coercion
    * ``is_active``         BooleanField         → bool coercion
    * ``status``            CharField(choices)   → set filter + ``__in``
    * ``created_on``        DateField            → date filter + ``_parse_ag_date``
    * ``created_at_ts``     DateTimeField        → date-with-time filter
                                                    + ``_parse_ag_datetime``
                                                    + ``_ag_filter_has_time``
    * ``metadata``          JSONField            → JSON lookup path
    * ``category``          ForeignKey           → group / pivot / sort through
                                                    a relational field

Rule #3: no cross-cluster imports — stood up fresh here.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.LexModel import LexModel, PermissionResult

QUERY_STATUS_ACTIVE = "active"
QUERY_STATUS_ARCHIVED = "archived"
QUERY_STATUS_DRAFT = "draft"
QUERY_STATUS_CHOICES = [
    (QUERY_STATUS_ACTIVE, "Active"),
    (QUERY_STATUS_ARCHIVED, "Archived"),
    (QUERY_STATUS_DRAFT, "Draft"),
]


class QueryCategory(LexModel):
    """FK target used by grouping / pivot / sort scenarios."""

    name = models.CharField(max_length=64)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return f"QCat<{self.name}>"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 14: category read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 14: category edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class QueryItem(LexModel):
    """Wide row — one field per filterable type."""

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16, choices=QUERY_STATUS_CHOICES, default=QUERY_STATUS_ACTIVE,
    )
    created_on = models.DateField(null=True, blank=True)
    created_at_ts = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    category = models.ForeignKey(
        QueryCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 14: item read-open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 14: item edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [QueryCategory, QueryItem]

# URL names expected by ``process_admin_rest_api`` — lowercased model name.
CATEGORY = "querycategory"
ITEM = "queryitem"

