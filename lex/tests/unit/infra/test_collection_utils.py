"""
Unit tests for ``lex.api.utils.collection_utils.flatten``.

**What this tests**

``flatten`` is a utility that reduces a list of lists into a single flat list.
It is used across the codebase for flattening nested query results.

Run::

    python manage.py test lex.tests.test_collection_utils
"""

from django.test import SimpleTestCase

from lex.api.utils.collection_utils import flatten


class TestFlatten(SimpleTestCase):
    """Prove ``flatten`` reduces nested lists correctly."""

    def test_basic_flatten(self):
        self.assertEqual(flatten([[1, 2], [3, 4]]), [1, 2, 3, 4])

    def test_single_list(self):
        self.assertEqual(flatten([[1, 2, 3]]), [1, 2, 3])

    def test_empty_outer(self):
        self.assertEqual(flatten([]), [])

    def test_empty_inner(self):
        self.assertEqual(flatten([[], [], []]), [])

    def test_mixed_empty_and_full(self):
        self.assertEqual(flatten([[], [1], [], [2, 3]]), [1, 2, 3])

    def test_preserves_order(self):
        result = flatten([["c", "b"], ["a"]])
        self.assertEqual(result, ["c", "b", "a"])

    def test_nested_types(self):
        result = flatten([[{"a": 1}], [{"b": 2}]])
        self.assertEqual(result, [{"a": 1}, {"b": 2}])
