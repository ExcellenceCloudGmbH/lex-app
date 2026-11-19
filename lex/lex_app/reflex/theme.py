"""The LEX look for Reflex dashboards: RFDS's components, dressed entirely in lex-app's design (decision E13).

RFDS draws every component from its own CSS variables -- canvas, panels, text, borders, accents,
statuses, radii, type, the chrome. Left alone they give RFDS's look (a slate canvas, indigo-derived
accents, IBM Plex, square 4-6 px corners, flat cards), which beside lex-app's own screens reads as
another product. This module sets every one of those variables from lex-app's design tokens
(:mod:`lex.lex_app.design_system.lex_tokens`), for the light and the dark mode, and adds the few
rules variables cannot express: lex-app's gradient header and side navigation, its 16 px cards
with soft elevation, its navy (light) or teal (dark) primary buttons, its soft teal focus ring.

Nothing here types a colour: every value is read from ``TOKENS`` when the app compiles, so a new
release of the token file restyles the dashboards with no change here. The stylesheet is inlined in
every page's head (:func:`lex.lex_app.reflex.design.lex_head_components`). Its selectors start at
``html:root:root`` (specificity 0,2,1), so they outrank RFDS's own ``:root`` / ``.light`` / ``.dark``
rules and its brand sheet's ``:root:root`` (0,2,0) whatever order the sheets load in.

Two RFDS mechanisms need more than a variable on the page root:

* RFDS derives its accent from Radix's ``indigo`` scale (its brand sheet rewrites that scale from
  the brand colour, nudged to pass its contrast gates -- ``#14B4B4`` becomes ``#25afcc``). The
  scale steps lex-app's look depends on are set here from the LEX teal itself
  (:func:`accent_scale`).
* RFDS re-declares the accent, link, focus, selection and panel variables on every Radix theme
  element, from that scale. Those (:data:`REDECLARED`) are forwarded there from a ``--lex-v-*``
  copy set per mode, so a region forced into the other mode still gets that mode's value.
"""

from __future__ import annotations

from typing import Any, Mapping

#: lex-app loads Inter from Google Fonts (process-admin-general-client ``src/App.css``); the
#: dashboards load the same weights the same way.
INTER_STYLESHEET = "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"

MODES = ("light", "dark")


def _tokens(mode: str) -> Mapping[str, Any]:
    from lex.lex_app.design_system.lex_tokens import TOKENS

    return TOKENS[mode]


def rfds_variables(mode: str) -> dict[str, str]:
    """RFDS's CSS variables for ``mode``, each set from a lex-app token.

    The keys are RFDS's own variable names; the values come from ``TOKENS[mode]``.
    """
    t = _tokens(mode)
    c = t["color"]
    bg, text, border, tag, status = c["bg"], c["text"], c["border"], c["tag"], c["status"]
    teal, navy = c["teal"], c["navy"]
    side, header = t["sidenav"], t["headerbar"]
    accent_fg = c["nav"]["active"]["fg"]
    return {
        # surfaces
        "--rfds-color-bg-canvas": bg["canvas"],
        "--rfds-color-surface-panel": bg["surface"],
        "--rfds-color-surface-raised": bg["surface"],
        "--rfds-color-surface-overlay": bg["surface"],
        "--rfds-color-surface-sunken": bg["surface2"],
        "--rfds-color-bg-subtle": bg["surface2"],
        "--rfds-color-bg-hover": bg["surfaceHover"],
        "--rfds-color-bg-pressed": bg["selected"],
        "--rfds-color-bg-readonly": bg["surface2"],
        "--rfds-color-bg-skeleton": bg["surfaceHover"],
        # text and icons
        "--rfds-color-text-default": text["primary"],
        "--rfds-color-text-muted": text["muted"],
        "--rfds-color-text-placeholder": text["muted"],
        "--rfds-color-text-link": text["link"],
        "--rfds-color-text-link-hover": text["heading"],
        "--rfds-color-icon-default": c["icon"]["default"],
        "--rfds-color-icon-muted": c["icon"]["default"],
        # lines
        "--rfds-color-border-subtle": border["soft"],
        "--rfds-color-border-default": border["line"],
        # accent: lex-app's teal for everything that marks or selects
        "--rfds-color-accent-solid": teal["500"],
        "--rfds-color-accent-solid-hover": teal["600"],
        "--rfds-color-accent-indicator": teal["500"],
        "--rfds-color-accent-fg": accent_fg,
        "--rfds-color-accent-bg": bg["selected"],
        "--rfds-color-accent-bg-hover": c["alpha"]["tealSelected"],
        "--rfds-color-accent-border": teal["500"],
        "--rfds-color-selection-bg": c["alpha"]["tealSelectedSoft"],
        "--rfds-color-selection-bg-hover": c["alpha"]["tealSelected"],
        "--rfds-color-selection-bar": teal["500"],
        "--rfds-color-selection-range-bg": c["alpha"]["tealSelectedSoft"],
        "--rfds-color-selection-range-border": teal["500"],
        "--rfds-color-focus-ring": teal["500"],
        # the chrome: lex-app's navy header and side navigation
        "--rfds-palette-brand-chrome": navy["700"],
        "--rfds-palette-brand-chrome-fg": header["fg"],
        "--rfds-color-chrome-bg": navy["700"],
        "--rfds-color-chrome-fg": header["fg"],
        "--rfds-color-chrome-fg-muted": side["dim"],
        "--rfds-color-chrome-hover": side["hover"],
        "--rfds-color-chrome-selected": side["activeBg"],
        "--rfds-color-chrome-border": side["border"],
        "--rfds-color-chrome-stripe": "transparent",
        # statuses: lex-app's tag palette
        "--rfds-color-status-danger-fg": tag["error"]["fg"],
        "--rfds-color-status-danger-bg": tag["error"]["bg"],
        "--rfds-color-status-danger-border": tag["error"]["bd"],
        "--rfds-color-status-danger-solid": status["error"],
        "--rfds-color-status-warning-fg": tag["warn"]["fg"],
        "--rfds-color-status-warning-bg": tag["warn"]["bg"],
        "--rfds-color-status-warning-border": tag["warn"]["bd"],
        "--rfds-color-status-warning-solid": status["warning"],
        "--rfds-color-status-success-fg": tag["success"]["fg"],
        "--rfds-color-status-success-bg": tag["success"]["bg"],
        "--rfds-color-status-success-border": tag["success"]["bd"],
        "--rfds-color-status-success-solid": teal["700"],
        "--rfds-color-status-info-fg": status["info"],
        "--rfds-color-status-info-solid": status["info"],
        "--rfds-color-status-neutral-fg": tag["neutral"]["fg"],
        "--rfds-color-status-neutral-bg": tag["neutral"]["bg"],
        "--rfds-color-status-neutral-border": tag["neutral"]["bd"],
        # tables
        "--rfds-component-table-header-bg": t["table"]["headBg"],
        "--rfds-component-table-header-fg": t["table"]["headFg"],
        "--rfds-component-table-row-separator": border["soft"],
        # charts: RFDS's own categorical palette stays -- it is checked for colour-blind
        # readers and read from a fixed table in the browser, never from these variables.
        "--rfds-color-chart-surface": bg["surface"],
        # type and shape
        "--rfds-font-family-sans": t["fontFamily"]["sans"],
        "--rfds-font-family-mono": t["fontFamily"]["mono"],
        "--rfds-radius-lg": t["radius"]["control"],
        "--rfds-radius-xl": t["radius"]["surface"],
    }


def accent_scale(mode: str) -> dict[str, str]:
    """The Radix ``indigo`` steps RFDS's accent reads, set from the LEX teal for ``mode``.

    Steps 1-7 (backgrounds and borders) keep RFDS's brand-derived tints; the solid, its hover,
    the accent text, the selection washes and the text on a solid are lex-app's own.
    """
    t = _tokens(mode)
    c = t["color"]
    teal, alpha = c["teal"], c["alpha"]
    dark = mode == "dark"
    return {
        "--indigo-8": teal["600"] if dark else teal["400"],
        "--indigo-9": teal["500"],
        "--indigo-10": teal["400"] if dark else teal["600"],
        "--indigo-11": teal["light"] if dark else teal["700"],
        "--indigo-12": teal["100"] if dark else teal["900"],
        "--indigo-a3": alpha["tealSelectedSoft"],
        "--indigo-a4": alpha["tealSelected"],
        "--indigo-a5": t["sidenav"]["activeHoverBg"],
        "--indigo-a11": teal["light"] if dark else teal["700"],
        "--indigo-contrast": c["text"]["onCta"],
        "--indigo-indicator": teal["500"],
        "--indigo-track": teal["500"],
        "--color-background": c["bg"]["canvas"],
        "--color-panel-solid": c["bg"]["surface"],
    }


#: RFDS variables RFDS re-declares on each Radix theme element (``.radix-themes`` and every
#: ``[data-accent-color]``) from the accent scale; :func:`lex_theme_css` forwards lex-app's value
#: to them there.
REDECLARED = (
    "--rfds-color-surface-panel", "--rfds-color-surface-overlay", "--rfds-color-text-link",
    "--rfds-color-text-link-hover", "--rfds-color-focus-ring", "--rfds-color-accent-solid",
    "--rfds-color-accent-solid-hover", "--rfds-color-accent-fg", "--rfds-color-accent-bg",
    "--rfds-color-accent-bg-hover", "--rfds-color-accent-border", "--rfds-color-selection-bg",
    "--rfds-color-selection-bg-hover", "--rfds-color-selection-bar", "--rfds-color-selection-range-bg",
    "--rfds-color-selection-range-border",
)


def _forward_name(name: str) -> str:
    return "--lex-v-" + name[len("--rfds-color-"):]


def lex_variables(mode: str) -> dict[str, str]:
    """lex-app's component values RFDS has no variable for, as ``--lex-*`` for :func:`component_rules`."""
    t = _tokens(mode)
    c = t["color"]
    side, header, gradient = t["sidenav"], t["headerbar"], t["gradient"]
    dark = mode == "dark"
    return {
        "--lex-header-bg": header["bg"],
        "--lex-header-shadow": header["shadow"],
        "--lex-sidenav-bg": side["bg"],
        "--lex-sidenav-text": side["text"],
        "--lex-sidenav-dim": side["dim"],
        "--lex-sidenav-label": side["label"],
        "--lex-sidenav-hover": side["hover"],
        "--lex-sidenav-active-bg": side["activeBg"],
        "--lex-sidenav-active-hover-bg": side["activeHoverBg"],
        "--lex-sidenav-accent": side["accent"],
        "--lex-sidenav-border": side["border"],
        "--lex-card-radius": t["card"]["radius"],
        "--lex-card-shadow": t["shadow"]["elevation1"],
        "--lex-card-shadow-hover": t["shadow"]["card"],
        # lex-app's primary button: the navy header gradient, white text; in dark, the teal one.
        "--lex-button-primary-bg": gradient["cta"] if dark else gradient["header"],
        "--lex-button-primary-fg": c["text"]["onCta"] if dark else c["text"]["onAction"],
        "--lex-button-radius": t["button"]["radius"],
        "--lex-button-weight": str(t["button"]["fontWeight"]),
        "--lex-button-shadow-hover": t["shadow"]["button"],
        "--lex-focus-ring": t["shadow"]["focusRing"],
        "--lex-heading": c["text"]["heading"],
    }




def component_rules() -> str:
    """The rules a variable cannot express: gradients, elevation, the primary button, the focus ring.

    First, RFDS's re-declared variables (:data:`REDECLARED`) take lex-app's value on every Radix
    theme element that would otherwise derive them from the accent scale.
    """
    forwarded = "\n".join(f"  {name}: var({_forward_name(name)});" for name in REDECLARED)
    return (f'html:root:root :is(.radix-themes, [data-accent-color="indigo"]) {{\n{forwarded}\n}}\n'
            + _RULES.strip())


_RULES = """
html:root:root .rfds-header { background: var(--lex-header-bg); box-shadow: var(--lex-header-shadow); }
html:root:root .rfds-side-nav { background: var(--lex-sidenav-bg); border-inline-end-color: var(--lex-sidenav-border); }
html:root:root .rfds-side-nav .rfds-side-nav-heading { color: var(--lex-sidenav-label); }
html:root:root .rfds-side-nav .rfds-side-nav-link { color: var(--lex-sidenav-text); border-radius: 0; }
html:root:root .rfds-side-nav .rfds-side-nav-link svg { color: var(--lex-sidenav-dim); }
html:root:root .rfds-side-nav .rfds-side-nav-link:hover { background-color: var(--lex-sidenav-hover); color: var(--lex-sidenav-text); }
html:root:root .rfds-side-nav .rfds-side-nav-link[aria-current="page"] {
  background-color: var(--lex-sidenav-active-bg); color: var(--lex-sidenav-text);
  box-shadow: inset 3px 0 0 var(--lex-sidenav-accent); }
html:root:root .rfds-side-nav .rfds-side-nav-link[aria-current="page"]:hover { background-color: var(--lex-sidenav-active-hover-bg); }
html:root:root .rfds-side-nav .rfds-side-nav-link[aria-current="page"] svg { color: var(--lex-sidenav-accent); }
html:root:root :is(.rfds-card, .rfds-kpi-tile).rt-Card {
  --card-border-radius: var(--lex-card-radius); border-radius: var(--lex-card-radius);
  --base-card-surface-box-shadow: var(--lex-card-shadow);
  --base-card-surface-hover-box-shadow: var(--lex-card-shadow-hover);
  --base-card-surface-active-box-shadow: var(--lex-card-shadow); }
html:root:root :is(h1, h2, h3, .rfds-page-title, .rfds-card-title) { color: var(--lex-heading); }
html:root:root .rfds-button.rt-Button { border-radius: var(--lex-button-radius); font-weight: var(--lex-button-weight);
  text-transform: none; transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
html:root:root .rfds-button.rt-Button.rt-variant-solid[data-accent-color="indigo"] {
  background: var(--lex-button-primary-bg); color: var(--lex-button-primary-fg); }
html:root:root .rfds-button.rt-Button:where(:not(:disabled)):hover { transform: translateY(-1px); box-shadow: var(--lex-button-shadow-hover); }
html:root:root .rfds-button.rt-Button:where(:not(:disabled)):active { transform: translateY(0) scale(0.98); box-shadow: none; }
html:root:root :is(.rfds-button, .rfds-icon-button, .rfds-text-field, .rfds-select):focus-visible {
  outline: none; box-shadow: var(--lex-focus-ring); }
html:root:root :is(.rfds-page-title, #rfds-page-title):focus-visible { outline: none; }
@media (prefers-reduced-motion: reduce) { html:root:root .rfds-button.rt-Button { transition: none; }
  html:root:root .rfds-button.rt-Button:hover { transform: none; } }
"""


def _block(selector: str, values: Mapping[str, str]) -> str:
    lines = "\n".join(f"  {name}: {value};" for name, value in values.items())
    return f"{selector} {{\n{lines}\n}}"


#: Where each mode's variables apply: the page's own mode on ``html``, and any region RFDS forces
#: into one (``rfds.forced_dark`` / ``forced_light`` mark it with the mode's class). The doubled
#: ``:root`` outranks RFDS's brand sheet (``:root:root``).
SELECTORS = {
    "light": "html:root:root, html:root:root .light, html:root:root .light-theme",
    "dark": "html:root:root.dark, html:root:root .dark, html:root:root .dark-theme",
}


def mode_variables(mode: str) -> dict[str, str]:
    """Every variable one mode's block sets: RFDS's, the accent scale, lex-app's, and the forwards."""
    rfds = rfds_variables(mode)
    return {**rfds, **accent_scale(mode), **lex_variables(mode),
            **{_forward_name(name): rfds[name] for name in REDECLARED}}


def lex_theme_css() -> str:
    """The stylesheet: both modes' variables, then lex-app's component rules."""
    blocks = [_block(SELECTORS[mode], mode_variables(mode)) for mode in MODES]
    return "/* The LEX look over RFDS -- generated from lex_tokens by lex.lex_app.reflex.theme. */\n" + "\n".join(
        [*blocks, component_rules()]
    )


__all__ = ["INTER_STYLESHEET", "MODES", "REDECLARED", "SELECTORS", "accent_scale", "component_rules",
           "lex_theme_css", "lex_variables", "mode_variables", "rfds_variables"]
