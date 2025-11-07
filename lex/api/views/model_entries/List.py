from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination

from lex.api.views.model_entries.filter_backends import (
    UserReadRestrictionFilterBackend,
)
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import (
    ModelEntryProviderMixin,
)


class CustomPageNumberPagination(PageNumberPagination):
    page_query_param = "page"
    page_size_query_param = "perPage"

    def paginate_queryset(self, queryset, request, view=None):
        if request.query_params["perPage"] == -1:
            self.page_size = (
                queryset.count()
            )  # Set the page size equal to the total number of objects in the queryset

        return super().paginate_queryset(queryset, request, view)


class ListModelEntries(ModelEntryProviderMixin, ListAPIView):
    pagination_class = CustomPageNumberPagination
    # see https://stackoverflow.com/a/40585846
    # We use the UserReadRestrictionFilterBackend for filtering out those instances that the user
    #   does not have access to
    filter_backends = [UserReadRestrictionFilterBackend]
    # permission_classes = [IsAuthenticated, KeycloakUMAPermission]
