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
        if self.child_mode == "kitchen_sink":
            # Cluster 15.2 — exercises every LexLogger builder method
            # in a single chained call, so the on-disk markdown shape
            # of each method is asserted end-to-end.
            import pandas as pd
            (
                LexLogger()
                .add_heading("KS-Heading", 2)
                .add_text("ks-text")
                .add_list(["alpha", "beta"], ordered=True)
                .add_quote("ks-quote")
                .add_code("x = 1", "python")
                .add_link("ks-link", "https://example.test/ks")
                .add_image("ks-alt", "https://example.test/img.png")
                .add_horizontal_rule()
                .add_table(["h1", "h2"], [["v1", "v2"]])
                .add_dataframe(pd.DataFrame({"col": [7]}))
                .add_raw_markdown("**ks-bold**")
                .log()
            )
            return
        if self.child_mode == "double_log":
            # Cluster 15.3 / 15.4 — two .log() calls inside the same
            # model context. _get_or_create_locked must dedup the row
            # and append the second message, AND LexLogger.log() must
            # reset its own content buffer between calls so the second
            # call doesn't re-emit the first message.
            LexLogger().add_text("first-line").log()
            LexLogger().add_text("second-line").log()
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
        if key == "unit":
            # Two units so that 15.15 can assert "some log, some don't"
            # in the same combinatoric fan-out.
            return ["loud_one", "quiet_one"]
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
