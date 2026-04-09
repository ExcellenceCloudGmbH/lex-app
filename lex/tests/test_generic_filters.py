"""
Unit tests for ``lex.api.filters.GenericFilters.create_filter_queries_from_tree_paths``.

**What this tests (customer-visible behaviour)**

The ``activeFilterTree`` query parameter allows the frontend to send
nested foreign-key filter trees to the API.
``create_filter_queries_from_tree_paths`` recursively walks the tree
and builds a flat ``{fk__fk__in: [pks]}`` dictionary that Django's
ORM can apply directly to a queryset.

**Why it matters**

If the recursive traversal misses a level or builds wrong key paths,
the grid silently shows unfiltered (or empty) results — a data
correctness issue for the user.

**Methodology**

Pure recursive function — no DB, no queryset.

Run::

    python manage.py test lex.tests.test_generic_filters
"""

from django.test import SimpleTestCase

from lex.api.filters.GenericFilters import create_filter_queries_from_tree_paths


class TestCreateFilterQueriesFromTreePaths(SimpleTestCase):
    """Prove ``create_filter_queries_from_tree_paths`` builds correct ORM filters."""

    def test_leaf_node_with_entries(self):
        """A leaf node with 'entries' produces a single __in filter."""
        queries = {}
        tree = {"entries": [1, 2, 3]}
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {"in": [1, 2, 3]})

    def test_single_child(self):
        """One level of nesting produces 'child__in'."""
        queries = {}
        tree = {
            "children": {
                "period": {"entries": [10, 20]}
            }
        }
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {"period__in": [10, 20]})

    def test_nested_children(self):
        """Two levels of nesting produce 'parent__child__in'."""
        queries = {}
        tree = {
            "children": {
                "fund": {
                    "children": {
                        "period": {"entries": [1, 2]}
                    }
                }
            }
        }
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {"fund__period__in": [1, 2]})

    def test_multiple_siblings(self):
        """Sibling children produce separate filter keys."""
        queries = {}
        tree = {
            "children": {
                "fund": {"entries": [1]},
                "period": {"entries": [10, 20]},
            }
        }
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {
            "fund__in": [1],
            "period__in": [10, 20],
        })

    def test_existing_query_string_prefix(self):
        """Existing prefix is preserved in the generated key."""
        queries = {}
        tree = {"entries": [5]}
        create_filter_queries_from_tree_paths(queries, tree, "parent__")
        self.assertEqual(queries, {"parent__in": [5]})

    def test_deep_nesting_three_levels(self):
        """Three levels of nesting build the full path."""
        queries = {}
        tree = {
            "children": {
                "a": {
                    "children": {
                        "b": {
                            "children": {
                                "c": {"entries": [99]}
                            }
                        }
                    }
                }
            }
        }
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {"a__b__c__in": [99]})

    def test_empty_entries(self):
        """Empty entries list still creates the filter key."""
        queries = {}
        tree = {"entries": []}
        create_filter_queries_from_tree_paths(queries, tree, "")
        self.assertEqual(queries, {"in": []})

    def test_accumulates_into_existing_dict(self):
        """Results accumulate into the passed dictionary."""
        queries = {"existing__in": [0]}
        tree = {"entries": [1]}
        create_filter_queries_from_tree_paths(queries, tree, "new__")
        self.assertEqual(queries, {
            "existing__in": [0],
            "new__in": [1],
        })
