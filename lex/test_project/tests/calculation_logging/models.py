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
    child_mode = models.CharField(max_length=20, default="log_only")
    units_csv = models.CharField(max_length=200, default="u1")
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

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


ALL_MODELS = [
    LogRootCalc,
    LogLoudChild,
    LogSilentChild,
    LogMiddleCombinatoric,
    LogGrandchildCalc,
    LogConditionalChild,
]
