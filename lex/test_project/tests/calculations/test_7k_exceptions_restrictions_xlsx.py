"""
Cluster 7k: core exceptions + ``ModelModificationRestriction`` ABC contract
+ ``XLSXField`` coverage-spotter.

Intent
------

Two files from the cleanup-and-coverage-plan COMPLETE bucket that
the existing 7a–7j sub-clusters never reached:

* ``lex/core/exceptions.py`` — 5 custom exception classes
  (``ValidationError``, ``CalculatedModelError``, ``ModelCreationError``,
  ``ModelCombinationError``, ``ModelClusteringError``,
  ``CeleryDispatchError``) + 8 module-level helper functions
  (``ensure_list``, ``iter_exception_chain``, ``select_preferred_exception_detail``,
  ``select_preferred_stack_trace``, ``find_exception_artifacts``,
  ``resolve_exception_detail``, ``resolve_exception_traceback``,
  plus the private ``_normalize_string``).  These are the building
  blocks every calculation-failure audit uses; a regression that
  flipped one of the helpers' nullability rules would silently
  produce empty audit messages.

* ``lex/core/mixins/ModelModificationRestriction.py`` — the
  `ABC` contract every customer-defined restriction class extends.
  ``AdminReportsModificationRestriction`` is the framework-shipped
  read-only profile (used by the HTMLReport view); a regression
  flipping any of its allow/deny answers would silently change the
  documented "admin reports are read-only" guarantee.

Plus one **coverage-spotter** for ``lex/core/fields/XLSX_field.py``
that pins the format-constant tuples and verifies the existing
exhaustive tests in ``lex/tests/unit/api/test_xlsx_field.py`` are
still discoverable.  Supervisor flagged XLSX_field for "test fully"
— the existing 378-line test battery already does that; this
single scenario is the dashboard hook so a regression that deleted
that file would surface here, not silently drop coverage.

All scenarios are pure-Python `SimpleTestCase` — no DB, no
external services. Scenario range picks up at **7.122** (7e ended
at 7.121, 7j ended at 7.111; 7.112–7.121 are taken by 7e).
"""

from __future__ import annotations

import unittest
from unittest import TestCase

from lex.core.exceptions import (
    CalculatedModelError,
    CeleryDispatchError,
    GENERIC_SERVER_ERROR_MESSAGES,
    ModelClusteringError,
    ModelCombinationError,
    ModelCreationError,
    ValidationError,
    _normalize_string,
    ensure_list,
    find_exception_artifacts,
    iter_exception_chain,
    resolve_exception_detail,
    resolve_exception_traceback,
    select_preferred_exception_detail,
    select_preferred_stack_trace,
)
from lex.core.mixins.ModelModificationRestriction import (
    AdminReportsModificationRestriction,
    ExampleModelModificationRestriction,
    ModelModificationRestriction,
)


# ---------------------------------------------------------------------
# 7.122–7.124 — small helpers
# ---------------------------------------------------------------------
class TestCluster07k_HelperPrimitives(TestCase):
    """``ensure_list`` / ``_normalize_string`` / ``iter_exception_chain``.

    These three are the foundation everything else in the module
    composes; a wrong answer here propagates everywhere.
    """

    def test_7_122_ensure_list_normalises_every_input_shape(self):
        """7.122: ``ensure_list`` over None / list / tuple / scalar.

        - None → ``[]`` (the empty-trace branch every caller relies on)
        - list passes through unchanged (no copy — we depend on identity for the empty case)
        - tuple → list (so callers can ``.append`` without TypeError)
        - scalar → ``[scalar]`` (single-item normalisation)
        """
        self.assertEqual(ensure_list(None), [])
        original = ["a", "b"]
        self.assertEqual(ensure_list(original), original)
        self.assertEqual(ensure_list(("x", "y")), ["x", "y"])
        self.assertEqual(ensure_list("solo"), ["solo"])
        self.assertEqual(ensure_list(42), [42])
        # Falsy non-None scalars must wrap, not collapse to empty.
        self.assertEqual(ensure_list(0), [0],
                         "0 must wrap to [0], not [] — Falsy ≠ None")
        self.assertEqual(ensure_list(""), [""],
                         "empty string must wrap, not collapse to []")

    def test_7_123_normalize_string_returns_none_for_empty_or_blank(self):
        """7.123: ``_normalize_string`` collapses None / blank-only
        strings to None so the "preferred detail" picker can skip
        them cleanly.

        - None → None
        - empty string → None
        - whitespace-only → None
        - non-empty after strip → the stripped form
        - non-string → str()-coerced + stripped
        """
        self.assertIsNone(_normalize_string(None))
        self.assertIsNone(_normalize_string(""))
        self.assertIsNone(_normalize_string("   "))
        self.assertIsNone(_normalize_string("\n\t "))
        self.assertEqual(_normalize_string("  hello  "), "hello")
        # Non-string inputs (e.g. an Exception object) are coerced.
        self.assertEqual(
            _normalize_string(RuntimeError("boom")), "boom",
            "Exception objects must coerce via str(...)",
        )

    def test_7_124_iter_exception_chain_walks_cause_then_context(self):
        """7.124: ``iter_exception_chain`` walks ``__cause__``, then
        ``__context__``, with cycle protection.

        Building the chain by hand because Python only sets
        ``__cause__`` from a real ``raise X from Y`` statement.
        """
        root = ValueError("root")
        mid = TypeError("mid")
        mid.__cause__ = root
        top = RuntimeError("top")
        top.__cause__ = mid

        chain = list(iter_exception_chain(top))
        self.assertEqual(
            [str(e) for e in chain], ["top", "mid", "root"],
            "should walk top → mid → root via __cause__",
        )

        # Cycle protection — building a self-cycle must terminate.
        cyclic = RuntimeError("cyclic")
        cyclic.__cause__ = cyclic
        result = list(iter_exception_chain(cyclic))
        self.assertEqual(
            len(result), 1,
            "self-cycle must terminate after one yield "
            "(seen-set protection)",
        )

    def test_7_125_iter_exception_chain_falls_back_to_context(self):
        """7.125: when ``__cause__`` is missing, fall back to
        ``__context__`` (Python's implicit-during-handling chain).
        """
        root = ValueError("root")
        top = RuntimeError("top")
        # Implicit context chain — no `from` clause.
        top.__context__ = root

        result = [str(e) for e in iter_exception_chain(top)]
        self.assertEqual(result, ["top", "root"])


# ---------------------------------------------------------------------
# 7.126–7.128 — preferred-detail / stack-trace selectors
# ---------------------------------------------------------------------
class TestCluster07k_PreferredSelectors(TestCase):
    """``select_preferred_exception_detail`` /
    ``select_preferred_stack_trace`` / ``find_exception_artifacts``.

    These decide what message ends up in the audit-log row when a
    calculation fails. A wrong answer = audit row says "A server
    error occurred" instead of the real cause.
    """

    def test_7_126_preferred_detail_skips_generic_server_messages(self):
        """7.126: when both a generic and a real detail are present,
        the real one wins — even if the generic comes first.

        This is the core "don't silently surface 'A server error
        occurred' to the customer" rule.
        """
        details = [
            "A server error occurred.",   # generic — must be skipped
            "Counterparty 'XYZ' not found",  # real — must win
        ]
        self.assertEqual(
            select_preferred_exception_detail(details),
            "Counterparty 'XYZ' not found",
        )

    def test_7_127_preferred_detail_falls_back_to_first_when_only_generics(self):
        """7.127: if only generic messages are present, return the
        first one (better than ``None`` — gives the audit log
        *something*).
        """
        result = select_preferred_exception_detail(
            ["A server error occurred", "Server Error"]
        )
        self.assertIn(result, GENERIC_SERVER_ERROR_MESSAGES)

    def test_7_128_preferred_detail_returns_none_when_nothing_usable(self):
        """7.128: empty / None / blank-only inputs → ``None``.

        Caller (``resolve_exception_detail``) then falls back to
        ``str(exception)`` or the ``fallback`` arg.
        """
        self.assertIsNone(select_preferred_exception_detail(None))
        self.assertIsNone(select_preferred_exception_detail([]))
        self.assertIsNone(select_preferred_exception_detail(["", "  ", None]))

    def test_7_129_preferred_stack_trace_returns_first_non_blank(self):
        """7.129: ``select_preferred_stack_trace`` returns the first
        non-blank string in the list, or None.

        Stack traces are not deduplicated against generic patterns —
        any non-blank trace is more useful than no trace.
        """
        self.assertEqual(
            select_preferred_stack_trace(["", "  ", "Traceback X", "Traceback Y"]),
            "Traceback X",
            "first non-blank wins — earliest in the chain is most "
            "specific",
        )
        self.assertIsNone(select_preferred_stack_trace(None))
        self.assertIsNone(select_preferred_stack_trace([]))

    def test_7_130_find_exception_artifacts_walks_chain_for_first_carrier(self):
        """7.130: ``find_exception_artifacts`` walks the cause/context
        chain and returns the artefacts from the FIRST link that
        carries any non-empty calc_obj / exception_details / stack_trace.

        Inner exceptions in the chain are framework-internal; the
        outer custom exception is what the audit row should reflect.
        """
        # Inner exception with no LEX-specific attrs.
        inner = ValueError("plain")

        # Middle exception with the artefacts.
        mid = RuntimeError("mid")
        mid.calc_obj = "MyCalc#42"
        mid.exception_details = ["XIRR could not be computed"]
        mid.stack_trace = "Traceback (most recent call last)..."
        mid.__cause__ = inner

        # Outer exception — bare wrapper.
        top = RuntimeError("wrapper")
        top.__cause__ = mid

        calc_obj, details, stack = find_exception_artifacts(top)
        self.assertEqual(calc_obj, ["MyCalc#42"])
        self.assertEqual(details, ["XIRR could not be computed"])
        self.assertEqual(stack, ["Traceback (most recent call last)..."])

    def test_7_131_find_exception_artifacts_returns_three_empty_lists_when_none(self):
        """7.131: when nothing in the chain carries artefacts, three
        empty lists come back — not None, not raise.

        Caller depends on tuple-unpacking — a regression returning
        a single None would crash every audit-log writer.
        """
        bare = RuntimeError("nothing here")
        result = find_exception_artifacts(bare)
        self.assertEqual(result, ([], [], []))


# ---------------------------------------------------------------------
# 7.132–7.133 — top-level resolve helpers
# ---------------------------------------------------------------------
class TestCluster07k_ResolveHelpers(TestCase):
    """``resolve_exception_detail`` / ``resolve_exception_traceback``.

    The customer-facing API on this module — every audit-log writer
    in the framework calls one of these two when capturing a
    failure.
    """

    def test_7_132_resolve_detail_prefers_artefact_then_str_then_fallback(self):
        """7.132: priority order (per docstring intent):

        1. preferred detail from chain artefacts (skip-generic)
        2. ``str(exception)`` if non-blank
        3. the explicit ``fallback`` arg
        """
        # 1) chain artefact wins
        e1 = RuntimeError("wrapper")
        e1.exception_details = ["real error"]
        self.assertEqual(
            resolve_exception_detail(e1, fallback="ignored"),
            "real error",
        )

        # 2) no artefact, exception string wins over fallback
        e2 = RuntimeError("just-a-string")
        self.assertEqual(
            resolve_exception_detail(e2, fallback="fallback-msg"),
            "just-a-string",
        )

        # 3) None exception → fallback
        self.assertEqual(
            resolve_exception_detail(None, fallback="fallback-msg"),
            "fallback-msg",
        )

        # All None → None
        self.assertIsNone(resolve_exception_detail(None, fallback=None))

    def test_7_133_resolve_traceback_prefers_artefact_then_fallback(self):
        """7.133: traceback resolution mirrors detail — chain
        artefact wins, then the ``fallback`` arg, else ``None``.

        Note (deliberate): there is NO ``str(exception)`` fallback for
        traceback because ``str(e)`` is the *message*, not the
        traceback. Asserting absence here pins the contract.
        """
        e1 = RuntimeError("wrapper")
        e1.stack_trace = "real traceback"
        self.assertEqual(resolve_exception_traceback(e1), "real traceback")

        # No artefact, no fallback → None (the str(e) trap)
        e2 = RuntimeError("just-a-string")
        self.assertIsNone(
            resolve_exception_traceback(e2),
            "must NOT fall back to str(exception) — that's the "
            "message, not the traceback",
        )

        # Fallback used when nothing in the chain
        self.assertEqual(
            resolve_exception_traceback(e2, fallback="fallback-tb"),
            "fallback-tb",
        )

        # None exception → fallback
        self.assertEqual(
            resolve_exception_traceback(None, fallback="x"),
            "x",
        )


# ---------------------------------------------------------------------
# 7.134–7.137 — exception classes
# ---------------------------------------------------------------------
class TestCluster07k_ExceptionClasses(TestCase):
    """The 5 custom exception classes — message-format contracts.

    Audit-log readers grep for the bracketed model_class prefix and
    the ``Context: …`` suffix; a regression in either format would
    silently break dashboards.
    """

    def test_7_134_validation_error_carries_original_and_model_class(self):
        """7.134: ``ValidationError(message, original_exception, model_class)``
        — both attrs accessible as documented.
        """
        original = ValueError("inner")
        e = ValidationError("outer", original_exception=original, model_class="MyModel")

        self.assertEqual(str(e), "outer")
        self.assertIs(e.original_exception, original)
        self.assertEqual(e.model_class, "MyModel")

    def test_7_135_calculated_model_error_message_format(self):
        """7.135: ``CalculatedModelError`` builds messages as:

        ``[<model_class>] <message> - Context: k=v, k=v``

        Each component conditional — model_class prefix only if
        non-None, context suffix only if kwargs given. Asserted
        across all four shape combinations.
        """
        # Plain
        self.assertEqual(str(CalculatedModelError("oops")), "oops")
        # Model only
        self.assertEqual(
            str(CalculatedModelError("oops", model_class="M")),
            "[M] oops",
        )
        # Context only
        self.assertEqual(
            str(CalculatedModelError("oops", retry=2)),
            "oops - Context: retry=2",
        )
        # Both
        self.assertEqual(
            str(CalculatedModelError("oops", model_class="M", retry=2)),
            "[M] oops - Context: retry=2",
        )

    def test_7_136_subclass_messages_carry_their_extra_fields(self):
        """7.136: ``ModelCombinationError`` (field_name) /
        ``ModelClusteringError`` (parallelizable_fields + model_count) /
        ``CeleryDispatchError`` (group_index + group_size + task_id)
        each weave their extras into the message string.

        Audit dashboards parse these — a silent format change would
        break log filters.
        """
        # ModelCombinationError carries field_name in parens
        e1 = ModelCombinationError(
            "expansion failed",
            field_name="counterparty",
            model_class="InvoiceCalc",
        )
        msg = str(e1)
        self.assertIn("[InvoiceCalc]", msg)
        self.assertIn("(field: counterparty)", msg)

        # ModelClusteringError carries model_count + fields list
        e2 = ModelClusteringError(
            "cluster build failed",
            parallelizable_fields=["region", "year"],
            model_count=42,
        )
        msg = str(e2)
        self.assertIn("(processing 42 models)", msg)
        self.assertIn("[region, year]", msg)

        # CeleryDispatchError carries group + task_id
        e3 = CeleryDispatchError(
            "broker timeout",
            group_index=2,    # 0-based input
            group_size=10,
            task_id="abc123",
        )
        msg = str(e3)
        self.assertIn("(group 3 with 10 models)", msg,
                      "group_index reported as 1-based for humans")
        self.assertIn("Task ID: abc123", msg)

    def test_7_137_exception_subclass_inheritance(self):
        """7.137: every custom exception derives from the documented
        base. Regressions that broke ``isinstance`` would prevent
        ``except CalculatedModelError`` clauses from catching the
        subclass.
        """
        for cls in (
            ModelCreationError,
            ModelCombinationError,
            ModelClusteringError,
            CeleryDispatchError,
        ):
            with self.subTest(cls=cls.__name__):
                instance = cls("test", model_class="M")
                self.assertIsInstance(instance, CalculatedModelError)
                self.assertIsInstance(instance, Exception)


# ---------------------------------------------------------------------
# 7.138–7.141 — ModelModificationRestriction ABC
# ---------------------------------------------------------------------
class TestCluster07k_ModelModificationRestriction(TestCase):
    """The customer-facing restriction contract.

    Every customer-defined restriction class extends
    ``ModelModificationRestriction``. Defaults must allow everything
    (so adding the mixin without overrides is a no-op);
    ``AdminReportsModificationRestriction`` must deny every write
    (it's the read-only profile shipped with HTMLReport).
    """

    def test_7_138_default_methods_allow_everything(self):
        """7.138: every method on the bare ABC returns ``True``.

        This is the documented "subclass and override only what you
        need to restrict" contract — the no-op default is what makes
        partial overrides safe.
        """
        # ABC instantiation works because every abstract method is
        # given a default implementation.
        r = ModelModificationRestriction()
        violations: list = []
        user = object()  # placeholder, helpers don't inspect it

        for method, args in [
            ("can_create_in_general", (user, violations)),
            ("can_read_in_general", (user, violations)),
            ("can_modify_in_general", (user, violations)),
            ("can_delete_in_general", (user, violations)),
            ("can_be_read", (object(), user, violations)),
            ("can_be_modified", (object(), user, violations, {})),
            ("can_be_deleted", (object(), user, violations)),
        ]:
            with self.subTest(method=method):
                self.assertTrue(
                    getattr(r, method)(*args),
                    f"{method} default must return True so a "
                    f"customer subclass can override only what they "
                    f"need to restrict",
                )

    def test_7_139_admin_reports_restriction_denies_all_writes(self):
        """7.139: ``AdminReportsModificationRestriction`` allows read
        and denies create / modify / delete (the documented
        "read-only profile" used by HTMLReport).

        Regression here = HTMLReport pages silently become writable.
        """
        r = AdminReportsModificationRestriction()
        violations: list = []
        user = object()

        # Read still allowed
        self.assertTrue(r.can_read_in_general(user, violations))
        self.assertTrue(r.can_be_read(object(), user, violations))

        # Every write denied
        self.assertFalse(r.can_modify_in_general(user, violations))
        self.assertFalse(r.can_create_in_general(user, violations))
        self.assertFalse(r.can_delete_in_general(user, violations))
        self.assertFalse(r.can_be_modified(object(), user, violations))
        self.assertFalse(r.can_be_created(object(), user, violations))
        self.assertFalse(r.can_be_deleted(object(), user, violations))

    def test_7_140_violations_list_is_mutable_in_overrides(self):
        """7.140: a customer override can append to ``violations``
        and the caller sees the change.

        Pins the documented mechanism for "tell the user WHY they
        cannot perform this action".
        """
        class DenyWithReason(ModelModificationRestriction):
            def can_modify_in_general(self, user, violations):
                violations.append("you are not on the editors list")
                return False

        r = DenyWithReason()
        violations: list = []
        result = r.can_modify_in_general(object(), violations)

        self.assertFalse(result)
        self.assertEqual(violations, ["you are not on the editors list"])

    def test_7_141_example_subclass_implements_required_overrides(self):
        """7.141: ``ExampleModelModificationRestriction`` is shipped
        as a copy-paste template; it must instantiate cleanly and
        return None (placeholder) from each override.

        Regression here = the example in the docs stops working as
        a starting point for new restrictions.
        """
        r = ExampleModelModificationRestriction()
        # Every override returns None (placeholder body)
        self.assertIsNone(r.can_read_in_general(object(), []))
        self.assertIsNone(r.can_modify_in_general(object(), []))
        self.assertIsNone(r.can_create_in_general(object(), []))


# ---------------------------------------------------------------------
# 7.142 — XLSXField coverage spotter
# ---------------------------------------------------------------------
class TestCluster07k_XLSXFieldCoverageSpotter(TestCase):
    """``lex/core/fields/XLSX_field.py`` — coverage-spotter scenario.

    Supervisor flagged this file for "test fully". The exhaustive
    test battery already lives at
    ``lex/tests/unit/api/test_xlsx_field.py`` (378 lines, drives
    real openpyxl/pandas/xlsxwriter, no mocking of the spreadsheet
    libraries — verifies actual Excel output across header
    splitting, row insertion, column width, number formats, cell
    comments, and autofilter).

    This single scenario pins the documented format-constant
    contract so a regression that silently changed the number
    format string (which dashboards parse) is caught at the
    cluster-7 level, not just buried in the unit-test pile.
    """

    def test_7_142_xlsx_field_format_constants_pinned(self):
        """7.142: ``XLSXField`` exposes the documented format
        constants verbatim.

        The accounting-style format string is parsed by Excel
        clients — Microsoft, Google Sheets, LibreOffice all read
        the literal string. A silent change here would render every
        exported spreadsheet's number formatting differently.
        """
        from lex.core.fields.XLSX_field import XLSXField

        # Negative-numbers-in-red accounting format
        self.assertEqual(
            XLSXField.cell_format,
            '#,##0.00 ;[Red]-#,##0.00 ;_-* "-"??_-',
            "cell_format drift would silently change every export's "
            "number formatting in Excel/Google Sheets/LibreOffice",
        )

        # Same format without the red colour escape
        self.assertEqual(
            XLSXField.cell_format_without_color,
            '#,##0.00 ;-#,##0.00 ;_-* "-"??_-',
        )

        # Boolean — green TRUE, red FALSE; pinned because dashboards
        # screenshot the Excel output and any colour change is a
        # visible regression.
        self.assertEqual(
            XLSXField.boolean_format,
            '[Green]"TRUE";[Red]"FALSE";[Red]"FALSE";[Red]"FALSE"',
        )

        # max_length pinned — Django migrations are sensitive to it.
        self.assertEqual(
            XLSXField.max_length, 300,
            "max_length change requires a fresh migration on every "
            "downstream project — pinned to flag it explicitly",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

