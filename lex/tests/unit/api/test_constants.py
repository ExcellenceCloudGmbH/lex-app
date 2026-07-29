"""
Unit tests for ``lex.process_admin.models.constants``.

**What this tests (customer-visible behaviour)**

The ``RELATION_FIELD_TYPES`` constant defines which Django field types
are treated as relational fields when building the model hierarchy and
dependency graph.  If a field type is missing, its relationships are
invisible — breaking cascading calculations and filter trees.

**Why it matters**

Adding a new relation type to Django (or removing one from the set
accidentally) silently breaks the entire dependency resolution system.

**Methodology**

Pure constant inspection — no DB.

Run::

    python manage.py test lex.process_admin.tests.test_constants
"""

from django.test import SimpleTestCase

from lex.process_admin.models.constants import (
    FOREIGN_KEY_NAME,
    MANY_TO_MANY_NAME,
    ONE_TO_ONE_NAME,
    RELATION_FIELD_TYPES,
)


class TestRelationFieldConstants(SimpleTestCase):
    """Prove the relation field type constants are correct."""

    def test_foreign_key_name(self):
        self.assertEqual(FOREIGN_KEY_NAME, "ForeignKey")

    def test_many_to_many_name(self):
        self.assertEqual(MANY_TO_MANY_NAME, "ManyToManyField")

    def test_one_to_one_name(self):
        self.assertEqual(ONE_TO_ONE_NAME, "OneToOneField")

    def test_relation_field_types_is_set(self):
        self.assertIsInstance(RELATION_FIELD_TYPES, set)

    def test_relation_field_types_contains_all(self):
        self.assertIn("ForeignKey", RELATION_FIELD_TYPES)
        self.assertIn("ManyToManyField", RELATION_FIELD_TYPES)
        self.assertIn("OneToOneField", RELATION_FIELD_TYPES)

    def test_relation_field_types_has_exactly_three(self):
        self.assertEqual(len(RELATION_FIELD_TYPES), 3)
