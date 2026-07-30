"""Pure mapping: LEX design tokens -> Streamlit native theme keys.

No Streamlit import. Keys are the flat dotted names Streamlit's config uses
(`primaryColor`, `sidebar.backgroundColor`, ...), which is also exactly what
the `--theme.<key>` CLI flags accept.
"""
from __future__ import annotations

_MODES = ("light", "dark")

# Theme keys Streamlit accepts ONLY at the top-level `[theme]` scope — they
# have no `theme.light.*` / `theme.dark.*` twin. The config writer must not
# place these inside a per-mode block: Streamlit rejects unrecognised config
# options outright. Verified against the installed Streamlit by scenario
# 1.209b, which fails if this set drifts from reality.
GLOBAL_ONLY_KEYS: frozenset = frozenset(
    {
        "chartCategoricalColors",
        "chartDivergingColors",
        "chartSequentialColors",
        "showSidebarBorder",
    }
)


def _font_ref(family: str, stylesheet_url: str, fallback: str) -> str:
    """Build Streamlit's ``"<family>:<url>, <fallback>"`` font reference.

    Streamlit splits on the FIRST colon to separate the family name from the
    stylesheet URL, so the URL's own colons (``https:``, ``Inter:wght``) are
    safe. Fallbacks follow as a comma-separated list.
    """
    return f"{family}:{stylesheet_url}, {fallback}"


def build_streamlit_theme(tokens: dict, mode: str) -> dict:
    """Return the flat Streamlit theme mapping for ``mode``.

    Raises:
        ValueError: if ``mode`` is not "light" or "dark".
        KeyError: if ``tokens`` is missing a section or leaf this mapping
            reads (the caller passes the vendored ``TOKENS`` constant).
    """
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {_MODES}")

    brand = tokens["brand"]
    font = tokens["font"]
    radius = tokens["radius"]
    chart = tokens["chart"]
    mode_tokens = tokens["modes"][mode]

    return {
        # Brand + surfaces
        "primaryColor": brand["primary"],
        "backgroundColor": mode_tokens["background"],
        "secondaryBackgroundColor": mode_tokens["secondary_background"],
        "textColor": mode_tokens["text"],
        "borderColor": mode_tokens["border"],
        "linkColor": mode_tokens["link"],
        "linkUnderline": False,
        "showWidgetBorder": True,
        "showSidebarBorder": False,
        # Typography — Streamlit accepts "<family>:<stylesheet url>" plus a
        # comma-separated fallback list. We reference the frontend's own
        # Google Fonts stylesheet so both surfaces load (or fail to load) the
        # same faces. See the design doc section 8.2.
        "font": _font_ref(font["body"], font["stylesheet_url"], font["fallback"]),
        "headingFont": _font_ref(font["heading"], font["stylesheet_url"], font["fallback"]),
        "codeFont": _font_ref(font["code"], font["code_stylesheet_url"], "monospace"),
        # Shape
        "baseRadius": radius["base"],
        "buttonRadius": radius["control"],
        # Code + data surfaces
        "codeBackgroundColor": mode_tokens["code_background"],
        "dataframeHeaderBackgroundColor": mode_tokens["dataframe_header_background"],
        "dataframeBorderColor": mode_tokens["border"],
        # Charts
        "chartCategoricalColors": chart["categorical"],
        "chartSequentialColors": chart["sequential"],
        "chartDivergingColors": chart["diverging"],
        # Semantic ramps
        "greenColor": mode_tokens["success"],
        "orangeColor": mode_tokens["warning"],
        "redColor": mode_tokens["error"],
        # Sidebar — navy in BOTH modes, matching lex-app's sidenav
        "sidebar.backgroundColor": brand["sidebar_bg"],
        "sidebar.textColor": brand["sidebar_text"],
        "sidebar.primaryColor": brand["primary"],
        "sidebar.borderColor": brand["sidebar_bg_end"],
    }
