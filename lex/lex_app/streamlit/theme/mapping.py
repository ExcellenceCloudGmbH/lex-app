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
    """
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {_MODES}")

    brand = tokens["brand"]
    font = tokens["font"]
    radius = tokens["radius"]
    chart = tokens["chart"]
    m = tokens["modes"][mode]

    return {
        # Brand + surfaces
        "primaryColor": brand["primary"],
        "backgroundColor": m["background"],
        "secondaryBackgroundColor": m["secondary_background"],
        "textColor": m["text"],
        "borderColor": m["border"],
        "linkColor": m["link"],
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
        "codeBackgroundColor": m["code_background"],
        "dataframeHeaderBackgroundColor": m["dataframe_header_background"],
        "dataframeBorderColor": m["border"],
        # Charts
        "chartCategoricalColors": chart["categorical"],
        "chartSequentialColors": chart["sequential"],
        "chartDivergingColors": chart["diverging"],
        # Semantic ramps
        "greenColor": m["success"],
        "orangeColor": m["warning"],
        "redColor": m["error"],
        # Sidebar — navy in BOTH modes, matching lex-app's sidenav
        "sidebar.backgroundColor": brand["sidebar_bg"],
        "sidebar.textColor": brand["sidebar_text"],
        "sidebar.primaryColor": brand["primary"],
        "sidebar.borderColor": brand["sidebar_bg_end"],
    }
