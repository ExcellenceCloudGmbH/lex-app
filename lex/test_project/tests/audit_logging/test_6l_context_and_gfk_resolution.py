"""
Sub-cluster 6l — ModelContext stack + GFK resolver + ContextResolver.

PR-7 audit-utils tier, second batch. Three small pure-Python helpers in
`lex/audit_logging/utils/` that no other test currently pins at the unit
level and that the calculation-logging pipeline depends on for every
write:

* ``lex/audit_logging/utils/ModelContext.py`` — `ModelContext` LIFO stack
  + the `model_logging_context(instance)` ContextVar-backed CM.
  Every `LexLogger.add_*()` call inside a `CalculatedModel.calculate()`
  body resolves the "which record am I logging against?" question
  through this stack. A regression that swapped LIFO for FIFO would
  silently attribute child logs to the parent (and vice-versa) — the
  per-record Calculation-Log Tab UI would render every nested calc's
  log lines under the wrong row.

* ``lex/audit_logging/utils/content_types.py`` — `safe_get_generic_related_object`
  is the customer-side resolver for any GFK-pointing audit row
  (`AuditLog.calculatable_object`, `CalculationLog.calculatable_object`).
  Silent failure here = the per-record Audit-Log Tab can't dereference
  back to the source row, so compliance teams lose the link from log →
  data. The contract is "return None on any non-fatal failure, never
  raise" — pinning that guarantee here.

* ``lex/audit_logging/utils/ContextResolver.py`` — `ContextResolver.resolve()`
  fuses the `operation_context` ContextVar + the `_model_context` stack
  into a single `ContextInfo` payload that downstream writers consume.
  The two failure modes (missing calculation_id / unresolvable AuditLog)
  must surface as `ContextResolutionError` — not generic exceptions —
  because operator dashboards filter on that class for triage.

All scenarios are pure-Python — no DB, no Keycloak, no Celery, no
fixtures — so the entire batch runs as `SimpleTestCase`.

Scenario IDs 6.109 – 6.121 (6.96–6.108 taken by 6k declarative model
contracts; 6.96–6.105 also used by 6h cache_manager — pre-existing
overlap, not introduced here).

Run with:
    lex test lex.test_project.tests.audit_logging.test_6l_context_and_gfk_resolution \\
        --verbosity=2 --noinput --keepdb
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from lex.audit_logging.utils.ModelContext import (
    ModelContext,
    _model_context,
    model_logging_context,
)
from lex.audit_logging.utils import content_types as ct_module
from lex.audit_logging.utils.content_types import safe_get_generic_related_object
from lex.audit_logging.utils.ContextResolver import ContextResolver
from lex.audit_logging.utils.DataModels import ContextInfo, ContextResolutionError


# ---------------------------------------------------------------------------
# Helpers — synthetic Django-like model instance (no DB, no app registration)
# ---------------------------------------------------------------------------


def _fake_instance(model_name="fakemodel", pk=1):
    """Build a stand-in for a Django model instance with the minimum
    surface ModelContext + ContextResolver touch — `_meta.model_name` and
    `pk`. Avoids dragging in test_project models for a pure-Python batch."""
    meta = SimpleNamespace(model_name=model_name, label_lower=f"app.{model_name}")
    return SimpleNamespace(_meta=meta, pk=pk)


# ---------------------------------------------------------------------------
# 1) ModelContext LIFO stack semantics
# ---------------------------------------------------------------------------


class TestCluster06l_ModelContextStack(SimpleTestCase):
    """Direct stack semantics — push / pop / current / parent / get_root.

    These four properties are the public API the rest of the audit
    pipeline reads. A regression here would silently attribute logs to
    the wrong record."""

    def test_6_109_fresh_stack_has_no_current_parent_or_root(self):
        ctx = ModelContext()
        self.assertIsNone(
            ctx.current,
            "Fresh stack must report current=None — anything else means we'd "
            "attribute the first log line to a stale instance from a previous request.",
        )
        self.assertIsNone(ctx.parent, "Fresh stack must report parent=None.")
        self.assertIsNone(
            ctx.get_root(),
            "Fresh stack must report get_root()=None — operator dashboards key "
            "on root_record for trace-back, a non-None default would mis-group every log.",
        )

    def test_6_110_push_then_current_is_lifo_top_and_parent_is_below(self):
        ctx = ModelContext()
        a = _fake_instance("a")
        b = _fake_instance("b")
        c = _fake_instance("c")
        ctx.push(a)
        ctx.push(b)
        ctx.push(c)
        self.assertIs(
            ctx.current, c,
            "current must be the most recently pushed instance (LIFO top) — "
            "swapping to FIFO would log every child against its grandparent.",
        )
        self.assertIs(
            ctx.parent, b,
            "parent must be the second-from-top — the per-record Audit Tab "
            "uses parent_record to render the 'logged from inside <parent>' breadcrumb.",
        )
        self.assertIs(
            ctx.get_root(), a,
            "get_root() must always return the BOTTOM (first-pushed) instance, "
            "never the current — that's the entry-point calc the trace links back to.",
        )

    def test_6_111_pop_returns_top_and_empty_pop_returns_none(self):
        ctx = ModelContext()
        a = _fake_instance("a")
        ctx.push(a)
        self.assertIs(ctx.pop(), a, "pop() returns the popped instance, not None.")
        self.assertIsNone(ctx.current, "After popping the only item, current=None.")
        # Empty pop must NOT raise — the CM's finally clause depends on this
        # being safe even when the body errored before push completed.
        self.assertIsNone(
            ctx.pop(),
            "pop() on an empty stack returns None silently — raising would mask "
            "the original exception in the CM's finally block.",
        )

    def test_6_112_repr_carries_current_parent_and_depth(self):
        ctx = ModelContext()
        ctx.push(_fake_instance("alpha"))
        ctx.push(_fake_instance("beta"))
        rendered = repr(ctx)
        # Operator forensics greps this; the four-token prefix + literal
        # "depth=" are the contract.
        self.assertIn("ModelContextStack", rendered)
        self.assertIn("current=", rendered)
        self.assertIn("parent=", rendered)
        self.assertIn(
            "depth=2", rendered,
            "depth must reflect actual stack length — a hard-coded 0/1 would "
            "make recursion-depth alerting useless.",
        )


# ---------------------------------------------------------------------------
# 2) model_logging_context CM — push/pop wrapping + nesting + type guard
# ---------------------------------------------------------------------------


class TestCluster06l_ModelLoggingContext(SimpleTestCase):
    """The `model_logging_context(instance)` CM is the actual user-facing
    seam — `with model_logging_context(self):` inside a calculate()
    body. Tests pin push-on-enter / pop-on-exit / nesting / type guard.
    """

    def _current(self):
        return _model_context.get()["model_context"].current

    def test_6_113_enter_pushes_and_exit_pops(self):
        instance = _fake_instance("widget", pk=42)
        before = self._current()
        with model_logging_context(instance):
            self.assertIs(
                self._current(), instance,
                "Inside the CM, current must be the just-pushed instance.",
            )
        self.assertIs(
            self._current(), before,
            "After the CM exits, current must be restored to its prior value — "
            "a leaked push would attribute the next request's log lines to a "
            "completed calculation.",
        )

    def test_6_114_nested_contexts_preserve_outer_after_inner_exits(self):
        outer = _fake_instance("outer", pk=1)
        inner = _fake_instance("inner", pk=2)
        with model_logging_context(outer):
            self.assertIs(self._current(), outer)
            with model_logging_context(inner):
                self.assertIs(
                    self._current(), inner,
                    "Inner context must shadow outer.",
                )
            self.assertIs(
                self._current(), outer,
                "After inner exits, current must restore to outer — losing the "
                "outer would silently log subsequent lines against the wrong row.",
            )

    def test_6_115_non_django_instance_raises_type_error(self):
        # The CM's type-guard prevents a developer from accidentally pushing
        # a dict/string and getting an opaque AttributeError later. Anything
        # without `_meta` is rejected at the boundary.
        with self.assertRaises(TypeError) as cm:
            with model_logging_context({"not": "a model"}):
                pass  # pragma: no cover
        self.assertIn(
            "Expected Django model instance",
            str(cm.exception),
            "TypeError message must name the contract — bare 'has no attribute' "
            "would not point the developer at the fix.",
        )


# ---------------------------------------------------------------------------
# 3) safe_get_generic_related_object — GFK resolver "never raises" contract
# ---------------------------------------------------------------------------


class TestCluster06l_SafeGetGenericRelatedObject(SimpleTestCase):
    """`safe_get_generic_related_object(audit_row)` is the customer-side
    GFK resolver. Contract from the docstring: returns None on any
    non-fatal failure, never raises. The per-record Audit-Log Tab calls
    this on every audit row to render the back-link to the source data."""

    def test_6_116_returns_none_when_content_type_id_missing(self):
        instance = SimpleNamespace(content_type_id=None, object_id=7, _state=SimpleNamespace(db=None))
        self.assertIsNone(
            safe_get_generic_related_object(instance),
            "Missing content_type_id is the 'never-set' state — must return "
            "None, not raise, so the UI can render '(unknown)' instead of 500ing.",
        )

    def test_6_117_returns_none_when_object_id_missing(self):
        instance = SimpleNamespace(content_type_id=5, object_id=None, _state=SimpleNamespace(db=None))
        self.assertIsNone(
            safe_get_generic_related_object(instance),
            "Missing object_id is also the 'never-set' state.",
        )

    def test_6_118_returns_resolved_object_via_default_manager(self):
        target = SimpleNamespace(pk=42, name="resolved")
        # Build the chain: safe_get_content_type -> ContentType -> model_class
        # -> _default_manager -> filter().first()
        fake_qs = mock.MagicMock()
        fake_qs.first.return_value = target
        fake_manager = mock.MagicMock()
        fake_manager.filter.return_value = fake_qs
        fake_model_class = mock.MagicMock()
        fake_model_class._default_manager = fake_manager
        fake_ct = mock.MagicMock()
        fake_ct.model_class.return_value = fake_model_class

        with mock.patch.object(ct_module, "safe_get_content_type", return_value=fake_ct):
            instance = SimpleNamespace(
                content_type_id=5, object_id=42, _state=SimpleNamespace(db=None)
            )
            self.assertIs(
                safe_get_generic_related_object(instance), target,
                "Happy path must dereference via _default_manager.filter(pk=).first() "
                "— a regression dropping .first() would return a QuerySet and the UI "
                "would render '<QuerySet [...]>' instead of the row.",
            )
        fake_manager.filter.assert_called_once_with(pk=42)

    def test_6_119_returns_none_when_model_class_unresolvable(self):
        # model_class() returning None happens when the source app was
        # uninstalled but stale audit rows still reference it. UI must show
        # '(unknown)', not 500.
        fake_ct = mock.MagicMock()
        fake_ct.model_class.return_value = None
        with mock.patch.object(ct_module, "safe_get_content_type", return_value=fake_ct):
            instance = SimpleNamespace(
                content_type_id=5, object_id=42, _state=SimpleNamespace(db=None)
            )
            self.assertIsNone(safe_get_generic_related_object(instance))

    def test_6_120_swallows_resolver_exception_and_returns_none(self):
        # Any DB error / cache error / migration mid-flight must be
        # swallowed — the audit log read path cannot 500 the audit tab.
        with mock.patch.object(
            ct_module, "safe_get_content_type", side_effect=RuntimeError("DB down")
        ):
            instance = SimpleNamespace(
                content_type_id=5, object_id=42, _state=SimpleNamespace(db=None)
            )
            self.assertIsNone(
                safe_get_generic_related_object(instance),
                "Resolver MUST swallow exceptions — the audit-tab UI cannot 500.",
            )


# ---------------------------------------------------------------------------
# 4) ContextResolver — operation+model context fusion
# ---------------------------------------------------------------------------


class TestCluster06l_ContextResolver(SimpleTestCase):
    """`ContextResolver.resolve()` reads operation_context (calc_id +
    audit_log shortcut) and the _model_context stack, looks up the
    AuditLog row, and returns a unified `ContextInfo`. Failure must
    surface as `ContextResolutionError` — never a generic exception —
    because operator dashboards filter alerts on that class."""

    def test_6_121_missing_calculation_id_raises_context_resolution_error(self):
        # operation_context default is {}; resolver must reject with the
        # named exception, not a KeyError or AttributeError.
        with mock.patch(
            "lex.audit_logging.utils.ContextResolver.operation_context"
        ) as op_ctx:
            op_ctx.get.return_value = {}  # no calculation_id key
            with self.assertRaises(ContextResolutionError) as cm:
                ContextResolver.resolve()
        self.assertIn(
            "calculation_id",
            str(cm.exception).lower(),
            "Error message must name calculation_id so the operator can wire "
            "it on whatever path emitted the log without a calc.",
        )

    def test_6_122_happy_path_returns_unified_context_info(self):
        fake_audit = SimpleNamespace(calculation_id="calc-42", save=mock.MagicMock())
        current = _fake_instance("current_model", pk=10)
        # Pre-load the model context stack with current model
        ctx = ModelContext([current])
        with mock.patch(
            "lex.audit_logging.utils.ContextResolver.operation_context"
        ) as op_ctx, mock.patch(
            "lex.audit_logging.utils.ContextResolver._model_context"
        ) as mc, mock.patch(
            "lex.audit_logging.utils.ContextResolver._safe_get_content_type",
            return_value=SimpleNamespace(id=99),
        ):
            op_ctx.get.return_value = {
                "calculation_id": "calc-42",
                "audit_log_temp": fake_audit,  # short-circuit AuditLog.objects.get
            }
            mc.get.return_value = {"model_context": ctx}
            info = ContextResolver.resolve()

        self.assertIsInstance(info, ContextInfo)
        self.assertEqual(info.calculation_id, "calc-42")
        self.assertIs(
            info.audit_log, fake_audit,
            "audit_log_temp shortcut bypasses the DB lookup — required for "
            "the 'audit row created earlier in same request' path.",
        )
        self.assertIs(info.current_model, current)
        self.assertIsNone(
            info.parent_model,
            "Single-element stack: current is set, parent is None.",
        )
        self.assertEqual(
            info.current_record, "current_model_10",
            "current_record format is '<model_name>_<pk>' — the per-record "
            "Calculation-Log Tab parses this to render the back-link.",
        )
        self.assertEqual(info.root_record, "current_model_10")

