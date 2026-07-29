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
        # NOT emitted into the native theme: Streamlit's `font` option takes a
        # family name and appends its own system fallback stack. This records
        # the intended chain for the CSS layer and for phase 3's handshake
        # payload, and is asserted by scenario 1.212b so it cannot rot.
        "fallback": "-apple-system, Segoe UI, sans-serif",
    },
    "radius": {
        "base": "12px",
        "control": "10px",
    },
    "chart": {
        # Brand-first categorical ramp; distinguishable in both modes.
        "categorical": ["#14b4b4", "#283C50", "#f2a33c", "#7b61ff", "#e2544b", "#3c9f5a"],
        "sequential": ["#e6f7f7", "#a6e3e3", "#5cc9c9", "#14b4b4", "#0d7a7a"],
        "diverging": ["#e2544b", "#f0a9a4", "#f2f2f2", "#a6e3e3", "#14b4b4"],
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
