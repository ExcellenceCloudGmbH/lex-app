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
        # Fonts are now stylesheet references ("<family>:<url>, <fallback>"),
        # pointing at the same Google Fonts source the frontend uses — see the
        # design doc section 8.2. The family still leads the value.
        assert theme["font"].startswith("Inter:https://")
        assert theme["headingFont"].startswith("Inter:https://")
        assert theme["codeFont"].startswith("Fira Code:https://")
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

        Font keys are COMPOSED (family + stylesheet URL + fallback) rather than
        copied from a single token, so they are asserted by family prefix; they
        stay in the completeness check so no key escapes assertion.
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
        # Composed values: derived from several tokens rather than copied from
        # one, so they are asserted by prefix instead of equality. Kept in the
        # completeness check below so a new key still cannot slip through
        # unasserted.
        composed = {
            "font": ("font", "body"),
            "headingFont": ("font", "heading"),
            "codeFont": ("font", "code"),
        }

        for mode in ("light", "dark"):
            theme = build_streamlit_theme(TOKENS, mode)

            assert set(theme) == set(wiring) | set(literals) | set(composed), (
                f"{mode}: emitted keys drifted from the wiring contract; "
                f"unexpected={set(theme) - set(wiring) - set(literals) - set(composed)}, "
                f"missing={(set(wiring) | set(literals) | set(composed)) - set(theme)}"
            )

            for key, (section, leaf) in wiring.items():
                source = TOKENS["modes"][mode] if section == "$mode" else TOKENS[section]
                origin = f"modes.{mode}" if section == "$mode" else section
                assert theme[key] == source[leaf], (
                    f"{mode}: {key} should be wired to {origin}.{leaf}"
                )

            for key, expected in literals.items():
                assert theme[key] is expected, f"{mode}: {key} should be {expected!r}"

            for key, (section, leaf) in composed.items():
                family = TOKENS[section][leaf]
                assert theme[key].startswith(f"{family}:"), (
                    f"{mode}: {key} should lead with the family from {section}.{leaf}"
                )

    # -- 1.208c -------------------------------------------------------
    def test_1_208c_declared_modes_match_the_token_modes(self) -> None:
        """Scenario 1.208: the module's mode tuple and the token file's mode
        keys stay in sync. These are two sources of truth (later tasks iterate
        ``_MODES`` to emit per-mode config), so a mode added to only one side
        must fail here rather than surface as a KeyError much later."""
        from lex.lex_app.streamlit.theme.mapping import _MODES
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        assert set(_MODES) == set(TOKENS["modes"])


class TestCluster1y_StreamlitContract:
    """Every key we emit must really exist in the installed Streamlit.

    These tests read Streamlit's own config-option template, so they fail the
    moment an upgrade renames, removes, or re-scopes a theme key — instead of
    letting the styling silently stop applying.

    Each test guards against a vacuous pass: an empty mapping must fail these
    assertions loudly rather than satisfy them by having nothing to check.
    """

    @staticmethod
    def _template() -> dict:
        from streamlit import config

        # Populate the template; it is built lazily on first access.
        config.get_config_options()
        return config._config_options_template

    # -- 1.209 --------------------------------------------------------
    def test_1_209_every_emitted_key_exists_in_streamlit(self) -> None:
        """Scenario 1.209: each mapped key resolves to a real
        ``theme.<key>`` config option in the installed Streamlit."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        template = self._template()
        emitted = build_streamlit_theme(TOKENS, "light")
        assert emitted, "build_streamlit_theme returned no keys to validate"

        unknown = sorted(k for k in emitted if f"theme.{k}" not in template)
        assert not unknown, (
            f"these keys do not exist in Streamlit's theme config: {unknown}. "
            f"A Streamlit upgrade probably renamed or removed them — fix "
            f"mapping.py rather than deleting this assertion."
        )

    # -- 1.209b -------------------------------------------------------
    def test_1_209b_global_only_keys_are_declared_correctly(self) -> None:
        """Scenario 1.209: the keys Streamlit accepts ONLY at ``[theme]``
        (no per-mode twin) are exactly the ones the module declares.

        The config writer routes keys by this set: a per-mode block containing
        a global-only key would make Streamlit reject the config as an
        unrecognised option. If an upgrade gives the chart palettes per-mode
        support, this test tells us we may narrow the set.
        """
        from lex.lex_app.streamlit.theme.mapping import (
            GLOBAL_ONLY_KEYS,
            build_streamlit_theme,
        )
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        template = self._template()
        emitted = build_streamlit_theme(TOKENS, "light")

        actually_global_only = {
            k
            for k in emitted
            if f"theme.light.{k}" not in template or f"theme.dark.{k}" not in template
        }
        assert actually_global_only == set(GLOBAL_ONLY_KEYS), (
            f"GLOBAL_ONLY_KEYS is out of date. Streamlit says global-only="
            f"{sorted(actually_global_only)}, module declares="
            f"{sorted(GLOBAL_ONLY_KEYS)}"
        )

    # -- 1.209c -------------------------------------------------------
    def test_1_209c_per_mode_keys_exist_in_both_mode_namespaces(self) -> None:
        """Scenario 1.209: every non-global-only key exists under BOTH
        ``theme.light.`` and ``theme.dark.``, so neither mode block can carry
        a key the other rejects."""
        from lex.lex_app.streamlit.theme.mapping import (
            GLOBAL_ONLY_KEYS,
            build_streamlit_theme,
        )
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        template = self._template()
        emitted = build_streamlit_theme(TOKENS, "light")

        per_mode = set(emitted) - set(GLOBAL_ONLY_KEYS)
        assert per_mode, "no per-mode keys to validate"

        for key in sorted(per_mode):
            assert f"theme.light.{key}" in template, f"missing theme.light.{key}"
            assert f"theme.dark.{key}" in template, f"missing theme.dark.{key}"


class TestCluster1y_ConfigWriter:
    """The theme reaches Streamlit as valid config: TOML and CLI flags."""

    # -- 1.210 --------------------------------------------------------
    def test_1_210_global_only_keys_never_land_in_a_mode_block(self) -> None:
        """Scenario 1.210: the 4 global-only keys appear at [theme] only.

        Streamlit rejects unrecognised config options outright, so a chart
        palette inside [theme.light] would break every dashboard rather than
        just look wrong.
        """
        from lex.lex_app.streamlit.theme.config_writer import build_full_config
        from lex.lex_app.streamlit.theme.mapping import GLOBAL_ONLY_KEYS
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        cfg = build_full_config(TOKENS)

        for key in GLOBAL_ONLY_KEYS:
            assert key in cfg["theme"], f"{key} missing from [theme]"
            assert key not in cfg["theme"]["light"], f"{key} leaked into light"
            assert key not in cfg["theme"]["dark"], f"{key} leaked into dark"

    # -- 1.211 --------------------------------------------------------
    def test_1_211_per_mode_values_differ_where_the_tokens_differ(self) -> None:
        """Scenario 1.211: the mode blocks carry that mode's own surfaces,
        while the brand accent stays constant across both."""
        from lex.lex_app.streamlit.theme.config_writer import build_full_config
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        cfg = build_full_config(TOKENS)

        assert cfg["theme"]["light"]["backgroundColor"] == "#ffffff"
        assert cfg["theme"]["dark"]["backgroundColor"] == "#0d1117"
        assert cfg["theme"]["light"]["primaryColor"] == "#14b4b4"
        assert cfg["theme"]["dark"]["primaryColor"] == "#14b4b4"

    # -- 1.212 --------------------------------------------------------
    def test_1_212_dotted_keys_become_nested_sidebar_tables(self) -> None:
        """Scenario 1.212: `sidebar.backgroundColor` is emitted as a nested
        `sidebar` table, which is the shape Streamlit's TOML parser expects —
        a literal dotted key would be read as an unknown option."""
        from lex.lex_app.streamlit.theme.config_writer import build_full_config
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        cfg = build_full_config(TOKENS)

        assert cfg["theme"]["light"]["sidebar"]["backgroundColor"] == "#283C50"
        assert cfg["theme"]["dark"]["sidebar"]["backgroundColor"] == "#283C50"
        # No literal dotted key survives anywhere.
        for scope in (cfg["theme"], cfg["theme"]["light"], cfg["theme"]["dark"]):
            assert not [k for k in scope if "." in k], f"dotted key left in {scope.keys()}"

    # -- 1.213 --------------------------------------------------------
    def test_1_213_rendered_toml_parses_and_round_trips(self) -> None:
        """Scenario 1.213: the rendered text is valid TOML and preserves the
        structure — not a string we merely hope Streamlit can read."""
        import tomllib

        from lex.lex_app.streamlit.theme.config_writer import (
            build_full_config,
            render_config_toml,
        )
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        text = render_config_toml(build_full_config(TOKENS))
        parsed = tomllib.loads(text)

        assert parsed["theme"]["primaryColor"] == "#14b4b4"
        assert parsed["theme"]["light"]["sidebar"]["backgroundColor"] == "#283C50"
        assert parsed["theme"]["dark"]["backgroundColor"] == "#0d1117"
        assert parsed["theme"]["chartCategoricalColors"][0] == "#14b4b4"
        assert parsed["theme"]["linkUnderline"] is False

    # -- 1.214 --------------------------------------------------------
    def test_1_214_cli_flags_are_fully_qualified_and_streamlit_valid(self) -> None:
        """Scenario 1.214: every emitted flag is `--theme.<path>=<value>` and
        names a real Streamlit option. Flags are the primary delivery path
        (a config file's location depends on the working directory), so a
        malformed flag name would make Streamlit exit on startup.
        """
        from streamlit import config

        from lex.lex_app.streamlit.theme.config_writer import theme_cli_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        config.get_config_options()
        template = config._config_options_template

        flags = theme_cli_flags(TOKENS)
        assert flags, "no flags emitted"

        for flag in flags:
            assert flag.startswith("--theme."), f"not a theme flag: {flag}"
            assert "=" in flag, f"flag has no value: {flag}"
            option = flag[2:].split("=", 1)[0]
            assert option in template, f"{option} is not a Streamlit config option"

    # -- 1.215 --------------------------------------------------------
    def test_1_215_list_and_bool_values_are_cli_encoded(self) -> None:
        """Scenario 1.215: chart palettes and booleans survive the flag
        encoding as JSON, which is what Streamlit's parser accepts — a bare
        Python `['#14b4b4']` or `False` would be mis-parsed."""
        from lex.lex_app.streamlit.theme.config_writer import theme_cli_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        flags = theme_cli_flags(TOKENS)
        by_option = {f[2:].split("=", 1)[0]: f.split("=", 1)[1] for f in flags}

        assert by_option["theme.chartCategoricalColors"].startswith("[")
        assert '"#14b4b4"' in by_option["theme.chartCategoricalColors"]
        assert by_option["theme.linkUnderline"] == "false"
        assert by_option["theme.showWidgetBorder"] == "true"

    # -- 1.216 --------------------------------------------------------
    def test_1_216_write_config_creates_the_file(self, tmp_path) -> None:
        """Scenario 1.216: `write_config` puts parseable TOML on disk at the
        given path, creating parent directories."""
        import tomllib

        from lex.lex_app.streamlit.theme.config_writer import write_config
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        target = tmp_path / ".streamlit" / "config.toml"
        write_config(target, TOKENS)

        assert target.exists()
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))
        assert parsed["theme"]["primaryColor"] == "#14b4b4"


class TestCluster1y_Fonts:
    """Fonts come from the SAME source the lex-app frontend uses.

    The frontend loads Inter from Google Fonts. Streamlit is pointed at that
    same stylesheet rather than a bundled copy, so the two surfaces agree in
    both directions: with network access both render Inter, and in an
    air-gapped deployment both fall back to system sans together. A bundled
    font for Streamlit alone would make it diverge from the app it must match.
    """

    # -- 1.217 --------------------------------------------------------
    def test_1_217_font_keys_reference_the_frontend_stylesheet(self) -> None:
        """Scenario 1.217: the body/heading fonts carry Streamlit's
        ``<name>:<url>`` stylesheet form, pointing at the same Google Fonts
        URL the frontend uses."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        theme = build_streamlit_theme(TOKENS, "light")

        for key in ("font", "headingFont"):
            value = theme[key]
            assert value.startswith("Inter:https://"), f"{key} is not a stylesheet ref: {value}"
            assert "fonts.googleapis.com/css2" in value, f"{key} lost the stylesheet URL"
            assert "family=Inter" in value, f"{key} does not request Inter"

    # -- 1.218 --------------------------------------------------------
    def test_1_218_font_declares_a_system_fallback(self) -> None:
        """Scenario 1.218: a fallback stack follows the webfont, so an
        air-gapped browser renders system sans instead of Streamlit's default
        face. Streamlit takes fallbacks as a comma-separated list."""
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        theme = build_streamlit_theme(TOKENS, "light")

        assert "," in theme["font"], "no fallback declared"
        assert theme["font"].rstrip().endswith("sans-serif")

    # -- 1.218b -------------------------------------------------------
    def test_1_218b_font_url_matches_the_frontend_weights(self) -> None:
        """Scenario 1.218: the requested weights match the frontend's own
        request, so headings and body copy have the same faces available in
        both surfaces. Frontend uses wght@300;400;500;600;700."""
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        url = TOKENS["font"]["stylesheet_url"]
        for weight in ("300", "400", "500", "600", "700"):
            assert weight in url, f"weight {weight} missing from the font URL"
        assert "display=swap" in url, "display=swap keeps text visible while loading"

    # -- 1.218c -------------------------------------------------------
    def test_1_218c_no_bundled_font_asset_is_referenced(self) -> None:
        """Scenario 1.218: nothing references a local font file. Bundling was
        deliberately rejected (see the design's §8.2) — a vendored woff2 for
        Streamlit alone would render Inter while an air-gapped frontend
        rendered system sans."""
        from lex.lex_app.streamlit.theme.config_writer import build_full_config
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        cfg = build_full_config(TOKENS)
        rendered = repr(cfg)

        assert ".woff" not in rendered
        assert "fontFaces" not in cfg["theme"]
        assert "static/" not in rendered


class TestCluster1y_LaunchFlags:
    """The theme reaches a `lex streamlit` launch as CLI flags.

    Flags are the primary delivery path: a `.streamlit/config.toml` resolves
    relative to the working directory, so only flags theme every launch
    identically regardless of where the dashboard was started from.
    """

    # -- 1.219 --------------------------------------------------------
    def test_1_219_theme_flags_are_added_to_a_plain_launch(self) -> None:
        """Scenario 1.219: a launch with no theme flags of its own receives
        the full generated set."""
        from lex.lex_app.streamlit.theme.config_writer import compose_launch_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        added = compose_launch_flags(["run", "dash.py"], TOKENS)

        assert added, "no theme flags composed"
        assert all(f.startswith("--theme.") for f in added)
        assert any(f.startswith("--theme.primaryColor=") for f in added)

    # -- 1.220 --------------------------------------------------------
    def test_1_220_an_explicit_user_flag_is_never_overridden(self) -> None:
        """Scenario 1.220: a customer who sets `--theme.primaryColor` keeps
        their value — we omit ours for that option rather than relying on
        Streamlit's precedence rules."""
        from lex.lex_app.streamlit.theme.config_writer import compose_launch_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        user_args = ["run", "dash.py", "--theme.primaryColor=#ff0000"]
        added = compose_launch_flags(user_args, TOKENS)

        assert not any(f.startswith("--theme.primaryColor=") for f in added), (
            "we clobbered the user's explicit primaryColor"
        )
        # Everything else is still themed.
        assert any(f.startswith("--theme.backgroundColor=") for f in added)

    # -- 1.220b -------------------------------------------------------
    def test_1_220b_user_flag_in_space_separated_form_is_respected(self) -> None:
        """Scenario 1.220: Streamlit also accepts `--opt value`, so the
        override check must not depend on the `=` form."""
        from lex.lex_app.streamlit.theme.config_writer import compose_launch_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        user_args = ["run", "dash.py", "--theme.primaryColor", "#ff0000"]
        added = compose_launch_flags(user_args, TOKENS)

        assert not any(f.startswith("--theme.primaryColor=") for f in added)

    # -- 1.220c -------------------------------------------------------
    def test_1_220c_a_user_mode_block_flag_is_respected_precisely(self) -> None:
        """Scenario 1.220: overriding `--theme.dark.backgroundColor` must
        suppress only that flag, not the light one or the base."""
        from lex.lex_app.streamlit.theme.config_writer import compose_launch_flags
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        added = compose_launch_flags(
            ["run", "dash.py", "--theme.dark.backgroundColor=#000000"], TOKENS
        )
        options = {f[2:].split("=", 1)[0] for f in added}

        assert "theme.dark.backgroundColor" not in options
        assert "theme.light.backgroundColor" in options
        assert "theme.backgroundColor" in options

    # -- 1.221 --------------------------------------------------------
    def test_1_221_a_broken_theme_never_blocks_the_launch(self) -> None:
        """Scenario 1.221: if the theme cannot be built, the CLI launches
        Streamlit UNTHEMED rather than failing. The design's safety property is
        that every failure degrades one rung and never to broken."""
        from lex.bin.lex import _safe_theme_flags

        # A tokens dict missing every section is the worst realistic case.
        assert _safe_theme_flags(["run", "dash.py"], tokens={}) == []
