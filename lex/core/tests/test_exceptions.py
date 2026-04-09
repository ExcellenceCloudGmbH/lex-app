"""
Unit tests for ``lex.core.exceptions`` — pure utility functions and exception classes.

**What this tests (customer-visible behaviour)**

The exception module provides helpers that extract, normalize, and select
the most informative error messages from (possibly chained) exceptions.
These messages are stored in ``calculation_error_message`` on every
CalculationModel and displayed in the frontend Calculation Log panel.

**Why it matters**

When a calculation fails, the user sees the message chosen by
``resolve_exception_detail``.  If the selection logic has a bug, users see
useless "A server error occurred" instead of the actual root cause.

**Methodology**

All functions under test are pure (no I/O, no DB).  We use ``SimpleTestCase``
and build exception objects / chains manually.

Run::

    python manage.py test lex.core.tests.test_exceptions
"""

from django.test import SimpleTestCase

from lex.core.exceptions import (
    ensure_list,
    iter_exception_chain,
    _normalize_string,
    select_preferred_exception_detail,
    select_preferred_stack_trace,
    find_exception_artifacts,
    resolve_exception_detail,
    resolve_exception_traceback,
    ValidationError,
    CalculatedModelError,
    ModelCreationError,
    ModelCombinationError,
    ModelClusteringError,
    CeleryDispatchError,
    GENERIC_SERVER_ERROR_MESSAGES,
)


class TestEnsureList(SimpleTestCase):
    """Prove ``ensure_list`` normalizes any input into a list."""

    def test_none_returns_empty(self):
        self.assertEqual(ensure_list(None), [])

    def test_list_passes_through(self):
        self.assertEqual(ensure_list([1, 2]), [1, 2])

    def test_tuple_converts_to_list(self):
        self.assertEqual(ensure_list((1, 2)), [1, 2])

    def test_scalar_wraps_in_list(self):
        self.assertEqual(ensure_list("hello"), ["hello"])
        self.assertEqual(ensure_list(42), [42])

    def test_empty_list_passes_through(self):
        self.assertEqual(ensure_list([]), [])


class TestIterExceptionChain(SimpleTestCase):
    """Prove ``iter_exception_chain`` follows __cause__ and __context__."""

    def test_single_exception(self):
        exc = ValueError("root")
        chain = list(iter_exception_chain(exc))
        self.assertEqual(len(chain), 1)
        self.assertIs(chain[0], exc)

    def test_explicit_cause_chain(self):
        root = ValueError("root")
        wrapper = RuntimeError("wrapper")
        wrapper.__cause__ = root
        chain = list(iter_exception_chain(wrapper))
        self.assertEqual(len(chain), 2)
        self.assertIs(chain[0], wrapper)
        self.assertIs(chain[1], root)

    def test_context_chain(self):
        root = ValueError("root")
        wrapper = RuntimeError("wrapper")
        wrapper.__context__ = root
        wrapper.__cause__ = None
        chain = list(iter_exception_chain(wrapper))
        self.assertEqual(len(chain), 2)

    def test_none_yields_nothing(self):
        self.assertEqual(list(iter_exception_chain(None)), [])

    def test_circular_chain_does_not_loop(self):
        a = ValueError("a")
        b = ValueError("b")
        a.__cause__ = b
        b.__cause__ = a
        chain = list(iter_exception_chain(a))
        self.assertEqual(len(chain), 2)


class TestNormalizeString(SimpleTestCase):
    """Prove ``_normalize_string`` strips whitespace and returns None for blanks."""

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_string(None))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_normalize_string("   "))

    def test_strips_whitespace(self):
        self.assertEqual(_normalize_string("  hello  "), "hello")

    def test_non_string_converted(self):
        self.assertEqual(_normalize_string(42), "42")


class TestSelectPreferredExceptionDetail(SimpleTestCase):
    """Prove the selection logic prefers specific messages over generic ones."""

    def test_prefers_specific_over_generic(self):
        details = ["A server error occurred.", "Division by zero in column B"]
        self.assertEqual(
            select_preferred_exception_detail(details),
            "Division by zero in column B",
        )

    def test_falls_back_to_generic_when_only_option(self):
        details = ["A server error occurred."]
        self.assertEqual(
            select_preferred_exception_detail(details),
            "A server error occurred.",
        )

    def test_none_returns_none(self):
        self.assertIsNone(select_preferred_exception_detail(None))

    def test_empty_list_returns_none(self):
        self.assertIsNone(select_preferred_exception_detail([]))

    def test_filters_out_blank_strings(self):
        details = ["", "  ", "Real error"]
        self.assertEqual(
            select_preferred_exception_detail(details),
            "Real error",
        )

    def test_single_detail_not_in_generic_set(self):
        self.assertEqual(
            select_preferred_exception_detail("Custom error"),
            "Custom error",
        )


class TestSelectPreferredStackTrace(SimpleTestCase):
    """Prove stack trace selection returns the first non-empty trace."""

    def test_returns_first_valid_trace(self):
        traces = [None, "", "  ", "Traceback (most recent call last):"]
        self.assertEqual(
            select_preferred_stack_trace(traces),
            "Traceback (most recent call last):",
        )

    def test_none_returns_none(self):
        self.assertIsNone(select_preferred_stack_trace(None))

    def test_all_blank_returns_none(self):
        self.assertIsNone(select_preferred_stack_trace(["", "  "]))


class TestFindExceptionArtifacts(SimpleTestCase):
    """Prove ``find_exception_artifacts`` walks the chain and finds attributes."""

    def test_finds_attributes_on_direct_exception(self):
        exc = RuntimeError("fail")
        exc.exception_details = ["Detail one"]
        exc.stack_trace = ["trace"]
        calc_obj, details, trace = find_exception_artifacts(exc)
        self.assertEqual(details, ["Detail one"])
        self.assertEqual(trace, ["trace"])

    def test_returns_empty_when_no_artifacts(self):
        exc = RuntimeError("plain")
        calc_obj, details, trace = find_exception_artifacts(exc)
        self.assertEqual(calc_obj, [])
        self.assertEqual(details, [])
        self.assertEqual(trace, [])

    def test_finds_attributes_on_chained_exception(self):
        root = ValueError("root")
        root.exception_details = ["Root detail"]
        wrapper = RuntimeError("wrapper")
        wrapper.__cause__ = root
        _, details, _ = find_exception_artifacts(wrapper)
        self.assertEqual(details, ["Root detail"])


class TestResolveExceptionDetail(SimpleTestCase):
    """Prove ``resolve_exception_detail`` picks the best error message."""

    def test_uses_exception_details_attribute(self):
        exc = RuntimeError("generic")
        exc.exception_details = ["Specific error"]
        self.assertEqual(resolve_exception_detail(exc), "Specific error")

    def test_falls_back_to_str_of_exception(self):
        exc = RuntimeError("Meaningful message")
        self.assertEqual(resolve_exception_detail(exc), "Meaningful message")

    def test_uses_fallback_when_exception_is_none(self):
        self.assertEqual(resolve_exception_detail(None, "Fallback"), "Fallback")

    def test_returns_none_when_everything_empty(self):
        self.assertIsNone(resolve_exception_detail(None))


class TestResolveExceptionTraceback(SimpleTestCase):
    """Prove ``resolve_exception_traceback`` extracts the best traceback."""

    def test_uses_stack_trace_attribute(self):
        exc = RuntimeError("fail")
        exc.stack_trace = ["Traceback here"]
        self.assertEqual(resolve_exception_traceback(exc), "Traceback here")

    def test_uses_fallback_when_no_trace(self):
        exc = RuntimeError("fail")
        self.assertEqual(
            resolve_exception_traceback(exc, "Fallback trace"),
            "Fallback trace",
        )

    def test_returns_none_when_everything_empty(self):
        self.assertIsNone(resolve_exception_traceback(None))


class TestValidationError(SimpleTestCase):
    """Prove ``ValidationError`` preserves original exception and model class."""

    def test_basic_construction(self):
        original = ValueError("bad data")
        exc = ValidationError("Validation failed", original_exception=original, model_class="Period")
        self.assertEqual(str(exc), "Validation failed")
        self.assertIs(exc.original_exception, original)
        self.assertEqual(exc.model_class, "Period")

    def test_is_exception(self):
        exc = ValidationError("test")
        self.assertIsInstance(exc, Exception)


class TestCalculatedModelError(SimpleTestCase):
    """Prove ``CalculatedModelError`` builds detailed messages with context."""

    def test_basic_message(self):
        exc = CalculatedModelError("Calculation failed")
        self.assertIn("Calculation failed", str(exc))

    def test_includes_model_class(self):
        exc = CalculatedModelError("Failed", model_class="Period")
        self.assertIn("[Period]", str(exc))

    def test_includes_context_kwargs(self):
        exc = CalculatedModelError("Failed", field="amount", record_id=42)
        self.assertIn("field=amount", str(exc))
        self.assertIn("record_id=42", str(exc))

    def test_is_exception(self):
        self.assertIsInstance(CalculatedModelError("x"), Exception)


class TestModelCombinationError(SimpleTestCase):
    """Prove ``ModelCombinationError`` includes field name in message."""

    def test_includes_field_name(self):
        exc = ModelCombinationError("No values", field_name="period")
        self.assertIn("(field: period)", str(exc))

    def test_includes_model_class(self):
        exc = ModelCombinationError("No values", model_class="NAV", field_name="period")
        self.assertIn("[NAV]", str(exc))


class TestModelClusteringError(SimpleTestCase):
    """Prove ``ModelClusteringError`` includes parallelizable fields info."""

    def test_includes_model_count(self):
        exc = ModelClusteringError("Clustering failed", model_count=12)
        self.assertIn("processing 12 models", str(exc))

    def test_includes_parallelizable_fields(self):
        exc = ModelClusteringError("Failed", parallelizable_fields=["period", "fund"])
        self.assertIn("period", str(exc))
        self.assertIn("fund", str(exc))


class TestCeleryDispatchError(SimpleTestCase):
    """Prove ``CeleryDispatchError`` includes group and task info."""

    def test_includes_group_info(self):
        exc = CeleryDispatchError("Dispatch failed", group_index=2, group_size=5)
        self.assertIn("group 3", str(exc))  # group_index + 1
        self.assertIn("5 models", str(exc))

    def test_includes_task_id(self):
        exc = CeleryDispatchError("Failed", task_id="abc-123")
        self.assertIn("Task ID: abc-123", str(exc))

    def test_is_calculated_model_error(self):
        exc = CeleryDispatchError("x")
        self.assertIsInstance(exc, CalculatedModelError)
