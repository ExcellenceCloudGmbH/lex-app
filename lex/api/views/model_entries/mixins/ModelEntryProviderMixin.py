from django.contrib.auth.models import User
from lex.api.utils.temporal import parse_as_of_datetime
from lex.api.views.permissions.UserPermission import UserPermission
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey


# LEX-702. What the UI legitimately needs to DISPLAY a user: a name to put
# on an audit row, a history entry, a created_by / edited_by column or an FK
# hover card. Nothing here is a credential and nothing here is a privilege
# flag.
USER_DISPLAY_FIELDS = ("id", "username", "first_name", "last_name", "email")


class UserModelSerializer(serializers.ModelSerializer):
    """Read-only display projection of the user table.

    An ALLOWLIST, not `__all__` minus a few names, and that distinction is
    the fix. LEX-702: `fields = "__all__"` on `auth.User` published the
    Django password hash, and this serializer is reached by
    `issubclass(model_class, User)` -- so a project's own user model lands
    here too and `__all__` would publish whatever columns it adds. An
    allowlist makes a new column invisible until someone chooses to expose
    it; a denylist makes it public until someone remembers to hide it.

    Note also what this class is NOT: a `LexSerializer`. It therefore never
    runs the can_read / permission_read visibility filter, which is why the
    permission system was working correctly and the hash still went out. The
    field list is the only thing standing between this table and the wire.

    `is_staff` / `is_superuser` are excluded on purpose. They are not
    secrets, but publishing them next to a user list tells an attacker which
    account is worth attacking first, and no frontend surface reads them.
    """

    id_field = serializers.ReadOnlyField(default=User._meta.pk.name)
    short_description = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id_field", "short_description") + USER_DISPLAY_FIELDS

    def get_short_description(self, obj):
        return f"{obj.first_name} {obj.last_name} - {obj.email}"


def resolve_serializer_class(model_class, mapping, choice):
    """The serializer class ``choice`` selects for ``model_class``, from ``mapping``.

    The one place the ``User`` swap (LEX-702) is decided: ``model_class`` and its
    subclasses always render through :class:`UserModelSerializer`, regardless of
    ``choice`` or what ``mapping`` holds. Shared by
    :meth:`ModelEntryProviderMixin.get_serializer_class` (list / detail /
    filter_values) and the fields endpoint's ``value_list`` flag, so neither can
    compute it against a different serializer than the other and disagree about
    which columns actually offer a value list.

    ``None`` when ``choice`` is not a key of ``mapping`` and ``model_class`` is
    not a ``User`` subclass -- callers raise their own flavour of "unknown
    serializer" error in that case. ``model_class`` is checked with
    ``isinstance(..., type)`` before ``issubclass`` so a test double that
    stands in for a model (e.g. a bare ``SimpleNamespace`` mimicking
    ``model._meta``, never a real class) falls through to the mapping lookup
    instead of raising ``TypeError`` -- every real model class is a ``type``.
    """
    if isinstance(model_class, type) and issubclass(model_class, User):
        return UserModelSerializer
    return mapping.get(choice)


class ModelEntryProviderMixin:
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def get_queryset(self):
        from lex.core.services.Bitemporal import get_queryset_as_of

        model_class = self.kwargs["model_container"].model_class
        queryset = model_class.objects.all()
        
        as_of_param = self.request.query_params.get("as_of")
        if as_of_param:
            as_of_date = parse_as_of_datetime(as_of_param)
            if as_of_date:
                # Use the bitemporal helper to get the historical snapshot
                queryset = get_queryset_as_of(model_class, as_of_date)
        else:
             pass
            # "Current Time" Request - Opportunity for Read Repair / Reconciliation
            
            # 1. Detail View: Check if we are requesting a specific ID
            # lookup_url_kwarg = getattr(self, 'lookup_url_kwarg', None) or getattr(self, 'lookup_field', 'pk')
            # pk = self.kwargs.get(lookup_url_kwarg)
            
            # if pk:
            #     # Sync SPECIFIC ID (Read-Repair)
            #     # Ensure main table is up to date for this record
            #     BitemporalSynchronizer.sync_record_for_model(model_class, pk)
            # else:
            #     # 2. List View: "Reconcile changes upon get request"
            #     # Doing full table scan is expensive. 
            #     # Strategy: Reconcile records that became valid in the last X minutes?
            #     # This covers the "I just waited for it to become valid" test case.
            #     # Let's say last 1 hour for safety in this "test mode".
            #     now = timezone.now()
            #     start_window = now - timezone.timedelta(hours=1)
            #     TemporalReconciler.reconcile_model_window(model_class, start_window, now)
        
        # Auto select_related for FK fields to prevent N+1 queries during serialization
        from django.db.models import ForeignKey
        fk_fields = [
            f.name for f in model_class._meta.fields
            if isinstance(f, ForeignKey)
        ]
        if fk_fields:
            queryset = queryset.select_related(*fk_fields)

        return queryset

    def get_serializer_class(self):
        """
        Chooses serializer based on `?serializer=<name>`, defaulting to 'default'.
        """
        container = self.kwargs["model_container"]
        choice = self.request.query_params.get("serializer", "default")
        mapping = (
            container.get_serializers_map()
            if hasattr(container, "get_serializers_map")
            else container.serializers_map
        )

        serializer_class = resolve_serializer_class(container.model_class, mapping, choice)
        if serializer_class is None:
            raise ValidationError(
                {
                    "error": f"Unknown serializer '{choice}' for model '{container.model_class._meta.model_name}'",
                    "available": list(mapping.keys()),
                }
            )

        return serializer_class
