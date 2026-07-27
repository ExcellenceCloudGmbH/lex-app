"""
Tests for ``LexModel`` atomic save behaviour.

Verifies:
    • Models with default ``is_atomic=True`` run lifecycle hooks inside
      a ``transaction.atomic()`` block
    • Models with ``is_atomic=False`` skip the atomic wrapper
"""

from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase
from django_lifecycle import AFTER_SAVE, hook
from lex.core.models.LexModel import LexModel


class AtomicLifecycleSaveModel(LexModel):
    class Meta:
        app_label = "lex_app"
        managed = False

    @hook(AFTER_SAVE)
    def capture_atomic_state(self):
        self.observed_atomic_states.append(connection.in_atomic_block)


class NonAtomicLifecycleSaveModel(LexModel):
    is_atomic = False

    class Meta:
        app_label = "lex_app"
        managed = False

    @hook(AFTER_SAVE)
    def capture_atomic_state(self):
        self.observed_atomic_states.append(connection.in_atomic_block)


class LexModelAtomicSaveTests(TransactionTestCase):
    def _base_save_side_effect(self, captured_states):
        def side_effect(_instance, *args, **kwargs):
            captured_states.append(connection.in_atomic_block)

        return side_effect

    def test_atomic_models_keep_lifecycle_save_transaction(self):
        instance = AtomicLifecycleSaveModel(id=1)
        instance.observed_atomic_states = []
        base_save_states = []

        with patch(
            "django.db.models.base.Model.save",
            autospec=True,
            side_effect=self._base_save_side_effect(base_save_states),
        ):
            instance.save()

        self.assertEqual(base_save_states, [True])
        self.assertEqual(instance.observed_atomic_states, [True])

    def test_non_atomic_models_run_lifecycle_hooks_outside_implicit_transaction(self):
        instance = NonAtomicLifecycleSaveModel(id=2)
        instance.observed_atomic_states = []
        base_save_states = []

        with patch(
            "django.db.models.base.Model.save",
            autospec=True,
            side_effect=self._base_save_side_effect(base_save_states),
        ):
            instance.save()

        self.assertEqual(base_save_states, [False])
        self.assertEqual(instance.observed_atomic_states, [False])

    def test_non_atomic_models_skip_hooks_without_implicit_transaction(self):
        instance = NonAtomicLifecycleSaveModel(id=3)
        instance.observed_atomic_states = []
        base_save_states = []

        with patch(
            "django.db.models.base.Model.save",
            autospec=True,
            side_effect=self._base_save_side_effect(base_save_states),
        ):
            instance.save(skip_hooks=True)

        self.assertEqual(base_save_states, [False])
        self.assertEqual(instance.observed_atomic_states, [])
