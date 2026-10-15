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
    * ``note``              CharField(null=True) → blank-folding ("" and NULL
                                                    are the same bucket)
    * ``ratio``              FloatField           → refused by filter_values
                                                    (continuous, no value list)
    * ``api_key``           CharField             → credential-shaped name,
                                                    refused by filter_values
                                                    whatever the serializer

``QueryMaskedItem`` overrides ``can_read`` to hide ``category`` per row
(cluster 14g's per-row field-masking fixture: 14.53, 14.56).

``QueryOpenItem`` overrides neither ``permission_read`` nor ``can_read`` at
all -- cluster 14g's "default permissions" fixture (14.55, 14.59, 14.60):
the read filter and the serializer both fall through to allow-everything, so
a database count is exact without any row-by-row check.

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
    note = models.CharField(max_length=200, null=True, blank=True, default="")
    ratio = models.FloatField(default=0.0)
    api_key = models.CharField(max_length=64, blank=True, default="")

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


class QueryMaskedItem(LexModel):
    """``category`` is hidden via ``can_read`` whenever ``visible`` is False.

    Cluster 14g's per-row field-masking fixture: ``permission_read`` is kept
    permissive (every row stays visible to the grid) while ``can_read`` --
    which ``to_representation`` always tries first -- excludes ``category``
    on masked rows. This is what ``filter_values`` must mirror: masked rows
    must not contribute to ``category``'s counts, and a foreign key only a
    masked row refers to must not get that row's name as its label.
    """

    name = models.CharField(max_length=200)
    visible = models.BooleanField(default=True)
    category = models.ForeignKey(
        QueryCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="masked_items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 14g: masked item read-open")

    def can_read(self, request):
        if self.visible:
            return True
        return {"id", "name", "visible"}  # category excluded on masked rows

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 14g: masked item edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class QueryOpenItem(models.Model):
    """A plain Django model -- no ``LexModel``, so no ``permission_read`` /
    ``can_read`` at all, inherited or otherwise.

    Cluster 14g's "declares neither" fixture (14.55, 14.59, 14.60). This has
    to NOT be a ``LexModel`` subclass: a LexModel that simply skips
    overriding ``permission_read`` still has it, inherited and unchanged --
    which is the OTHER case ``uses_default_read_permission`` covers (the
    literal-identity one), and which the row filter's
    ``_get_default_permission_target`` fast path reads Keycloak UMA
    ``request.user_permissions`` for, excluding every row for a plain test
    user who carries none. Only a model with no such method at all --
    ``_get_capabilities`` reporting both flags False -- reaches
    ``_handle_lexmodel_default``'s generic "allow by default" fallthrough
    and ``LexSerializer.readable_field_names``'s "all fields" fallthrough:
    so a database aggregate already answers exactly what the grid shows,
    with no per-row scan and no permission-check warnings.
    """

    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=QUERY_STATUS_CHOICES, default=QUERY_STATUS_ACTIVE,
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name


class QueryDefaultPermissionItem(LexModel):
    """A real LexModel that overrides NEITHER ``permission_read`` NOR
    ``can_read`` -- both stay the literal inherited default.

    Cluster 14g's "default permissions, through the real endpoint" fixture
    (14.60), distinct from ``QueryOpenItem``: that one has no such methods
    AT ALL (a plain Django model), which is ``uses_default_read_permission``'s
    OTHER True branch. This one exercises the identity branch -- the row
    filter's ``_get_default_permission_target`` fast path, keyed on Keycloak
    UMA ``request.user_permissions`` -- which needs a real-looking global
    read grant to show any rows at all for a test user who otherwise carries
    none; see ``_grant_global_read`` in the test file.
    """

    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=QUERY_STATUS_CHOICES, default=QUERY_STATUS_ACTIVE,
    )
    category = models.ForeignKey(
        QueryCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="default_perm_items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name


class QueryScopedFieldItem(LexModel):
    """``permission_read`` always allows the ROW but excludes
    ``secret_field`` unless the caller's Keycloak scopes include ``"read"``;
    ``can_read`` is left at the LexModel default (unoverridden).

    Cluster 14g's API-key fast-path parity fixture (14.66). ``permission_read``
    being an override (not the literal default) keeps the row filter off its
    restrictive UMA fast path regardless of auth method -- ``allow_fields``
    always sets ``allowed=True``, so the row is never excluded either way --
    which isolates the thing actually under test: whether
    ``readable_field_names``'s per-row fast path sees an API-key caller's
    OWN scopes (``DEFAULT_API_KEY_SCOPES`` includes ``"read"``) the same way
    ``can_read(request)`` -- the grid's own path -- would.
    """

    name = models.CharField(max_length=200)
    secret_field = models.CharField(max_length=64, blank=True, default="classified")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name

    def permission_read(self, uc):
        if "read" in uc.keycloak_scopes:
            return PermissionResult.allow_all("cluster 14g: read scope present")
        return PermissionResult.allow_fields({"id", "name"}, "cluster 14g: read scope absent")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 14g: scoped item edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [
    QueryCategory, QueryItem, QueryMaskedItem, QueryOpenItem,
    QueryDefaultPermissionItem, QueryScopedFieldItem,
]

# URL names expected by ``process_admin_rest_api`` — lowercased model name.
CATEGORY = "querycategory"
ITEM = "queryitem"
MASKED_ITEM = "querymaskeditem"
OPEN_ITEM = "queryopenitem"
DEFAULT_PERM_ITEM = "querydefaultpermissionitem"
SCOPED_FIELD_ITEM = "queryscopedfielditem"

