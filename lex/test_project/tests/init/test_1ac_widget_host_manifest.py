"""Intent: a widget an author asked for must render, or say why it did not.

The failure this file prevents is the silent one -- a widget missing from a
dashboard because an option was misspelled, with a page that renders cleanly and
nothing in the logs. Validation therefore raises at the ``page.calculation(...)``
call site, where the traceback points at the line the author wrote.

Also pins the contract that makes the design worth having: one host render per
page regardless of widget count, and status envelopes routed back to the widget
that produced them.

Cluster 01-init, batch 1ac, scenarios 1.251-1.258.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ac_widget_host_manifest.py
"""

import pytest

from lex.lex_app.streamlit.widgets.spec import (
    MANIFEST_VERSION,
    WidgetSpecError,
    build_manifest,
    calculation_spec,
)

pytestmark = pytest.mark.init


class TestCluster1ac_WidgetHostManifest:
    """Manifest construction and validation -- pure, no Streamlit runtime."""

    def test_01_251_valid_spec_normalises_options(self):
        """Scenario 1.251: a well-formed calculation spec resolves every option.

        Given a calculation widget declared with show_log on
        When the spec is built
        Then the option set is concrete, so no consumer branches on absence
        """
        spec = calculation_spec("w1", "quarter", 42, show_log=True)
        assert spec["type"] == "calculation"
        assert spec["model"] == "quarter"
        assert spec["pk"] == 42
        assert spec["options"]["show_log"] is True
        assert spec["options"]["on_status"] is False

    def test_01_252_rejects_bool_pk(self):
        """Scenario 1.252: a boolean pk is refused rather than coerced.

        Given ``pk=True`` -- a mistake, since bool subclasses int
        When the spec is built
        Then it raises, instead of silently addressing record id 1
        """
        with pytest.raises(WidgetSpecError, match="pk must be"):
            calculation_spec("w1", "quarter", True)

    def test_01_253_rejects_empty_model(self):
        """Scenario 1.253: a widget without a model is refused at call time."""
        with pytest.raises(WidgetSpecError, match="model"):
            calculation_spec("w1", "", 42)

    def test_01_254_rejects_non_positive_log_height(self):
        """Scenario 1.254: a zero or negative log height is refused.

        A zero-height log renders as an invisible widget -- the silent failure
        this suite exists to prevent.
        """
        with pytest.raises(WidgetSpecError, match="log_height"):
            calculation_spec("w1", "quarter", 42, log_height=0)

    def test_01_255_manifest_carries_version_and_layout(self):
        """Scenario 1.255: the manifest declares its version and layout.

        The host refuses versions it does not know rather than guessing, so the
        producer must always state one.
        """
        manifest = build_manifest([calculation_spec("w1", "quarter", 42)])
        assert manifest["version"] == MANIFEST_VERSION
        assert manifest["layout"] == {"kind": "rows"}
        assert len(manifest["widgets"]) == 1

    def test_01_256_rejects_duplicate_widget_ids(self):
        """Scenario 1.256: two widgets may not share an id.

        Given two specs with the same id
        When the manifest is assembled
        Then it raises -- ids route status envelopes back to a widget, so a
        duplicate would deliver one widget's result to another
        """
        specs = [calculation_spec("same", "quarter", 1), calculation_spec("same", "quarter", 2)]
        with pytest.raises(WidgetSpecError, match="duplicate widget id"):
            build_manifest(specs)

    def test_01_257_rejects_unknown_option(self):
        """Scenario 1.257: an unrecognised option is refused, not ignored.

        Given a spec carrying ``shwo_log`` (a plausible typo)
        When the manifest is assembled
        Then it raises and names both the offender and the valid options
        """
        spec = calculation_spec("w1", "quarter", 42)
        spec["options"]["shwo_log"] = True
        with pytest.raises(WidgetSpecError) as exc:
            build_manifest([spec])
        assert "shwo_log" in str(exc.value)
        assert "show_log" in str(exc.value)

    def test_01_258_rejects_unknown_widget_type(self):
        """Scenario 1.258: an unknown widget type is refused with the known set."""
        spec = calculation_spec("w1", "quarter", 42)
        spec["type"] = "calculaton"
        with pytest.raises(WidgetSpecError) as exc:
            build_manifest([spec])
        assert "calculaton" in str(exc.value)
        assert "calculation" in str(exc.value)
