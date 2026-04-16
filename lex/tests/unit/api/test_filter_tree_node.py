"""
Tests for ``lex.api.filters.FilterTreeNode`` — tree evaluation and serialization.

**What is tested:**

    * ``FilterTreeNode.evaluate()`` — leaf returns ``objects.all()``, parent
      intersects child selections with parent queryset via ``__in`` lookups
    * ``FilterTreeNode.write_self_to_dict()`` — serializes tree of filtered
      objects into ``{node_id: [pk, ...]}`` dict

**Why this matters:**

    The filter tree is the mechanism the frontend uses to filter related models
    in the AG Grid views.  If ``evaluate()`` drops a child's selection, the
    grid shows unfiltered data.  If ``write_self_to_dict()`` loses a child
    node, the frontend tree and backend queryset diverge.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_filter_tree_node --verbosity=2 --noinput
"""

from unittest.mock import MagicMock, call

from django.test import SimpleTestCase

from lex.api.filters.FilterTreeNode import FilterTreeNode


# ─── helpers ──────────────────────────────────────────────────────────

def _mock_container(pk_name="pk"):
    """Minimal model_container stub."""
    mc = MagicMock()
    mc.pk_name = pk_name
    return mc


def _make_leaf(node_id, model_container, selection="noSelection"):
    """Create a leaf FilterTreeNode (no children)."""
    return FilterTreeNode(
        node_id=node_id,
        model_container=model_container,
        parent_to_self_fk_name="",
        selection=selection,
        children=[],
    )


# ════════════════════════════════════════════════════════════════════════
#  evaluate()
# ════════════════════════════════════════════════════════════════════════

class TestFilterTreeNodeEvaluateLeaf(SimpleTestCase):
    """Leaf nodes (no children) evaluate to objects.all()."""

    def test_leaf_sets_filtered_objects_to_all(self):
        mc = _mock_container()
        mock_qs = MagicMock(name="all_qs")
        mc.model_class.objects.all.return_value = mock_qs

        node = _make_leaf("leaf-1", mc)
        node.evaluate()

        mc.model_class.objects.all.assert_called_once()
        self.assertEqual(node.filtered_objects, mock_qs)


class TestFilterTreeNodeEvaluateParent(SimpleTestCase):
    """Parent nodes evaluate by intersecting child selections."""

    def test_parent_with_no_selection_child(self):
        """Child with 'noSelection' → child's full queryset used as filter."""
        parent_mc = _mock_container()
        child_mc = _mock_container()

        child_all_qs = MagicMock(name="child_all")
        child_mc.model_class.objects.all.return_value = child_all_qs

        child = FilterTreeNode(
            node_id="child-1",
            model_container=child_mc,
            parent_to_self_fk_name="child_fk",
            selection="noSelection",
            children=[],
        )

        parent_filtered_qs = MagicMock(name="parent_filtered")
        parent_mc.model_class.objects.filter.return_value = parent_filtered_qs

        parent = FilterTreeNode(
            node_id="parent-1",
            model_container=parent_mc,
            parent_to_self_fk_name="",
            selection="noSelection",
            children=[child],
        )

        parent.evaluate()

        # Parent filters using child_fk__in: child's full queryset
        parent_mc.model_class.objects.filter.assert_called_once()
        filter_kwargs = parent_mc.model_class.objects.filter.call_args[1]
        self.assertIn("child_fk__in", filter_kwargs)
        self.assertEqual(filter_kwargs["child_fk__in"], child_all_qs)

    def test_parent_with_selected_child(self):
        """Child with explicit selection → child queryset is .filter(pk__in=selection)."""
        parent_mc = _mock_container(pk_name="id")
        child_mc = _mock_container(pk_name="id")

        child_all_qs = MagicMock(name="child_all")
        child_filtered_qs = MagicMock(name="child_filtered")
        child_all_qs.filter.return_value = child_filtered_qs
        child_mc.model_class.objects.all.return_value = child_all_qs

        child = FilterTreeNode(
            node_id="child-1",
            model_container=child_mc,
            parent_to_self_fk_name="child_fk",
            selection=[10, 20],
            children=[],
        )

        parent_mc.model_class.objects.filter.return_value = MagicMock()

        parent = FilterTreeNode(
            node_id="parent-1",
            model_container=parent_mc,
            parent_to_self_fk_name="",
            selection="noSelection",
            children=[child],
        )

        parent.evaluate()

        # Child's queryset was filtered by the parent's pk_name
        child_all_qs.filter.assert_called_once_with(id__in=[10, 20])

        # Parent uses the filtered child queryset
        filter_kwargs = parent_mc.model_class.objects.filter.call_args[1]
        self.assertEqual(filter_kwargs["child_fk__in"], child_filtered_qs)

    def test_multiple_children(self):
        """Parent with two children → filter has both fk__in entries."""
        parent_mc = _mock_container()

        child_mc_a = _mock_container()
        child_mc_a.model_class.objects.all.return_value = MagicMock(name="a_qs")
        child_a = FilterTreeNode("a", child_mc_a, "fk_a", "noSelection", [])

        child_mc_b = _mock_container()
        child_mc_b.model_class.objects.all.return_value = MagicMock(name="b_qs")
        child_b = FilterTreeNode("b", child_mc_b, "fk_b", "noSelection", [])

        parent_mc.model_class.objects.filter.return_value = MagicMock()

        parent = FilterTreeNode("root", parent_mc, "", "noSelection", [child_a, child_b])
        parent.evaluate()

        filter_kwargs = parent_mc.model_class.objects.filter.call_args[1]
        self.assertIn("fk_a__in", filter_kwargs)
        self.assertIn("fk_b__in", filter_kwargs)


# ════════════════════════════════════════════════════════════════════════
#  write_self_to_dict()
# ════════════════════════════════════════════════════════════════════════

class TestFilterTreeNodeWriteSelfToDict(SimpleTestCase):
    """Verify tree serialization to {node_id: [pk...]} dict."""

    def test_leaf_writes_own_pks(self):
        mc = _mock_container()
        node = _make_leaf("leaf-1", mc)

        # Simulate evaluated objects
        obj1 = MagicMock()
        obj1.pk = 10
        obj2 = MagicMock()
        obj2.pk = 20
        node.filtered_objects = [obj1, obj2]

        result = {}
        node.write_self_to_dict(result)

        self.assertIn("leaf-1", result)
        self.assertEqual(sorted(result["leaf-1"]), [10, 20])

    def test_parent_writes_self_and_children(self):
        """write_self_to_dict recurses into children."""
        parent_mc = _mock_container()
        child_mc = _mock_container()

        parent = FilterTreeNode("parent", parent_mc, "", "noSelection", [])
        child = FilterTreeNode("child", child_mc, "fk", "noSelection", [])
        parent.children = [child]

        parent_obj = MagicMock()
        parent_obj.pk = 1
        parent.filtered_objects = [parent_obj]

        child_obj = MagicMock()
        child_obj.pk = 100
        child.filtered_objects = [child_obj]

        result = {}
        parent.write_self_to_dict(result)

        self.assertIn("parent", result)
        self.assertIn("child", result)
        self.assertEqual(result["parent"], [1])
        self.assertEqual(result["child"], [100])

    def test_empty_filtered_objects(self):
        """Node with no filtered objects writes empty list."""
        mc = _mock_container()
        node = _make_leaf("empty", mc)
        node.filtered_objects = []

        result = {}
        node.write_self_to_dict(result)
        self.assertEqual(result["empty"], [])
