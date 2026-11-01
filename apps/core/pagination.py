"""Pagination that emits the standard envelope with a ``meta`` block (doc 04 §1.1)."""

from rest_framework.pagination import PageNumberPagination

from .responses import ok


class ApiPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 200

    def get_paginated_response(self, data):
        return ok(
            data,
            meta={
                'page': self.page.number,
                'limit': self.get_page_size(self.request),
                'total': self.page.paginator.count,
                'total_pages': self.page.paginator.num_pages,
            },
        )


def paginated(queryset, request, serialize):
    """Paginate ``queryset`` and render it through ``serialize`` in one call.

    ``serialize`` receives the page's object list and returns JSONable data —
    usually ``lambda rows: SomeSerializer(rows, many=True).data``.
    """
    paginator = ApiPagination()
    page = paginator.paginate_queryset(queryset, request)
    return paginator.get_paginated_response(serialize(page if page is not None else []))
