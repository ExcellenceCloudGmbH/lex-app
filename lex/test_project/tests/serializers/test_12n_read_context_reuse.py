"""Reading a list builds the caller's permission context once (BUG-011).

Intent
------
The list filter remembers each row's ``permission_read`` answer for the
serializer. Where the serializer runs without it — a project view, a list
whose filter backends are switched off (as the stress cluster does) — it
decided every row through ``can_read(request)``, which built a fresh
``UserContext`` per row: one query for the user's groups per row, so a
500-row list ran 500 extra queries. ``LexModel.can_read`` is
``permission_read`` with a context built from the request, so the serializer
now builds that context from the one it already caches for the request: the
same answer per row, and the groups are read once. A model that overrides
``can_read`` is still asked through it.

Cluster 12n — scenario 12.63. Type: E.
Covers: lex/api/serializers/base_serializers.py (to_representation, legacy can_read branch).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12n_read_context_reuse.py -v
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import TAG, TagItem

pytestmark = pytest.mark.serializers


class TestCluster12n_ReadContextReuse(E2ETestCase):
    """Cluster 12n: the list serializer reuses the request's user context."""

    e2e_models = [TagItem]

    def _list(self):
        # Without the list filter, so the serializer decides every row itself.
        with CaptureQueriesContext(connection) as queries:
            response = self.list_get(TAG)
        self.assertEqual(response.status_code, 200, response.content[:300])
        rows = response.json()
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        group_queries = [q["sql"] for q in queries.captured_queries if '"auth_group"' in q["sql"]]
        return len(rows), len(group_queries)

    # -- 12.63 ---------------------------------------------------------
    def test_12_63_group_lookups_do_not_grow_with_rows(self) -> None:
        """
        Scenario 12.63: the user's groups are read the same number of times for
        3 rows as for 30.
        Given: TagItem (lex's own can_read and permission_read)
        When: the list is requested with 3 rows, then with 30, with the list
              filter switched off
        Then: every row is returned both times, and the number of auth_group
              queries is the same both times.
        """
        for n in range(3):
            TagItem.objects.create(label=f"tag-{n}")
        rows_small, groups_small = self._list()
        for n in range(3, 30):
            TagItem.objects.create(label=f"tag-{n}")
        rows_large, groups_large = self._list()
        self.assertEqual((rows_small, rows_large), (3, 30))
        self.assertEqual(groups_large, groups_small)
