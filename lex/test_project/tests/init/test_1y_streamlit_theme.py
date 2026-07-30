"""Cluster 1y — Streamlit theme parity (scenarios 1.203–1.218).

Intent: Streamlit pages must carry the real lex brand. The theme is derived
from vendored design tokens by pure functions, so the whole mapping is
assertable as a data transform — no browser, no running server.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.init


class TestCluster1y_Tokens:
    """The vendored token module is well-formed and matches the frontend."""

    # -- 1.203 --------------------------------------------------------
    def test_1_203_light_and_dark_expose_identical_keys(self) -> None:
        """Scenario 1.203: both modes define exactly the same token keys, so
        no mode can silently lack a colour the other has."""
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        light = TOKENS["modes"]["light"]
        dark = TOKENS["modes"]["dark"]
        assert set(light) == set(dark), (
            f"light/dark token key mismatch: "
            f"only-light={set(light) - set(dark)}, only-dark={set(dark) - set(light)}"
        )

    # -- 1.204 --------------------------------------------------------
    def test_1_204_brand_values_match_the_frontend(self) -> None:
        """Scenario 1.204: the brand accent and sidebar navy are the values the
        frontend actually ships. Guards the exact drift this batch fixes —
        the old config said #08BCC2."""
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        assert TOKENS["brand"]["primary"] == "#14b4b4"
        assert TOKENS["brand"]["sidebar_bg"] == "#283C50"
        assert TOKENS["brand"]["primary"] != "#08BCC2", "stale pre-2026-07 accent"

    # -- 1.204 (companion) --------------------------------------------
    def test_1_204b_tokens_hash_is_derived_from_the_tokens(self) -> None:
        """Scenario 1.204: TOKENS_HASH is a real digest of TOKENS, not a
        constant. Phase 4's CI drift check compares this value against the
        design system's published tokens, so the canonicalisation (sorted-key
        JSON, sha256) is load-bearing and must not change silently."""
        import hashlib
        import json

        from lex.lex_app.streamlit.theme.tokens import TOKENS, TOKENS_HASH

        assert len(TOKENS_HASH) == 64
        assert set(TOKENS_HASH) <= set("0123456789abcdef")

        expected = hashlib.sha256(
            json.dumps(TOKENS, sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert TOKENS_HASH == expected

        # It must actually track the data: a mutated copy hashes differently.
        mutated = json.loads(json.dumps(TOKENS))
        mutated["brand"]["primary"] = "#000000"
        mutated_hash = hashlib.sha256(
            json.dumps(mutated, sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert mutated_hash != TOKENS_HASH


class TestCluster1y_Mapping:
    """Tokens map onto Streamlit's native theme keys, per mode."""

    # -- 1.205 --------------------------------------------------------
    def test_1_205_light_mode_carries_brand_and_typography(self) -> None:
        """Scenario 1.205: the mapped light theme uses the brand accent, the
        navy sidebar, Inter, and the lex radii."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        theme = build_streamlit_theme(TOKENS, "light")

        assert theme["primaryColor"] == "#14b4b4"
        assert theme["backgroundColor"] == "#ffffff"
        assert theme["sidebar.backgroundColor"] == "#283C50"
        assert theme["sidebar.textColor"] == "#dfe7ee"
        assert theme["font"] == "Inter"
        assert theme["headingFont"] == "Inter"
        assert theme["codeFont"] == "Fira Code"
        assert theme["baseRadius"] == "12px"
        assert theme["buttonRadius"] == "10px"
        assert theme["dataframeHeaderBackgroundColor"] == "#F6F8FA"

    # -- 1.206 --------------------------------------------------------
    def test_1_206_dark_mode_swaps_surfaces_but_keeps_brand(self) -> None:
        """Scenario 1.206: dark mode changes surfaces and text while the brand
        accent and the navy sidebar stay put — the sidebar is navy in BOTH
        modes in lex-app."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        dark = build_streamlit_theme(TOKENS, "dark")

        assert dark["backgroundColor"] == "#0d1117"
        assert dark["textColor"] == "#c9d1d9"
        assert dark["primaryColor"] == "#14b4b4"
        assert dark["sidebar.backgroundColor"] == "#283C50"

    # -- 1.207 --------------------------------------------------------
    def test_1_207_chart_palettes_are_mapped_as_lists(self) -> None:
        """Scenario 1.207: chart ramps reach Streamlit so plots match the
        brand instead of falling back to Streamlit's defaults."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        theme = build_streamlit_theme(TOKENS, "light")

        assert theme["chartCategoricalColors"][0] == "#14b4b4"
        assert isinstance(theme["chartSequentialColors"], list)
        assert isinstance(theme["chartDivergingColors"], list)

    # -- 1.208 --------------------------------------------------------
    def test_1_208_unknown_mode_is_rejected(self) -> None:
        """Scenario 1.208: a typo'd mode fails loudly instead of silently
        producing a half-built theme."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        with pytest.raises(ValueError, match="unknown mode"):
            build_streamlit_theme(TOKENS, "sepia")

    # -- 1.208b -------------------------------------------------------
    def test_1_208b_every_emitted_key_is_wired_to_the_right_token(self) -> None:
        """Scenario 1.208: the COMPLETE key->token wiring, for both modes.

        1.205-1.207 spot-check the headline keys; this pins every one of them.
        Without it a swapped pair (success<->warning, primary<->primary_hover)
        passes every other test silently — precisely the drift class this
        batch exists to prevent. The set-equality assertion also means adding
        a new key to the mapping FORCES updating this contract.
        """
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        # (streamlit key -> path into TOKENS); "$mode" means the mode dict.
        wiring = {
            "primaryColor": ("brand", "primary"),
            "backgroundColor": ("$mode", "background"),
            "secondaryBackgroundColor": ("$mode", "secondary_background"),
            "textColor": ("$mode", "text"),
            "borderColor": ("$mode", "border"),
            "linkColor": ("$mode", "link"),
            "font": ("font", "body"),
            "headingFont": ("font", "heading"),
            "codeFont": ("font", "code"),
            "baseRadius": ("radius", "base"),
            "buttonRadius": ("radius", "control"),
            "codeBackgroundColor": ("$mode", "code_background"),
            "dataframeHeaderBackgroundColor": ("$mode", "dataframe_header_background"),
            "dataframeBorderColor": ("$mode", "border"),
            "chartCategoricalColors": ("chart", "categorical"),
            "chartSequentialColors": ("chart", "sequential"),
            "chartDivergingColors": ("chart", "diverging"),
            "greenColor": ("$mode", "success"),
            "orangeColor": ("$mode", "warning"),
            "redColor": ("$mode", "error"),
            "sidebar.backgroundColor": ("brand", "sidebar_bg"),
            "sidebar.textColor": ("brand", "sidebar_text"),
            "sidebar.primaryColor": ("brand", "primary"),
            "sidebar.borderColor": ("brand", "sidebar_bg_end"),
        }
        literals = {
            "linkUnderline": False,
            "showWidgetBorder": True,
            "showSidebarBorder": False,
        }

        for mode in ("light", "dark"):
            theme = build_streamlit_theme(TOKENS, mode)

            assert set(theme) == set(wiring) | set(literals), (
                f"{mode}: emitted keys drifted from the wiring contract; "
                f"unexpected={set(theme) - set(wiring) - set(literals)}, "
                f"missing={(set(wiring) | set(literals)) - set(theme)}"
            )

            for key, (section, leaf) in wiring.items():
                source = TOKENS["modes"][mode] if section == "$mode" else TOKENS[section]
                origin = f"modes.{mode}" if section == "$mode" else section
                assert theme[key] == source[leaf], (
                    f"{mode}: {key} should be wired to {origin}.{leaf}"
                )

            for key, expected in literals.items():
                assert theme[key] is expected, f"{mode}: {key} should be {expected!r}"

    # -- 1.208c -------------------------------------------------------
    def test_1_208c_declared_modes_match_the_token_modes(self) -> None:
        """Scenario 1.208: the module's mode tuple and the token file's mode
        keys stay in sync. These are two sources of truth (later tasks iterate
        ``_MODES`` to emit per-mode config), so a mode added to only one side
        must fail here rather than surface as a KeyError much later."""
        from lex.lex_app.streamlit.theme.mapping import _MODES
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        assert set(_MODES) == set(TOKENS["modes"])
