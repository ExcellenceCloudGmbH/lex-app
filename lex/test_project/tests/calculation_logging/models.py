"""
Models for Cluster 15 — Calculation Logging (LexLogger).

Per Rule #3 of the test plan: per-cluster models, never reused.
All six models are minimal — the cluster is about the *logging
topology*, not field shapes.
"""

from __future__ import annotations

from django.db import models

from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    """Same pattern as Cluster 6 — open up permissions for tests."""
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 15")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 15")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


def _parse_units(csv: str) -> list[str]:
    return [u.strip() for u in (csv or "").split(",") if u.strip()]


@_permissive
class LogRootCalc(CalculationModel):
    """
    The root of the calculation tree. `calculate()` dispatches based
    on `child_mode`. `units_csv` provides the unit list passed to
    child `.create()` calls (defining_fields combinatorial input).
    """

    name = models.CharField(max_length=200)
    child_mode = models.CharField(max_length=40, default="log_only")
    units_csv = models.CharField(max_length=200, default="u1")
    calculation_error_message = models.TextField(blank=True, default="")
    # What the run's live log held when a mode below read it, as the log
    # panel's backfill would (15.65, 15.76, 15.77). A field rather than an
    # attribute: on the Celery route calculate() runs on the task's copy of
    # the root, and only what is saved comes back.
    live_log_after_inner = models.TextField(null=True, blank=True)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def _read_live_log(self):
        """The run's live log, read the way the log panel's backfill does
        (InitCalculationLogs), as someone opening the panel now would."""
        from lex.api.utils import operation_context
        from lex.audit_logging.utils.CacheManager import CacheManager

        return CacheManager.get_message(
            CacheManager.build_cache_key(
                f"{self._meta.model_name}_{self.pk}",
                operation_context.get()["calculation_id"],
            )
        )

    def _save_failing_nested_then_read_live_log(self, failing_model):
        """Save a nested CalculationModel that fails, handle the failure, read
        the run's live log, and carry on logging (15.76, 15.77)."""
        from lex.audit_logging.handlers.LexLogger import LexLogger
        from lex.core.models.CalculationModel import CalculationModelException

        try:
            failing_model(
                name=self.units_csv,
                is_calculated=failing_model.IN_PROGRESS,
            ).save()
        except CalculationModelException:
            pass
        self.live_log_after_inner = self._read_live_log()
        LexLogger().add_text("root recovered").log()

    def calculate(self):
        # Local imports — these are only used inside calculate(), and
        # importing at module top-level would create a circular import
        # via the LexLogger -> CalculationLog -> ... chain when this
        # models module is imported during table creation.
        from lex.audit_logging.handlers.LexLogger import LexLogger

        units = _parse_units(self.units_csv)
        LexLogger().add_text(f"root {self.name}").log()

        if self.child_mode == "log_only":
            return
        if self.child_mode == "loud":
            LogLoudChild.create(unit=units)
            return
        if self.child_mode == "silent":
            LogSilentChild.create(unit=units)
            return
        if self.child_mode == "mixed":
            LogLoudChild.create(unit=units)
            LogSilentChild.create(unit=units)
            return
        if self.child_mode == "three_level":
            LogMiddleCombinatoric.create(unit=units)
            return
        if self.child_mode == "conditional":
            LogConditionalChild.create(unit=units)
            return
        if self.child_mode == "calcmodel_chain":
            # Direct CalculationModel.save() inside root.calculate() — 2 levels.
            inner = LogInnerSavedCalc(
                name=self.units_csv,
                is_calculated=LogInnerSavedCalc.IN_PROGRESS,
            )
            inner.save()
            return
        if self.child_mode == "calcmodel_chain_3":
            # Three-level CalcModel chain via direct .save() at each hop.
            middle = LogMiddleSavedCalc(
                name=self.units_csv,
                is_calculated=LogMiddleSavedCalc.IN_PROGRESS,
            )
            middle.save()
            return
        if self.child_mode == "fanout_then_calcmodel":
            # Mixin fan-out, then inner CalcModel.save() inside each child.
            LogChildWithInnerSave.create(unit=units)
            return
        if self.child_mode == "fail_after_log":
            # Logs a step that succeeds, then fails — inside the atomic
            # transaction, so the rollback takes both log rows with it.
            LexLogger().add_text("step one done").log()
            raise ValueError("step two failed")
        if self.child_mode == "fail_child_then_log":
            # The batch stops at the failing child (fail-fast); the root
            # handles that and carries on logging (cluster 15m, 15.62).
            from lex.core.exceptions import CalculatedModelError

            try:
                LogFailingChild.create(unit=units)
            except CalculatedModelError:
                LexLogger().add_text("root recovered").log()
            return
        if self.child_mode == "quiet_three_level":
            LogQuietMiddle.create(unit=units)
            return
        if self.child_mode == "celery_loud":
            LogCeleryLoudChild.create(unit=units)
            return
        if self.child_mode == "celery_quiet_three_level":
            LogCeleryQuietMiddle.create(unit=units)
            return
        if self.child_mode == "calcmodel_then_read_live_log":
            # Once the nested CalculationModel has finished, read this run's
            # live log (15.65).
            LogInnerSavedCalc(
                name=self.units_csv,
                is_calculated=LogInnerSavedCalc.IN_PROGRESS,
            ).save()
            self.live_log_after_inner = self._read_live_log()
            return
        if self.child_mode == "celery_calcmodel_chain":
            # A nested CalculationModel whose calculate() is a
            # @lex_shared_task: dispatched as itself on the Celery route (15.72).
            LogCeleryInnerSavedCalc(
                name=self.units_csv,
                is_calculated=LogCeleryInnerSavedCalc.IN_PROGRESS,
            ).save()
            return
        if self.child_mode == "celery_calcmodel_chain_3":
            # A @lex_shared_task middle that saves a nested CalculationModel
            # of its own while it runs on the worker (15.73).
            LogCeleryMiddleSavedCalc(
                name=self.units_csv,
                is_calculated=LogCeleryMiddleSavedCalc.IN_PROGRESS,
            ).save()
            return
        if self.child_mode == "failing_caught_read_live_log":
            self._save_failing_nested_then_read_live_log(LogFailingInnerSavedCalc)
            return
        if self.child_mode == "celery_failing_caught_read_live_log":
            self._save_failing_nested_then_read_live_log(LogCeleryFailingInnerSavedCalc)
            return
        if self.child_mode == "failing_calcmodel":
            # The nested CalculationModel's failure fails the root (15.66).
            LogFailingInnerSavedCalc(
                name=self.units_csv,
                is_calculated=LogFailingInnerSavedCalc.IN_PROGRESS,
            ).save()
            return
        if self.child_mode == "failing_calcmodel_caught":
            # The root handles the nested CalculationModel's failure and
            # carries on (15.67).
            from lex.core.models.CalculationModel import CalculationModelException

            try:
                LogFailingInnerSavedCalc(
                    name=self.units_csv,
                    is_calculated=LogFailingInnerSavedCalc.IN_PROGRESS,
                ).save()
            except CalculationModelException:
                LexLogger().add_text("root recovered").log()
            return


@_permissive
class LogLoudChild(CalculatedModelMixin):
    """Combinatorial child that always emits a LexLogger call."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"loud:{self.unit}"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers (e.g. .create(unit=[...]))
        # supply unit values as overrides that bypass this method.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"loud {self.unit}").log()


@_permissive
class LogSilentChild(CalculatedModelMixin):
    """Combinatorial child that NEVER emits a LexLogger call.
    Mirrors PE_LTIP_*, RE, VAH, APEP_* PFOs in production.
    """

    unit = models.CharField(max_length=50)
    touched = models.BooleanField(default=False)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"silent:{self.unit}"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers (e.g. .create(unit=[...]))
        # supply unit values as overrides that bypass this method.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        # Deliberate: trivial state update, NO LexLogger call.
        self.touched = True


@_permissive
class LogGrandchildCalc(CalculatedModelMixin):
    """Deepest layer of the 3-level chain — always logs."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers (e.g. .create(unit=[...]))
        # supply unit values as overrides that bypass this method.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"grandchild {self.unit}").log()


@_permissive
class LogMiddleCombinatoric(CalculatedModelMixin):
    """Middle layer of the 3-level chain — logs AND triggers
    LogGrandchildCalc.create() inside its own calculate().
    """

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers (e.g. .create(unit=[...]))
        # supply unit values as overrides that bypass this method.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"middle {self.unit}").log()
        LogGrandchildCalc.create(unit=[self.unit])


@_permissive
class LogConditionalChild(CalculatedModelMixin):
    """Logs only when unit == 'loud_one'. Mirrors the production case
    where some instances in the same combinatorial fan-out log and
    others don't (scenario 15.18).
    """

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers (e.g. .create(unit=[...]))
        # supply unit values as overrides that bypass this method.
        if key == "unit":
            return ["loud_one"]
        return []

    def calculate(self):
        if self.unit == "loud_one":
            from lex.audit_logging.handlers.LexLogger import LexLogger
            LexLogger().add_text(f"conditional {self.unit}").log()


@_permissive
class LogInnerSavedCalc(CalculationModel):
    """CalculationModel whose calculate() emits a LexLogger line.
    Used to test hierarchy preservation across the .save() boundary
    when called from inside another calc's calculate()."""
    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"inner {self.name}").log()


@_permissive
class LogMiddleSavedCalc(CalculationModel):
    """Middle layer of the 3-level CalculationModel chain — logs
    AND saves a LogInnerSavedCalc inside calculate()."""
    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"middle {self.name}").log()
        inner = LogInnerSavedCalc(
            name=f"inner-from-{self.name}",
            is_calculated=LogInnerSavedCalc.IN_PROGRESS,
        )
        inner.save()


@_permissive
class LogChildWithInnerSave(CalculatedModelMixin):
    """Combinatorial child that logs AND saves an inner
    LogInnerSavedCalc from inside its calculate(). Tests the
    mixed Mixin → CalculationModel.save() boundary."""
    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — combinatorial callers override.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger
        LexLogger().add_text(f"child {self.unit}").log()
        inner = LogInnerSavedCalc(
            name=f"from-{self.unit}",
            is_calculated=LogInnerSavedCalc.IN_PROGRESS,
        )
        inner.save()


ALL_MODELS = [
    LogRootCalc,
    LogLoudChild,
    LogSilentChild,
    LogMiddleCombinatoric,
    LogGrandchildCalc,
    LogConditionalChild,
    LogInnerSavedCalc,
    LogMiddleSavedCalc,
    LogChildWithInnerSave,
]


@_permissive
class LogOnCreateCalc(CalculationModel):
    """Calculates when created through the app (``calculate_on_create``) and
    logs one line, so its run leaves a log the grid can open (cluster 15k).

    Kept out of ALL_MODELS: only 15k creates it through the API."""

    name = models.CharField(max_length=200)

    calculate_on_create = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"created {self.name}").log()


# ── Cluster 15m / 15n: a generated row's log is filed under that row ──
# Kept out of ALL_MODELS: only 15m and 15n create them.


@_permissive
class LogFailingChild(CalculatedModelMixin):
    """Combinatorial child that logs, then fails when ``unit == "bad"``.

    The failure stops the batch; the lines it wrote before failing must
    still be filed under it (15.62).
    """

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — the root passes the units as overrides.
        if key == "unit":
            return ["ok"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"before failure {self.unit}").log()
        if self.unit == "bad":
            raise ValueError(f"LogFailingChild({self.unit}) failing on purpose")


@_permissive
class LogQuietMiddle(CalculatedModelMixin):
    """Middle layer that creates its grandchildren first and logs only
    after them, so the grandchild is the first to need the middle's row
    (15.60, 15.61)."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — the root passes the units as overrides.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LogGrandchildCalc.create(unit=[self.unit])
        LexLogger().add_text(f"middle after {self.unit}").log()


GENERATED_ROW_MODELS = [LogFailingChild, LogQuietMiddle]


# ── Cluster 15o: a nested CalculationModel's synchronous run ──
# Kept out of ALL_MODELS: only 15o creates it.


@_permissive
class LogFailingInnerSavedCalc(CalculationModel):
    """Like LogInnerSavedCalc, but its calculate() fails after logging, inside
    its own transaction (15.66, 15.67)."""

    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"failing inner {self.name}").log()
        raise ValueError(f"LogFailingInnerSavedCalc({self.name}) failing on purpose")


NESTED_RUN_MODELS = [LogFailingInnerSavedCalc]


@_permissive
class LogCeleryLoudChild(CalculatedModelMixin):
    """Like LogLoudChild, but ``calculate()`` is a ``@lex_shared_task``, so
    ``create()`` takes the Celery route when Celery is active (15.63)."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — the root passes the units as overrides.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery loud {self.unit}").log()


@_permissive
class LogCeleryGrandchild(CalculatedModelMixin):
    """Deepest layer of the Celery three-level chain — always logs (15.64)."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — the middle passes the unit as an override.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery grandchild {self.unit}").log()


@_permissive
class LogCeleryQuietMiddle(CalculatedModelMixin):
    """Celery middle layer: dispatches its grandchildren before it has
    logged anything, and logs only after them (15.64). It is unsaved while
    its calculate() runs, so the stack shipped to the grandchildren's tasks
    carries a model with no primary key."""

    unit = models.CharField(max_length=50)

    defining_fields = ["unit"]
    parallelizable_fields: list[str] = []

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        # Fallback only — the root passes the units as overrides.
        if key == "unit":
            return ["u1"]
        return []

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LogCeleryGrandchild.create(unit=[self.unit])
        LexLogger().add_text(f"celery middle after {self.unit}").log()


from lex.lex_app.celery_tasks import lex_shared_task  # noqa: E402

# Explicit task names: Celery names a task after ``module.function`` and all
# three ``calculate`` methods live in this module, so without them the last
# registration would run every model's calculate() (see celery_async/models.py).
LogCeleryLoudChild.calculate = lex_shared_task(
    name="test_15n.celery_loud_child.calculate"
)(LogCeleryLoudChild.calculate)
LogCeleryGrandchild.calculate = lex_shared_task(
    name="test_15n.celery_grandchild.calculate"
)(LogCeleryGrandchild.calculate)
LogCeleryQuietMiddle.calculate = lex_shared_task(
    name="test_15n.celery_quiet_middle.calculate"
)(LogCeleryQuietMiddle.calculate)

CELERY_ROUTE_MODELS = [LogCeleryLoudChild, LogCeleryGrandchild, LogCeleryQuietMiddle]


# ── Cluster 15p: a CalculationModel whose calculate() is a @lex_shared_task ──
# Kept out of ALL_MODELS: only 15p creates them. With Celery active,
# calculate_hook dispatches each of them as its own decorated task, not
# through the generic calc_and_save.


@_permissive
class LogCeleryInnerSavedCalc(CalculationModel):
    """Like LogInnerSavedCalc, with a @lex_shared_task calculate() (15.72)."""

    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery inner {self.name}").log()


@_permissive
class LogCeleryFailingInnerSavedCalc(CalculationModel):
    """Like LogFailingInnerSavedCalc, with a @lex_shared_task calculate()
    that logs and then fails (15.77)."""

    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery failing inner {self.name}").log()
        raise ValueError(f"LogCeleryFailingInnerSavedCalc({self.name}) failing on purpose")


@_permissive
class LogCeleryMiddleSavedCalc(CalculationModel):
    """Like LogMiddleSavedCalc, with a @lex_shared_task calculate(): logs and
    saves a LogInnerSavedCalc while it runs on the worker (15.73)."""

    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery middle {self.name}").log()
        LogInnerSavedCalc(
            name=f"inner-from-{self.name}",
            is_calculated=LogInnerSavedCalc.IN_PROGRESS,
        ).save()


@_permissive
class LogCeleryRootCalc(CalculationModel):
    """A root with a @lex_shared_task calculate() that logs one line (15.74)."""

    name = models.CharField(max_length=100)
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):  # pragma: no cover
        return self.name

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(f"celery root {self.name}").log()


LogCeleryInnerSavedCalc.calculate = lex_shared_task(
    name="test_15p.celery_inner_saved_calc.calculate"
)(LogCeleryInnerSavedCalc.calculate)
LogCeleryFailingInnerSavedCalc.calculate = lex_shared_task(
    name="test_15p.celery_failing_inner_saved_calc.calculate"
)(LogCeleryFailingInnerSavedCalc.calculate)
LogCeleryMiddleSavedCalc.calculate = lex_shared_task(
    name="test_15p.celery_middle_saved_calc.calculate"
)(LogCeleryMiddleSavedCalc.calculate)
LogCeleryRootCalc.calculate = lex_shared_task(
    name="test_15p.celery_root_calc.calculate"
)(LogCeleryRootCalc.calculate)

DECORATED_RUN_MODELS = [
    LogCeleryInnerSavedCalc,
    LogCeleryFailingInnerSavedCalc,
    LogCeleryMiddleSavedCalc,
    LogCeleryRootCalc,
]
