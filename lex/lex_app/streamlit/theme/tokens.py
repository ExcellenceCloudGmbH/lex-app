"""LEX design tokens — VENDORED, DO NOT HAND-EDIT.

Provenance: transcribed from the lex-app frontend design system
(`@excellencecloudgmbh/lex-tokens`). Phase 4 of the design replaces this file
with output generated from the design system's `tokens.json` and adds a CI
drift check; until then it is the single Python-side source of truth.

Values verified against the frontend on 2026-07-30:
  brand teal        #14b4b4   (CustomSidebar NAV.teal)
  sidebar navy      #283C50 -> #1a2d3e gradient
  sidebar text      #dfe7ee   (NAV.text)
  dark surfaces     #0d1117 / #161b22, text #c9d1d9 (calculation-log dark mode)
"""
from __future__ import annotations

import hashlib
import json

TOKENS: dict = {
    "brand": {
        "primary": "#14b4b4",
        "primary_hover": "#0d9e9e",
        "sidebar_bg": "#283C50",
        "sidebar_bg_end": "#1a2d3e",
        "sidebar_text": "#dfe7ee",
    },
    "font": {
        "body": "Inter",
        "heading": "Inter",
        "code": "Fira Code",
        # The SAME stylesheet the lex-app frontend loads (see
        # lex/react/build/assets/*.css). Pointing Streamlit at the frontend's
        # own source is what keeps the two surfaces consistent: with network
        # access both render Inter, and in an air-gapped deployment both fall
        # back to `fallback` together. Bundling a woff2 for Streamlit alone was
        # deliberately rejected — see the design doc, section 8.2.
        "stylesheet_url": (
            "https://fonts.googleapis.com/css2"
            "?family=Inter:wght@300;400;500;600;700&display=swap"
        ),
        "code_stylesheet_url": (
            "https://fonts.googleapis.com/css2"
            "?family=Fira+Code:wght@400;500&display=swap"
        ),
        # Rendered after the webfont as a comma-separated fallback list, which
        # is the form Streamlit's font options accept.
        "fallback": "-apple-system, Segoe UI, sans-serif",
    },
    "radius": {
        "base": "12px",
        "control": "10px",
    },
    "chart": {
        # Brand-first categorical ramp; distinguishable in both modes.
        "categorical": ["#14b4b4", "#283C50", "#f2a33c", "#7b61ff", "#e2544b", "#3c9f5a"],
        # Streamlit requires EXACTLY 10 stops for the sequential and diverging
        # ramps (app_session._parse_and_populate_chart_colors, required_length=10)
        # and silently falls back to its own defaults otherwise.
        "sequential": [
            "#f0fbfb", "#d9f5f5", "#bfeded", "#a6e3e3", "#8ad9d9",
            "#5cc9c9", "#2fbdbd", "#14b4b4", "#109696", "#0d7a7a",
        ],
        "diverging": [
            "#c2382f", "#e2544b", "#ef8880", "#f7bdb8", "#fae4e2",
            "#e0f4f4", "#a6e3e3", "#5cc9c9", "#14b4b4", "#0d7a7a",
        ],
    },
    "modes": {
        "light": {
            "background": "#ffffff",
            "secondary_background": "#F6F8FA",
            "text": "#1F2328",
            "border": "#d0d7de",
            "link": "#0d9e9e",
            "code_background": "#F6F8FA",
            "dataframe_header_background": "#F6F8FA",
            "success": "#1a7f37",
            "warning": "#9a6700",
            "error": "#d1242f",
        },
        "dark": {
            "background": "#0d1117",
            "secondary_background": "#161b22",
            "text": "#c9d1d9",
            "border": "#30363d",
            "link": "#5cc9c9",
            "code_background": "#161b22",
            "dataframe_header_background": "#161b22",
            "success": "#3fb950",
            "warning": "#d29922",
            "error": "#f85149",
        },
    },
}

TOKENS_HASH: str = hashlib.sha256(
    json.dumps(TOKENS, sort_keys=True).encode("utf-8")
).hexdigest()
