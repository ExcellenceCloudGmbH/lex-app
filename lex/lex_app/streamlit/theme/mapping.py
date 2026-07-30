"""Pure mapping: LEX design tokens -> Streamlit native theme keys.

No Streamlit import. Keys are the flat dotted names Streamlit's config uses
(`primaryColor`, `sidebar.backgroundColor`, ...), which is also exactly what
the `--theme.<key>` CLI flags accept.
"""
from __future__ import annotations

_MODES = ("light", "dark")


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
        # Typography
        "font": font["body"],
        "headingFont": font["heading"],
        "codeFont": font["code"],
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
