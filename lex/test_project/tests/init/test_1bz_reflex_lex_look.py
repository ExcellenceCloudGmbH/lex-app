"""The LEX look for Reflex dashboards: RFDS's components in lex-app's design, generated from the tokens.

Intent: a Reflex dashboard is part of lex-app -- opened from its Analytics tab, framed beside its
own screens -- so it has to look like lex-app, not like RFDS's stock theme (decision E13). Every
colour, the type, the radii and the chrome come from lex-app's design tokens
(``lex/lex_app/design_system/lex_tokens.py``), for the light and the dark mode; the header and
side navigation are lex-app's navy gradients, cards its 16 px soft-elevation cards, the primary
button its navy (light) or teal (dark) one; pages open in light mode, as lex-app does, and never
follow the operating system. A regression is a dashboard that reads as another product: RFDS's
slate canvas and indigo-cyan accent, square bordered cards, a different font -- which is what
the first cut shipped, because RFDS's brand sheet outranked lex-app's overrides.

Cluster 1bz -- scenarios 1.740-1.744. Type: U.
Covers: lex/lex_app/reflex/theme.py, lex/lex_app/reflex/design.py (lex_head_components,
brand_spec), lex/lex_app/reflex/config.py (default_color_mode), lex/lex_app/reflex/_js/lex_rfds.css.
Run: python -m lex pytest lex/test_project/tests/init/test_1bz_reflex_lex_look.py -v
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.design_system import lex_tokens
from lex.lex_app.reflex import theme

pytestmark = pytest.mark.init

_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _blocks(css: str) -> dict[str, dict[str, str]]:
    """``selector -> {variable: value}`` for each top-level rule of ``css`` (no nesting needed here)."""
    out: dict[str, dict[str, str]] = {}
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        out.setdefault(" ".join(selector.split()), {}).update(dict(_DECLARATION.findall(body)))
    return out


def _hex_literals_in_code(path: Path) -> list[str]:
    """Hex colours in the module's string values (its docstrings may name colours they explain)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [hit for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            for hit in re.findall(r"#[0-9a-fA-F]{3,8}\b", node.value)]


def _rfds_css() -> str:
    import rfds

    return (Path(rfds.__file__).parent / "styles" / "generated" / "rfds.css").read_text(encoding="utf-8")


class TestCluster01bz_Tokens(TestCase):
    """1.740: every value is lex-app's token, in both modes."""

    # -- 1.740 ---------------------------------------------------------
    def test_1_740_every_colour_type_and_radius_is_a_lex_token(self) -> None:
        """
        Scenario 1.740: the dashboards' design is lex-app's tokens, light and dark.
        Given: lex-app's design tokens.
        When:  the theme's variables are built for each mode.
        Then:  the canvas, surfaces, text, lines, accent, statuses, chrome, table header, font
               and radii are the tokens' values for that mode; the module types no colour
               itself; and a changed token changes the stylesheet with no other edit.
        """
        for mode in theme.MODES:
            t = lex_tokens.TOKENS[mode]
            c = t["color"]
            v = theme.rfds_variables(mode)
            expected = {
                "--rfds-color-bg-canvas": c["bg"]["canvas"],
                "--rfds-color-surface-panel": c["bg"]["surface"],
                "--rfds-color-text-default": c["text"]["primary"],
                "--rfds-color-text-muted": c["text"]["muted"],
                "--rfds-color-text-link": c["text"]["link"],
                "--rfds-color-border-default": c["border"]["line"],
                "--rfds-color-accent-solid": c["teal"]["500"],
                "--rfds-color-focus-ring": c["teal"]["500"],
                "--rfds-color-status-danger-fg": c["tag"]["error"]["fg"],
                "--rfds-color-status-success-bg": c["tag"]["success"]["bg"],
                "--rfds-color-chrome-bg": c["navy"]["700"],
                "--rfds-color-chrome-fg": t["headerbar"]["fg"],
                "--rfds-component-table-header-bg": t["table"]["headBg"],
                "--rfds-font-family-sans": t["fontFamily"]["sans"],
                "--rfds-radius-lg": t["radius"]["control"],
            }
            for name, value in expected.items():
                self.assertEqual(v[name], value, f"{mode}: {name} is not lex-app's token")
            self.assertEqual(theme.lex_variables(mode)["--lex-card-radius"], t["card"]["radius"])
        self.assertNotEqual(theme.rfds_variables("light")["--rfds-color-bg-canvas"],
                            theme.rfds_variables("dark")["--rfds-color-bg-canvas"], "each mode has its own canvas")
        self.assertIn("Inter", theme.rfds_variables("light")["--rfds-font-family-sans"])

        self.assertEqual(_hex_literals_in_code(Path(theme.__file__)), [], "colours come from the tokens only")

        changed = copy.deepcopy(lex_tokens.TOKENS)
        changed["light"]["color"]["bg"]["canvas"] = "#ABCDEF"
        with patch.object(lex_tokens, "TOKENS", changed):
            self.assertIn("--rfds-color-bg-canvas: #ABCDEF;", theme.lex_theme_css())


class TestCluster01bz_Precedence(TestCase):
    """1.741: the stylesheet wins over RFDS's own, wherever RFDS sets a variable."""

    # -- 1.741 ---------------------------------------------------------
    def test_1_741_lex_values_outrank_rfds_and_reach_every_radix_element(self) -> None:
        """
        Scenario 1.741: lex-app's values apply over RFDS's, in either load order.
        Given: RFDS's stylesheet (``:root`` / ``.light`` / ``.dark``), its brand sheet
               (``:root:root``), and its re-declaration of the accent variables on every Radix
               theme element.
        When:  the LEX stylesheet is generated.
        Then:  each mode's block starts at ``html:root:root`` (outranking ``:root:root``) and also
               covers regions forced into that mode; every variable RFDS re-declares on Radix
               elements that the theme gives a value of its own is forwarded there from a
               per-mode copy, and every other one RFDS derives from an accent step this theme sets -- so an RFDS release that re-declares one more
               fails here instead of quietly showing its own colour.
        """
        css = theme.lex_theme_css()
        blocks = _blocks(css)
        light = blocks[theme.SELECTORS["light"]]
        dark = blocks[theme.SELECTORS["dark"]]
        for selector in (theme.SELECTORS["light"], theme.SELECTORS["dark"]):
            for part in selector.split(","):
                self.assertTrue(part.strip().startswith("html:root:root"), part)
        self.assertIn("html:root:root .dark", theme.SELECTORS["dark"], "a forced-dark region gets dark values")
        self.assertIn("html:root:root .light", theme.SELECTORS["light"])
        self.assertLess(css.index(theme.SELECTORS["light"]), css.index(theme.SELECTORS["dark"]),
                        "dark comes last, so the dark html wins over its own light root rule")

        forwarding = blocks['html:root:root :is(.radix-themes, [data-accent-color="indigo"])']
        for name in theme.REDECLARED:
            forward = "--lex-v-" + name[len("--rfds-color-"):]
            self.assertEqual(forwarding[name], f"var({forward})")
            self.assertEqual(light[forward], light[name], f"light {forward}")
            self.assertEqual(dark[forward], dark[name], f"dark {forward}")

        match = re.search(r"\.radix-themes, \[data-accent-color\]:where\([^)]*\)\) \{([^}]*)\}", _rfds_css())
        self.assertIsNotNone(match, "RFDS's accent re-declaration block was not found")
        scale_steps = {name.replace("--indigo-", "--accent-") for name in theme.accent_scale("light")
                       if name.startswith("--indigo-")}
        scale_steps.add("--accent-contrast")  # set as --indigo-contrast
        ours = theme.rfds_variables("light")
        for name, value in _DECLARATION.findall(match.group(1)):
            if name in ours:  # a value of our own: RFDS's re-declaration would hide it unless forwarded
                self.assertIn(name, theme.REDECLARED, f"RFDS re-declares {name}; forward lex-app's value")
                continue
            used = re.findall(r"var\((--[\w-]+)\)", value)
            self.assertTrue(used and all(u in scale_steps for u in used),
                            f"RFDS re-declares {name}: {value} -- forward it or set the step it reads")


class TestCluster01bz_Accent(TestCase):
    """1.742: the accent is lex-app's teal itself."""

    # -- 1.742 ---------------------------------------------------------
    def test_1_742_the_accent_scale_is_the_lex_teal_not_rfds_adjusted_brand(self) -> None:
        """
        Scenario 1.742: RFDS's accent reads lex-app's teal, not its gate-adjusted version.
        Given: RFDS's brand sheet, which rebuilds Radix's indigo scale from the brand colour and
               nudges it to pass its contrast gates (#14B4B4 becomes a cyan).
        When:  the accent scale is built for each mode.
        Then:  the solid is TEAL 500 exactly, its hover the next step, the accent text a dark
               (light mode) or light (dark mode) teal, selection washes lex-app's teal alphas,
               text on a solid lex-app's on-teal colour, and the page and panel backgrounds the
               tokens' canvas and surface; the brand passed to RFDS is the same teal.
        """
        from lex.lex_app.reflex.design import brand_spec

        teal = lex_tokens.TOKENS["light"]["color"]["teal"]
        light, dark = theme.accent_scale("light"), theme.accent_scale("dark")
        self.assertEqual((light["--indigo-9"], dark["--indigo-9"]), (teal["500"], teal["500"]))
        self.assertEqual((light["--indigo-10"], dark["--indigo-10"]), (teal["600"], teal["400"]))
        self.assertEqual((light["--indigo-11"], dark["--indigo-11"]), (teal["700"], teal["light"]))
        alpha = lex_tokens.TOKENS["light"]["color"]["alpha"]
        self.assertEqual((light["--indigo-a3"], light["--indigo-a4"]), (alpha["tealSelectedSoft"], alpha["tealSelected"]))
        self.assertEqual(light["--indigo-contrast"], lex_tokens.TOKENS["light"]["color"]["text"]["onCta"])
        for mode, scale in (("light", light), ("dark", dark)):
            bg = lex_tokens.TOKENS[mode]["color"]["bg"]
            self.assertEqual((scale["--color-background"], scale["--color-panel-solid"]), (bg["canvas"], bg["surface"]))
        self.assertEqual(brand_spec().primary.upper(), teal["500"].upper())
        self.assertEqual(theme.mode_variables("light")["--indigo-9"], teal["500"], "the scale is in the mode block")


class TestCluster01bz_Components(TestCase):
    """1.743: lex-app's chrome, cards and buttons."""

    # -- 1.743 ---------------------------------------------------------
    def test_1_743_header_navigation_cards_and_buttons_are_lex_apps(self) -> None:
        """
        Scenario 1.743: the rules variables cannot express are lex-app's components.
        Given: lex-app's header bar, side navigation, cards and buttons (tokens and
               process-admin-general-client's theme).
        When:  the component rules are generated.
        Then:  the header is the navy gradient with its teal glow and no RFDS stripe; the side
               navigation the navy gradient, its current page a teal wash with a teal bar; cards
               and KPI tiles 16 px with soft elevation instead of a border; buttons 8 px, weight
               600, never uppercase, lifting on hover (not with reduced motion); the primary
               button the navy gradient with white text in light, the teal gradient with dark
               text in dark; focus a soft teal ring, and the page title no dashed outline.
        """
        rules = theme.component_rules()
        self.assertIn("html:root:root .rfds-header { background: var(--lex-header-bg); box-shadow: var(--lex-header-shadow); }", rules)
        self.assertIn("html:root:root .rfds-side-nav { background: var(--lex-sidenav-bg);", rules)
        self.assertRegex(rules, r'\[aria-current="page"\] \{\s*background-color: var\(--lex-sidenav-active-bg\);'
                                r'[^}]*inset 3px 0 0 var\(--lex-sidenav-accent\)')
        self.assertIn("--base-card-surface-box-shadow: var(--lex-card-shadow);", rules)
        self.assertIn("border-radius: var(--lex-card-radius);", rules)
        self.assertRegex(rules, r"\.rfds-button\.rt-Button \{[^}]*font-weight: var\(--lex-button-weight\);[^}]*text-transform: none;")
        self.assertIn("transform: translateY(-1px)", rules)
        self.assertRegex(rules, r"prefers-reduced-motion: reduce\) \{[^}]*transition: none;")
        self.assertRegex(rules, r'rt-variant-solid\[data-accent-color="indigo"\] \{\s*background: var\(--lex-button-primary-bg\);')
        self.assertIn(":is(.rfds-page-title, #rfds-page-title):focus-visible { outline: none; }", rules)

        t = lex_tokens.TOKENS
        light, dark = theme.lex_variables("light"), theme.lex_variables("dark")
        self.assertEqual(light["--lex-header-bg"], t["light"]["headerbar"]["bg"])
        self.assertEqual(light["--lex-sidenav-bg"], t["light"]["sidenav"]["bg"])
        self.assertEqual((light["--lex-button-primary-bg"], light["--lex-button-primary-fg"]),
                         (t["light"]["gradient"]["header"], t["light"]["color"]["text"]["onAction"]))
        self.assertEqual((dark["--lex-button-primary-bg"], dark["--lex-button-primary-fg"]),
                         (t["dark"]["gradient"]["cta"], t["dark"]["color"]["text"]["onCta"]))
        self.assertEqual((light["--lex-card-radius"], light["--lex-button-radius"], light["--lex-button-weight"]),
                         ("16px", "8px", "600"))
        self.assertEqual(theme.rfds_variables("light")["--rfds-color-chrome-stripe"], "transparent")


class TestCluster01bz_Pages(TestCase):
    """1.744: every page carries the look and opens light."""

    # -- 1.744 ---------------------------------------------------------
    def test_1_744_pages_load_inter_and_the_theme_and_open_in_light_mode(self) -> None:
        """
        Scenario 1.744: the look is on every page, and the mode is lex-app's.
        Given: a project's Reflex config and app built by lex-app.
        When:  the config is read and the head components are built.
        Then:  pages open in light mode (not the operating system's); the head loads Inter from
               Google Fonts and inlines the generated theme followed by the embedded layout;
               the embedded layout file no longer carries colours of its own.
        """
        from lex.lex_app.reflex.config import lex_config
        from lex.lex_app.reflex.design import lex_head_components

        self.assertEqual(lex_config().default_color_mode, "light")

        heads = lex_head_components()
        rendered = [str(h.render()) for h in heads]
        self.assertTrue(any(theme.INTER_STYLESHEET in r for r in rendered), "Inter is loaded")
        self.assertIn("family=Inter", theme.INTER_STYLESHEET)
        style = heads[-1]
        css = "".join(str(child.render()) for child in style.children)
        self.assertIn("--rfds-color-bg-canvas", css)
        self.assertIn(".lex-embedded .rfds-shell-body", css)
        self.assertLess(css.index("--rfds-color-bg-canvas"), css.index(".lex-embedded"))

        from lex.lex_app.reflex._js import HERE

        embedded = (HERE / "lex_rfds.css").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", embedded), [], "colours live in the theme only")
