"""Intent: switching mode must not cost the lex brand.

Two mechanisms met in one merge and depend on each other in a way neither one
states. Batch 1ae themes Streamlit from LEX design tokens through config;
batch 1ad switches mode at runtime by reloading with
``?embed_options=light_theme|dark_theme``. Read separately, the switch looks like
it replaces the whole theme with Streamlit's built-in palette -- which would mean
every theme follow silently discarded the branding.

It does not, and the reason is worth writing down because it is not obvious from
either branch. In Streamlit 1.58 the URL option is a *preference signal*, not a
theme; the resolver then prefers the custom variant of that mode::

    Light: ["Custom Theme Light", "Light"]     # custom first, built-in as fallback
    Dark:  ["Custom Theme Dark",  "Dark"]

and "Custom Theme Light"/"Custom Theme Dark" exist only when the config supplies
BOTH ``[theme.light]`` and ``[theme.dark]``. A config carrying only the flat
``[theme]`` section produces a single unnamed custom theme, which that table
cannot reach -- so the switch would fall through to the built-in and the brand
would vanish on the first mode change.

So the coupling is: the theme follower is safe *because* the generated config
populates both mode sections. This file pins that, since the config is generated
and a future simplification to one flat section would be a silent regression --
the theme would look right until someone switched mode.

Cluster 01-init, batch 1af, scenario 1.300.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1af_theme_switch_preserves_brand.py
"""

import pathlib

import pytest

from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
from lex.lex_app.streamlit.theme.tokens import TOKENS
from lex.streamlit_theme import embed_theme_from_params

pytestmark = pytest.mark.init

#: Repo root, from this file's location under lex/test_project/tests/init/.
_LEX = pathlib.Path(__file__).resolve().parents[3]


class TestCluster1af_ThemeSwitchPreservesBrand:
    """The runtime mode switch and the launch-time branding must compose."""

    def test_01_300_both_mode_sections_carry_the_brand(self):
        """Scenario 1.300: light and dark are both branded, so either can be chosen.

        Given the generated Streamlit config
        When each mode is inspected
        Then both name the brand accent, so selecting either mode by URL lands on
             a *custom* theme rather than falling through to Streamlit's built-in
        """
        light = build_streamlit_theme(TOKENS, "light")
        dark = build_streamlit_theme(TOKENS, "dark")

        # The accent is the most visible thing a fallback would lose.
        assert light["primaryColor"] == dark["primaryColor"], (
            "the accent must survive a mode switch"
        )
        assert light["primaryColor"].lower() == "#14b4b4"

        # Surfaces differ -- otherwise there would be nothing to switch.
        assert light["backgroundColor"] != dark["backgroundColor"]

        config = (_LEX / ".streamlit/config.toml").read_text()
        for section in ("[theme.light]", "[theme.dark]"):
            assert section in config, (
                f"{section} missing: without both, Streamlit builds a single unnamed "
                "custom theme that ?embed_options=light_theme|dark_theme cannot select, "
                "so the first theme follow would revert to the built-in palette"
            )

        # Both sections must be populated, not merely present -- Streamlit only
        # registers a custom light/dark pair when each has real values.
        for marker in ("[theme.light]", "[theme.dark]"):
            body = config.split(marker, 1)[1].split("\n[", 1)[0]
            assert "primaryColor" in body, f"{marker} is present but empty"

    def test_01_300_the_two_vocabularies_stay_distinct(self):
        """Scenario 1.300 (second half): our mode names are not Streamlit's.

        ``embed_theme_from_params`` speaks "light"/"dark"; Streamlit's URL speaks
        ``light_theme``/``dark_theme``. Collapsing the two is an easy edit and a
        silent one -- Streamlit drops an unrecognised embed option without
        complaint, so the page would simply never change mode.
        """
        assert embed_theme_from_params(["dark_theme"]) == "dark"
        assert embed_theme_from_params(["light_theme"]) == "light"
        # The bare internal spellings are NOT what Streamlit accepts, and must
        # not be read as if they were.
        assert embed_theme_from_params(["dark"]) == ""
        assert embed_theme_from_params(["light"]) == ""
