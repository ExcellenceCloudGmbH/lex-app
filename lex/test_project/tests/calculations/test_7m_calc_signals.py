"""Tests for model_name propagation through the calculation signal chain (PR #615).

Intent: PR #615 added ``model_name=instance._meta.object_name`` to every
``ActiveCalculationStateStore.mark_in_progress`` call.  The intent is that
the active-calculations snapshot (consumed by the Instance Controller via
``GET /api/active-calculations``) carries the human-readable Django class
name (``"AtomicCalc"``, not the lower-case model_name ``"atomiccalc"``) so
the release-safety UI can present meaningful labels without a follow-up
schema call.

Three source paths now propagate ``model_name``:

1. ``CalculationSignals.update_calculation_status`` — called on every
   state transition (IN_PROGRESS / SUCCESS / ERROR / CANCELLED / ABORTED).
   Only the IN_PROGRESS branch calls ``mark_in_progress``; the others call
   ``clear``.  A regression that drops ``model_name`` from the IN_PROGRESS
   branch leaves every snapshot entry with an empty string that the IC
   cannot render.

2. ``One.OneModelEntry.update`` — the early-registration path that writes
   the state-store entry *before* entering the atomic transaction block so
   page-refreshes during a calculation see the spinner immediately.  Without
   ``model_name`` this path and the signal path produce inconsistent entries
   (one blank, one populated) depending on which write lands first.

Cluster 7m — scenarios 7.188–7.195. Type: U (SimpleTestCase — scenarios
7.188–7.191 use a fake unmanaged model and mock the store so no DB is
needed) + E (E2ETestCase — scenario 7.192 drives the real REST endpoint to
pin the One.py path end-to-end).
Covers: ``lex/core/signals/CalculationSignals.py``,
``lex/api/views/model_entries/One.py``.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7m_calc_signals.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.CalculationSignals import update_calculation_status
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc

pytestmark = pytest.mark.calculations

_MARK_IN_PROGRESS = (
    "lex.core.signals.ActiveCalculationStateStore"
    ".ActiveCalculationStateStore.mark_in_progress"
)
_CLEAR = (
    "lex.core.signals.ActiveCalculationStateStore"
    ".ActiveCalculationStateStore.clear"
)
_SYNC_SEND = (
    "lex.utilities.channel_layer.sync_channel_group_send"
)


class _FakeCalcMeta:
    """Minimal ``_meta`` shim for a fake CalculationModel subclass."""

    def __init__(self, object_name: str, model_name: str, label_lower: str):
        self.object_name = object_name
        self.model_name = model_name
        self.label_lower = label_lower


def _make_fake_instance(is_calculated: str, *, object_name: str = "FakeCalc") -> object:
    """Return a minimal fake instance that satisfies ``update_calculation_status``."""

    class _FakeCalcClass(CalculationModel):
        class Meta:
            app_label = "lex_app"
            managed = False

        def calculate(self):  # pragma: no cover
            pass

    instance = MagicMock(spec=_FakeCalcClass)
    instance.__class__ = _FakeCalcClass
    instance.is_calculated = is_calculated
    instance._meta = _FakeCalcMeta(
        object_name=object_name,
        model_name=object_name.lower(),
        label_lower=f"lex_app.{object_name.lower()}",
    )
    instance.id = 99
    instance.pk = 99
    instance.__str__ = lambda self: f"{object_name} #99"
    return instance


# ── CalculationSignals unit tests ─────────────────────────────────────────────


class TestCluster07m_SignalModelName(SimpleTestCase):
    """Cluster 7m: ``update_calculation_status`` passes model_name correctly."""

    # 7.188 --------------------------------------------------------------
    def test_7_188_in_progress_passes_object_name_as_model_name(self) -> None:
        """
        Scenario 7.188: IN_PROGRESS path includes model_name=instance._meta.object_name.
        Given: a ``CalculationModel`` instance with ``is_calculated=IN_PROGRESS``
               and ``_meta.object_name == "FakeCalc"``.
        When: ``update_calculation_status(instance)`` is called.
        Then: ``mark_in_progress`` is called with ``model_name="FakeCalc"``
              (the Django class name, not the lower-case model_name).
        """
        instance = _make_fake_instance(CalculationModel.IN_PROGRESS, object_name="FakeCalc")

        with patch(_MARK_IN_PROGRESS) as mock_mark, patch(_SYNC_SEND):
            update_calculation_status(instance)

        mock_mark.assert_called_once()
        _, kwargs = mock_mark.call_args
        self.assertEqual(
            kwargs.get("model_name"),
            "FakeCalc",
            "mark_in_progress must receive model_name=instance._meta.object_name "
            "for the IN_PROGRESS branch — snapshot labels depend on this",
        )

    # 7.189 --------------------------------------------------------------
    def test_7_189_success_does_not_call_mark_in_progress(self) -> None:
        """
        Scenario 7.189: SUCCESS branch calls clear, not mark_in_progress.
        Given: instance with ``is_calculated=SUCCESS``.
        When: ``update_calculation_status(instance)`` is called.
        Then: ``mark_in_progress`` is never called; ``clear`` is called once.
        """
        instance = _make_fake_instance(CalculationModel.SUCCESS)

        with patch(_MARK_IN_PROGRESS) as mock_mark, \
             patch(_CLEAR) as mock_clear, \
             patch(_SYNC_SEND):
            update_calculation_status(instance)

        mock_mark.assert_not_called()
        mock_clear.assert_called_once()

    # 7.190 --------------------------------------------------------------
    def test_7_190_error_does_not_call_mark_in_progress(self) -> None:
        """
        Scenario 7.190: ERROR branch calls clear, not mark_in_progress.
        Given: instance with ``is_calculated=ERROR``.
        When: ``update_calculation_status(instance)`` is called.
        Then: ``mark_in_progress`` is never called; ``clear`` is called once.
        """
        instance = _make_fake_instance(CalculationModel.ERROR)

        with patch(_MARK_IN_PROGRESS) as mock_mark, \
             patch(_CLEAR) as mock_clear, \
             patch(_SYNC_SEND):
            update_calculation_status(instance)

        mock_mark.assert_not_called()
        mock_clear.assert_called_once()

    # 7.191 --------------------------------------------------------------
    def test_7_191_non_calculation_model_returns_early(self) -> None:
        """
        Scenario 7.191: Non-CalculationModel instance triggers early return.
        Given: an instance whose class does NOT subclass ``CalculationModel``.
        When: ``update_calculation_status(instance)`` is called.
        Then: neither ``mark_in_progress`` nor ``clear`` nor ``sync_channel_group_send``
              is invoked — the guard at the top of the function short-circuits cleanly.
        """

        class _NotACalcModel:
            is_calculated = CalculationModel.IN_PROGRESS

        instance = _NotACalcModel()

        with patch(_MARK_IN_PROGRESS) as mock_mark, \
             patch(_CLEAR) as mock_clear, \
             patch(_SYNC_SEND) as mock_send:
            update_calculation_status(instance)

        mock_mark.assert_not_called()
        mock_clear.assert_not_called()
        mock_send.assert_not_called()


# ── One.py integration test ───────────────────────────────────────────────────


class TestCluster07m_OneModelNamePropagation(E2ETestCase):
    """Cluster 7m: One.py early-registration path passes model_name."""

    e2e_models = ALL_MODELS
    # Disable the default ``mark_in_progress`` no-op patch so the spy
    # installed per-test captures the actual call the view makes.
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self) -> None:
        super().setUp()
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

        ActiveCalculationStateStore.clear_all()

    def tearDown(self) -> None:
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

        ActiveCalculationStateStore.clear_all()
        super().tearDown()

    # 7.192 --------------------------------------------------------------
    def test_7_192_patch_calculate_true_passes_model_name_to_store(self) -> None:
        """
        Scenario 7.192: One.py early registration includes model_name=object_name.
        Given: an ``AtomicCalc`` row that is not yet running (NOT_CALCULATED).
        When: the client sends ``PATCH /<model>/<pk>/`` with ``{"calculate":"true"}``.
        Then: the ``mark_in_progress`` spy is called with
              ``model_name=instance._meta.object_name`` (i.e. ``"AtomicCalc"``).
        """
        calc = AtomicCalc.objects.create(name="signal-chain-test")

        spy = self.spy_on("mark_in_progress")

        # Suppress the async dispatch so the test does not spin up a thread.
        with patch(
            "lex.api.views.model_entries.One._calculation_executor",
            create=True,
        ) as mock_executor:
            mock_executor.submit = lambda fn: None  # fire-and-forget no-op
            response = self.client.patch(
                self.url_detail("atomiccalc", calc.pk),
                data={"calculate": "true"},
                format="json",
            )

        # We only assert that mark_in_progress was called with the right
        # model_name — not the full dispatch lifecycle (already covered by
        # existing clusters 9d / 10n / 7n).
        self.assertTrue(
            spy.called,
            "mark_in_progress must be called when a calculate=true PATCH is accepted",
        )
        _, kwargs = spy.call_args
        self.assertEqual(
            kwargs.get("model_name"),
            "AtomicCalc",
            "One.py must pass model_name=instance._meta.object_name (the class name "
            "'AtomicCalc', not the lower-case 'atomiccalc') to mark_in_progress so "
            "the active-calculations snapshot carries a human-readable label",
        )

    # 7.193 --------------------------------------------------------------
    def test_7_193_mark_in_progress_receives_matching_record_id_and_model_name(self) -> None:
        """
        Scenario 7.193: record_id and model_name are consistent in the One.py call.
        Given: an ``AtomicCalc`` instance with a known pk.
        When: ``calculate=true`` PATCH is sent.
        Then: ``mark_in_progress`` is called with ``record_id`` of the form
              ``"atomiccalc_<pk>"`` AND ``model_name="AtomicCalc"``, proving
              the two naming fields are internally consistent (lower-case for the
              store key, title-case for the display label).
        """
        calc = AtomicCalc.objects.create(name="consistency-test")

        spy = self.spy_on("mark_in_progress")

        with patch(
            "lex.api.views.model_entries.One._calculation_executor",
            create=True,
        ) as mock_executor:
            mock_executor.submit = lambda fn: None
            self.client.patch(
                self.url_detail("atomiccalc", calc.pk),
                data={"calculate": "true"},
                format="json",
            )

        self.assertTrue(
            spy.called,
            "mark_in_progress must be called for calculate=true PATCH",
        )
        _, kwargs = spy.call_args
        expected_record_id = f"atomiccalc_{calc.pk}"
        self.assertEqual(
            kwargs.get("record_id"),
            expected_record_id,
            f"record_id must be 'atomiccalc_<pk>' (lower-case model_name + pk); "
            f"got {kwargs.get('record_id')!r}",
        )
        self.assertEqual(
            kwargs.get("model_name"),
            "AtomicCalc",
            "model_name must be the title-case class name from _meta.object_name",
        )
