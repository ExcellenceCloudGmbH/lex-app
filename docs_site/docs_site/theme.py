"""The site's colours: one token set per colour mode, the only place a colour value is written.

The values are the approved design's palette. ``VARS`` turns them into CSS custom properties on the
frame (``--docs-<token>``), the light set by default and the dark one under Reflex's ``.dark`` class
on ``<html>``, so every rule in the frame says ``var(--docs-...)`` and follows the colour mode by
itself. Everything a lex.ui demo draws keeps RFDS's own (Radix) tokens.

Callout and status tones meet 4.5:1 for their text on their background in both modes.
"""

LIGHT: dict[str, str] = {
    "bg": "#FFFFFF", "side": "#FAFBFC", "header": "rgba(255,255,255,0.86)", "surface": "#FFFFFF",
    "sunk": "#F7F9FB", "hover": "#EEF2F6",
    "ink": "#1F2630", "muted": "#5B6475", "item-ink": "#3D4757", "border": "#E3E8EE",
    "link": "#0B7F7F", "link-hover": "#095F5F", "accent": "#14B4B4", "on-accent": "#FFFFFF",
    "pill-bg": "#E6F6F6", "pill-ink": "#0A6B6B",
    "code-bg": "#F7F9FB", "code-ink": "#24292F", "kw": "#8250DF", "fn": "#0550AE", "str": "#0A6B4F",
    "on-bg": "#FFFFFF", "on-ink": "#1F2630",
    # callouts and status messages: info, success, warning, danger
    "info-bg": "#EEF6FA", "info-border": "#C9E2EE", "info-ink": "#134A63",
    "success-bg": "#E8F7F5", "success-border": "#B5E3DD", "success-ink": "#0A5F57",
    "warning-bg": "#FDF5E1", "warning-border": "#F0DCA6", "warning-ink": "#6E4A00",
    "danger-bg": "#FDECEC", "danger-border": "#F2C4C4", "danger-ink": "#8A1F1F",
}

DARK: dict[str, str] = {
    "bg": "#0E141B", "side": "#0C1218", "header": "rgba(14,20,27,0.86)", "surface": "#151E28",
    "sunk": "#111922", "hover": "#1C2734",
    "ink": "#E6EDF3", "muted": "#9AA7B5", "item-ink": "#C3CDD8", "border": "#243243",
    "link": "#4FD1D1", "link-hover": "#86E3E3", "accent": "#14B4B4", "on-accent": "#FFFFFF",
    "pill-bg": "#12343A", "pill-ink": "#86E3E3",
    "code-bg": "#0B1117", "code-ink": "#D6DEE6", "kw": "#C9A7FF", "fn": "#7CC4FF", "str": "#86E3A6",
    "on-bg": "#243243", "on-ink": "#E6EDF3",
    "info-bg": "#0F2430", "info-border": "#1E4659", "info-ink": "#B7DDEE",
    "success-bg": "#0F2E2B", "success-border": "#1D5550", "success-ink": "#8BE3D4",
    "warning-bg": "#2E2412", "warning-border": "#5A4818", "warning-ink": "#F2D58A",
    "danger-bg": "#331A1C", "danger-border": "#5C2B2F", "danger-ink": "#F2B8BC",
}

TONES = ("info", "success", "warning", "danger")


def var(token: str) -> str:
    """``var(--docs-<token>)``; a token that is not in the palette is a mistake, so it raises."""
    if token not in LIGHT:
        raise KeyError(f"no colour token {token!r} in docs_site.theme")
    return f"var(--docs-{token})"


def _props(palette: dict[str, str]) -> dict[str, str]:
    return {f"--docs-{name}": value for name, value in palette.items()}


#: the frame's style: the light tokens, and the dark ones while Reflex's colour mode is dark
VARS: dict = {**_props(LIGHT), ".dark &": _props(DARK)}

#: Prism token colours for rx.code_block (react-syntax-highlighter): the design's keyword, function
#: and string colours, through the tokens, so one theme serves both modes
CODE_THEME: dict = {
    'code[class*="language-"]': {"color": var("code-ink"), "background": "none"},
    'pre[class*="language-"]': {"color": var("code-ink"), "background": "none"},
    "comment": {"color": var("muted"), "fontStyle": "italic"},
    "prolog": {"color": var("muted")},
    "keyword": {"color": var("kw")},
    "boolean": {"color": var("kw")},
    "important": {"color": var("kw")},
    "atrule": {"color": var("kw")},
    "function": {"color": var("fn")},
    "class-name": {"color": var("fn")},
    "decorator": {"color": var("fn")},
    "builtin": {"color": var("fn")},
    "number": {"color": var("fn")},
    "constant": {"color": var("fn")},
    "property": {"color": var("fn")},
    "attr-name": {"color": var("fn")},
    "tag": {"color": var("kw")},
    "string": {"color": var("str")},
    "triple-quoted-string": {"color": var("str")},
    "string-interpolation": {"color": var("str")},
    "char": {"color": var("str")},
    "attr-value": {"color": var("str")},
    "url": {"color": var("str")},
    "regex": {"color": var("str")},
    "annotation": {"color": var("fn")},
    # operators and punctuation keep the code's own colour: Prism aliases a decorator to
    # "punctuation", and a colour there would paint over the decorator's
}
