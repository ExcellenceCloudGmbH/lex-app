from rest_framework.pagination import PageNumberPagination, LimitOffsetPagination


class CustomPageNumberPagination(PageNumberPagination):
    page_size_query_param = 'size'


class CustomLimitOffsetPagination(LimitOffsetPagination):
    limit_query_param = 'limit'
    offset_query_param = 'offset'
