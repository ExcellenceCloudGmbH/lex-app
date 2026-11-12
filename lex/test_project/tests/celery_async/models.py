"""
Shared models for Cluster 8 — Celery & Async.

* :class:`CeleryCalc` — a :class:`CalculationModel` with a
  ``@lex_shared_task``-decorated ``calculate()``. When
  ``CELERY_ACTIVE=False`` we expect the framework to fall back to
  synchronous execution (the happy path for the vast majority of
  customers who never turn Celery on).
* :class:`CelerySyncCalc` — no task decorator; exists so we can
  exercise ``should_use_celery()`` returning False due to *absence*
  of a ``.delay`` attribute (independent of env vars).
"""

from __future__ import annotations

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 8")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 8")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class CelerySyncCalc(CalculationModel):
    """Plain calc — no task decorator. should_use_celery must return False."""

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"CelerySyncCalc({self.name!r}) failing on purpose")


@_permissive
class CeleryCalc(CalculationModel):
    """
    Celery-aware calc — ``calculate()`` is wrapped with
    ``@lex_shared_task`` if the framework exposes the decorator. If
    not importable at module-load time we fall back to a plain method
    and the Cluster-8 tests that require the decorator are skipped.
    """

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"CeleryCalc({self.name!r}) failing on purpose")


try:  # pragma: no cover — environment dependent
    from lex.lex_app.celery_tasks import lex_shared_task

    CeleryCalc.calculate = lex_shared_task()(CeleryCalc.calculate)
    CELERY_DECORATOR_AVAILABLE = True
except Exception:  # pragma: no cover
    CELERY_DECORATOR_AVAILABLE = False


ALL_MODELS = [CelerySyncCalc, CeleryCalc]

CELERY_SYNC = "celerysynccalc"
CELERY = "celerycalc"



# ---------------------------------------------------------------------
# Audit-column fixtures (sub-cluster 8c)
#
# The two Celery dispatch paths must be indistinguishable from an audit
# standpoint. Both bodies below are IDENTICAL: they write a field and save.
# The only difference is whether calculate() carries @lex_shared_task, which
# selects func.delay(...) over the generic calc_and_save task.
# ---------------------------------------------------------------------

from lex.core.models.LexModel import LexModel  # noqa: E402


@_permissive
class AuditUndecoratedCalc(CalculationModel):
    """Undecorated calculate() -> dispatched via the generic calc_and_save task."""

    name = models.CharField(max_length=200)
    result = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        self.result = (self.result or 0) + 1
        self.save()


@_permissive
class AuditDecoratedCalc(CalculationModel):
    """Identical body, but calculate() is @lex_shared_task -> func.delay path."""

    name = models.CharField(max_length=200)
    result = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        self.result = (self.result or 0) + 1
        self.save()


@_permissive
class AuditDecoratedFailingCalc(CalculationModel):
    """Decorated; saves itself then raises -> ERROR terminal path.

    Deliberately ``is_atomic = False``: under ``is_atomic = True`` the raise
    rolls the whole calculation back, which would undo any audit-column stamp
    and hide the defect. Non-atomic is the mode where a partial write (and
    therefore a stamped edited_at) actually survives the failure.
    """

    name = models.CharField(max_length=200)
    result = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        self.result = (self.result or 0) + 1
        self.save()
        raise RuntimeError(f"AuditDecoratedFailingCalc({self.name!r}) failing on purpose")


@_permissive
class AuditCeleryChild(LexModel):
    """Pre-existing plain row updated as calculation *output*."""

    name = models.CharField(max_length=200)
    payload = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class AuditDecoratedParentCalc(CalculationModel):
    """Decorated; updates a pre-existing child row during calculate()."""

    name = models.CharField(max_length=200)
    child_pk = models.IntegerField(null=True, blank=True)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.child_pk:
            child = AuditCeleryChild.objects.get(pk=self.child_pk)
            child.payload = (child.payload or 0) + 1
            child.save()


try:
    from lex.lex_app.celery_tasks import lex_shared_task

    # Explicit task names are REQUIRED here: Celery derives a task's default
    # name from ``module.func_name``, and all three ``calculate`` methods live
    # in this single fixture module. Without distinct names they register under
    # the same key and the last registration silently wins — one model would
    # then execute another's calculate() body. (Real projects keep each model
    # in its own module, so the collision does not arise there.)
    AuditDecoratedCalc.calculate = lex_shared_task(
        name="test_8c.audit_decorated_calc.calculate"
    )(AuditDecoratedCalc.calculate)
    AuditDecoratedFailingCalc.calculate = lex_shared_task(
        name="test_8c.audit_decorated_failing_calc.calculate"
    )(AuditDecoratedFailingCalc.calculate)
    AuditDecoratedParentCalc.calculate = lex_shared_task(
        name="test_8c.audit_decorated_parent_calc.calculate"
    )(AuditDecoratedParentCalc.calculate)
except Exception:  # pragma: no cover - decorator must exist in a real build
    pass

ALL_MODELS.extend([
    AuditUndecoratedCalc,
    AuditDecoratedCalc,
    AuditDecoratedFailingCalc,
    AuditCeleryChild,
    AuditDecoratedParentCalc,
])


# ---------------------------------------------------------------------
# create(*args) fixtures (sub-cluster 8e)
#
# ``CalculatedModelMixin.create(*args)`` passes its positional arguments to
# every generated row's ``calculate()``. These batches are grouped one task
# per region, so the Celery route sends three group messages with three
# different group indexes. Both ``calculate`` methods are @lex_shared_task:
# without ``.delay`` on ``calculate`` the Celery route is never taken.
# ---------------------------------------------------------------------

import json  # noqa: E402

from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin  # noqa: E402


@_permissive
class CreateArgsBatch(CalculatedModelMixin):
    """Writes the positional arguments its ``calculate()`` received onto the row.

    ``calls`` logs every call as ``(region, args)``, including calls whose row
    is not saved afterwards. ``fail_once_for`` lists regions whose next call
    raises once, the way a transient error in a worker would.
    """

    region = models.CharField(max_length=16)
    received_args = models.TextField(blank=True, default="")

    defining_fields = ["region"]
    parallelizable_fields = ["region"]

    _region_keys = ["US", "EU", "APAC"]
    calls: list = []
    fail_once_for: set = set()

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.region

    def get_selected_key_list(self, key):
        if key == "region":
            return list(type(self)._region_keys)
        return []

    def calculate(self, *args):
        type(self).calls.append((self.region, args))
        if self.region in type(self).fail_once_for:
            type(self).fail_once_for.discard(self.region)
            raise RuntimeError(
                f"CreateArgsBatch({self.region!r}) failing once on purpose"
            )
        self.received_args = json.dumps(list(args))


@_permissive
class NoArgBatch(CalculatedModelMixin):
    """A ``calculate()`` that takes no arguments, like most customer batches."""

    region = models.CharField(max_length=16)
    calculated = models.BooleanField(default=False)

    defining_fields = ["region"]
    parallelizable_fields = ["region"]

    _region_keys = ["US", "EU", "APAC"]

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.region

    def get_selected_key_list(self, key):
        if key == "region":
            return list(type(self)._region_keys)
        return []

    def calculate(self):
        self.calculated = True


try:
    from lex.lex_app.celery_tasks import lex_shared_task

    # Explicit names for the same reason as the 8c fixtures above: every
    # ``calculate`` in this module would otherwise register as
    # ``...models.calculate`` and the last one would win.
    CreateArgsBatch.calculate = lex_shared_task(
        name="test_8e.create_args_batch.calculate"
    )(CreateArgsBatch.calculate)
    NoArgBatch.calculate = lex_shared_task(
        name="test_8e.no_arg_batch.calculate"
    )(NoArgBatch.calculate)
except Exception:  # pragma: no cover - decorator must exist in a real build
    pass

# Kept out of ALL_MODELS: only 8e's classes need these tables.
CREATE_ARGS_MODELS = [CelerySyncCalc, CreateArgsBatch, NoArgBatch]
