import json

from rest_framework import filters


# TODO: test this
class UserReadRestrictionFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        model_container = view.kwargs['model_container']
        return self._filter_queryset(request, queryset, model_container)

    def _filter_queryset(self, request, queryset, model_container):
        model = model_container.model_class
        if hasattr(model, 'modification_restriction'):
            pks = [obj.pk for obj in queryset if model.modification_restriction.can_be_read(instance=obj, user=request.user, violations=[])]
            queryset.filter(pk__in=pks)
        return queryset


def create_filter_queries_from_tree_paths(all_filter_queries, filter_node, query_string_so_far):
    if 'entries' in filter_node:
        all_filter_queries[query_string_so_far + 'in'] = filter_node['entries']
    else:
        for key, value in filter_node['children'].items():
            new_query_string = query_string_so_far + key + '__'
            create_filter_queries_from_tree_paths(all_filter_queries, value, new_query_string)


class ForeignKeyFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        active_filter_tree = request.GET.get('activeFilterTree', None)
        all_filter_queries = {}
        if active_filter_tree is not None:
            active_filter_tree = json.loads(active_filter_tree)
            create_filter_queries_from_tree_paths(all_filter_queries, active_filter_tree, '')
        return queryset.filter(**all_filter_queries)


class PrimaryKeyListFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        model_container = view.kwargs['model_container']
        filter_arguments = {
            model_container.pk_name + '__in':
                list(filter(lambda x: x != '', request.query_params.dict()['pks'].split(',')))
        } if 'pks' in request.query_params.dict() else {}
        return queryset.filter(**filter_arguments)


class StringFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        filter_arguments = request.GET.get('searchParams', '{}')
        return queryset.filter(**json.loads(filter_arguments))