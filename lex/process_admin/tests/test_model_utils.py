"""
Unit tests for ``lex.process_admin.models.utils`` — ``get_node_styling``.

**What this tests (customer-visible behaviour)**

``get_node_styling`` resolves the visual styling (icon, color, label
override) for a node in the model hierarchy sidebar.  It checks
parent-level overrides first, then falls back to global model_styling.

**Why it matters**

If the wrong styling dict is returned, nodes display with wrong
icons/colors or missing label overrides in the sidebar tree.

**Methodology**

Pure function with mock model_collection — no DB.

Run::

    python manage.py test lex.process_admin.tests.test_model_utils
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from lex.process_admin.models.utils import get_node_styling


class TestGetNodeStyling(SimpleTestCase):
    """Prove ``get_node_styling`` resolves styling in priority order."""

    def _make_collection(self, global_styling=None):
        collection = MagicMock()
        collection.model_styling = global_styling or {}
        return collection

    def test_parent_styling_takes_priority(self):
        parent_styling = {"fund": {"icon": "custom_icon"}}
        collection = self._make_collection({"fund": {"icon": "global_icon"}})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {"icon": "custom_icon"})

    def test_falls_back_to_global_styling(self):
        parent_styling = {}
        collection = self._make_collection({"fund": {"icon": "global_icon"}})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {"icon": "global_icon"})

    def test_returns_empty_when_no_styling(self):
        parent_styling = {}
        collection = self._make_collection({})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {})

    def test_non_dict_parent_styling_falls_through(self):
        """If parent_styling value is not a dict, use global instead."""
        parent_styling = {"fund": "not_a_dict"}
        collection = self._make_collection({"fund": {"icon": "fallback"}})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {"icon": "fallback"})

    def test_non_dict_global_styling_returns_empty(self):
        parent_styling = {}
        collection = self._make_collection({"fund": "not_a_dict"})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {})

    def test_unknown_node_returns_empty(self):
        parent_styling = {"other": {"icon": "x"}}
        collection = self._make_collection({"other": {"icon": "y"}})
        result = get_node_styling("fund", collection, parent_styling)
        self.assertEqual(result, {})
