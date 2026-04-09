"""
Unit tests for ``CalculationModel`` helper methods and ``CalculationModelException``.

**What this tests (customer-visible behaviour)**

``build_exception_chain`` builds the (calc_obj, exception_details,
stack_trace) triple that determines what error info is saved to the
database and shown to the user when a calculation fails.
``CalculationModelException`` wraps this triple into a DRF APIException
for HTTP error responses.

**Why it matters**

When a calculation fails, the user sees the error message selected from
the chain.  If ``build_exception_chain`` loses the current calc object
or drops the exception details, the error becomes impossible to debug.

**Methodology**

Pure functions — no DB.  We build mock exception objects with the
expected attributes and verify the chain assembly logic.

Run::

    python manage.py test lex.core.tests.test_calculation_model_helpers
"""

from django.test import SimpleTestCase

from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)


class TestCalculationModelConstants(SimpleTestCase):
    """Prove the state constants are correct."""

    def test_status_values(self):
        self.assertEqual(CalculationModel.IN_PROGRESS, "IN_PROGRESS")
        self.assertEqual(CalculationModel.ERROR, "ERROR")
        self.assertEqual(CalculationModel.SUCCESS, "SUCCESS")
        self.assertEqual(CalculationModel.NOT_CALCULATED, "NOT_CALCULATED")
        self.assertEqual(CalculationModel.ABORTED, "ABORTED")

    def test_statuses_tuple_contains_all(self):
        status_values = [s[0] for s in CalculationModel.STATUSES]
        self.assertIn("IN_PROGRESS", status_values)
        self.assertIn("ERROR", status_values)
        self.assertIn("SUCCESS", status_values)
        self.assertIn("NOT_CALCULATED", status_values)
        self.assertIn("ABORTED", status_values)

    def test_statuses_are_pairs(self):
        for status in CalculationModel.STATUSES:
            self.assertEqual(len(status), 2)
            self.assertEqual(status[0], status[1])


class TestBuildExceptionChain(SimpleTestCase):
    """Prove ``build_exception_chain`` assembles the chain correctly."""

    def test_no_artifacts_on_plain_exception(self):
        exc = RuntimeError("simple error")
        calc_obj, details, trace = CalculationModel.build_exception_chain(exc)
        self.assertEqual(calc_obj, [])
        self.assertEqual(details, [])
        self.assertEqual(trace, [])

    def test_appends_current_obj(self):
        exc = RuntimeError("error")
        obj = object()
        calc_obj, _, _ = CalculationModel.build_exception_chain(exc, current_obj=obj)
        self.assertIn(obj, calc_obj)

    def test_does_not_duplicate_current_obj(self):
        """If calc_obj already ends with current_obj, don't append again."""
        exc = RuntimeError("error")
        obj = object()
        exc.calc_obj = [obj]
        calc_obj, _, _ = CalculationModel.build_exception_chain(exc, current_obj=obj)
        self.assertEqual(calc_obj.count(obj), 1)

    def test_preserves_existing_artifacts(self):
        exc = RuntimeError("error")
        exc.exception_details = ["Detail A"]
        exc.stack_trace = ["trace"]
        exc.calc_obj = ["obj1"]
        calc_obj, details, trace = CalculationModel.build_exception_chain(exc)
        self.assertEqual(details, ["Detail A"])
        self.assertEqual(trace, ["trace"])
        self.assertIn("obj1", calc_obj)

    def test_current_obj_none_is_noop(self):
        exc = RuntimeError("error")
        calc_obj, _, _ = CalculationModel.build_exception_chain(exc, current_obj=None)
        self.assertEqual(calc_obj, [])


class TestCalculationModelException(SimpleTestCase):
    """Prove ``CalculationModelException`` stores and selects error info."""

    def test_stores_calc_obj(self):
        exc = CalculationModelException(calc_obj="obj1")
        self.assertEqual(exc.calc_obj, ["obj1"])

    def test_stores_exception_details(self):
        exc = CalculationModelException(exception_details=["Error A"])
        self.assertEqual(exc.exception_details, ["Error A"])

    def test_stores_stack_trace(self):
        exc = CalculationModelException(stack_trace=["trace"])
        self.assertEqual(exc.stack_trace, ["trace"])

    def test_selects_preferred_detail_as_message(self):
        exc = CalculationModelException(
            exception_details=["A server error occurred.", "Real error"]
        )
        self.assertIn("Real error", str(exc))

    def test_fallback_to_args_message(self):
        exc = CalculationModelException("Direct message")
        self.assertIn("Direct message", str(exc))

    def test_none_kwargs_become_empty_lists(self):
        exc = CalculationModelException()
        self.assertEqual(exc.calc_obj, [])
        self.assertEqual(exc.exception_details, [])
        self.assertEqual(exc.stack_trace, [])

    def test_is_api_exception(self):
        from rest_framework.exceptions import APIException
        exc = CalculationModelException("test")
        self.assertIsInstance(exc, APIException)
