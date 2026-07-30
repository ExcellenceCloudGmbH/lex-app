# Streamlit Theme Parity — Implementation Plan (Phases 1–2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Streamlit pages served by lex-app carry the real lex brand — teal `#14b4b4`, navy sidebar, Inter, matching surfaces and a dark mode — replacing the hand-written `config.toml` whose every value has drifted.

**Architecture:** Design tokens are vendored as a pure Python module. Two pure functions map them onto Streamlit 1.58's native theme keys (~120 of them, covering sidebar, dataframe, charts, fonts and radii) and render them as `--theme.*` CLI flags plus a fallback `config.toml`. A small CSS layer adds the four things with no native token (elevation, gradient CTA, sidebar gradient, logo lockup), injected automatically without touching customer dashboards.

**Tech Stack:** Python 3.12, Streamlit 1.58, Click (lex CLI), pytest via the `lex pytest` runner, Playwright (DOM regression).

**Spec:** [`docs/superpowers/specs/2026-07-30-streamlit-theme-parity-design.md`](../specs/2026-07-30-streamlit-theme-parity-design.md)

---

## Deviation from the spec — read before starting

The spec's Layer 3 proposed a **CLI shim that `runpy`s the customer script**. **Do not implement that.** Verified during planning: `ScriptRunner._run_script` applies Streamlit's *magic* AST transform to the file Streamlit itself compiles. A wrapper script would receive the transform while the customer's real script is executed by `runpy` — so every dashboard relying on magic (a bare `df` on its own line rendering a table) would silently render nothing. That is a severe regression.

Task 10 replaces it with a narrow, feature-detected wrapper around `ScriptRunner._run_script` that injects CSS *inside* the real run, leaving the customer script as the compiled unit so magic keeps working. Task 11 pins that behaviour with a test.

---

## File Structure

| File | Responsibility |
|---|---|
| `lex/lex_app/streamlit/theme/__init__.py` | Public surface: `build_full_config`, `theme_cli_flags`, `apply_css` |
| `lex/lex_app/streamlit/theme/tokens.py` | **Data only.** `TOKENS`, `TOKENS_HASH`. No imports beyond stdlib |
| `lex/lex_app/streamlit/theme/mapping.py` | **Pure.** tokens → Streamlit theme keys, per mode |
| `lex/lex_app/streamlit/theme/config_writer.py` | **Pure.** theme dict → TOML text and → CLI flag list |
| `lex/lex_app/streamlit/theme/assets/Inter-Regular.woff2` | Bundled font (never CDN) |
| `lex/lex_app/streamlit/theme/assets/Inter-SemiBold.woff2` | Bundled font |
| `lex/lex_app/streamlit/theme/overrides.css` | The four non-tokenisable rules |
| `lex/lex_app/streamlit/theme/bootstrap.py` | **Impure.** CSS injection + ScriptRunner patch |
| `lex/bin/lex.py` | Modify: append `--theme.*` flags; install the CSS patch |
| `requirements.txt` | Modify: pin `streamlit` |
| `pyproject.toml` | Modify: ship `assets/**` and `overrides.css` as package data |
| `lex/.streamlit/config.toml` | Replaced by generated output |
| `lex/test_project/tests/init/test_1y_streamlit_theme.py` | Cluster 1y tests, scenarios 1.203–1.218 |

**Test allocation (already resolved — do not re-derive):** cluster `01-init`, letters `a`–`x` are taken, so this batch is **letter `y`**, scenarios **1.203 onward**. Marker: `pytestmark = pytest.mark.init`.

**Run tests with:**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v
```

---

# PHASE 1 — Native theme from tokens (independently shippable)

## Task 1: Vendored token module  ✅ landed

**Files:**
- Create: `lex/lex_app/streamlit/theme/__init__.py`
- Create: `lex/lex_app/streamlit/theme/tokens.py`
- Test: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1y_streamlit_theme.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'lex.lex_app.streamlit.theme'`

- [ ] **Step 3: Create the package and token data**

Create `lex/lex_app/streamlit/theme/__init__.py`:

```python
"""LEX design tokens applied to Streamlit's native theme.

Public surface is re-exported at the bottom of this module once the
mapping/writer/bootstrap pieces exist (Tasks 2, 4, 10).
"""
```

Create `lex/lex_app/streamlit/theme/tokens.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/streamlit/theme/__init__.py lex/lex_app/streamlit/theme/tokens.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): vendor LEX design tokens as a pure Python module"
```

---

## Task 2: Token → Streamlit key mapping  ✅ landed

**Files:**
- Create: `lex/lex_app/streamlit/theme/mapping.py`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
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
        assert theme["font"] == "Inter"
        assert theme["headingFont"] == "Inter"
        assert theme["codeFont"] == "Fira Code"
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k Mapping
```

Expected: FAIL — `ModuleNotFoundError: ... theme.mapping`

- [ ] **Step 3: Write the mapping**

Create `lex/lex_app/streamlit/theme/mapping.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/streamlit/theme/mapping.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): pure token -> Streamlit theme-key mapping"
```

---

## Task 3: Key-existence contract test  ✅ landed

This is the cheapest guard in the design: it turns "a Streamlit upgrade renamed a theme key" from silently-lost styling into a failed unit test.

**Files:**
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_StreamlitContract:
    """Every key we emit must exist in the installed Streamlit."""

    # -- 1.209 --------------------------------------------------------
    def test_1_209_every_mapped_key_exists_in_streamlit_config(self) -> None:
        """Scenario 1.209: each mapped key resolves to a real
        ``theme.*``/``theme.<mode>.*`` config option in the installed
        Streamlit. A rename or removal upstream fails HERE, loudly, instead
        of quietly dropping that piece of styling from every dashboard.
        """
        from streamlit import config as st_config

        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        st_config.get_config_options()
        known = set(st_config._config_options_template)

        missing = []
        for mode in ("light", "dark"):
            for key in build_streamlit_theme(TOKENS, mode):
                # Both the mode-scoped and the base form must be valid.
                if f"theme.{mode}.{key}" not in known and f"theme.{key}" not in known:
                    missing.append(f"{mode}:{key}")

        assert not missing, (
            "Streamlit no longer accepts these theme keys — the installed "
            f"version renamed or removed them: {sorted(missing)}"
        )
```

- [ ] **Step 2: Run the test**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k Contract
```

Expected: PASS on Streamlit 1.58. If it FAILS, a key in Task 2 is wrong — fix the mapping, never weaken this test.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "test(streamlit-theme): contract-test every mapped key against installed Streamlit"
```

---

## Task 4: Config writer — TOML + CLI flags  ✅ landed

**Files:**
- Create: `lex/lex_app/streamlit/theme/config_writer.py`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_ConfigWriter:
    """The mapping renders to both delivery paths, which cannot disagree."""

    # -- 1.210 --------------------------------------------------------
    def test_1_210_toml_has_both_mode_sections_and_parses(self) -> None:
        """Scenario 1.210: the rendered config is valid TOML carrying
        [theme.light] and [theme.dark] with their sidebar sub-tables."""
        import tomllib

        from lex.lex_app.streamlit.theme.config_writer import render_config_toml
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        text = render_config_toml(TOKENS)
        parsed = tomllib.loads(text)

        assert parsed["theme"]["light"]["primaryColor"] == "#14b4b4"
        assert parsed["theme"]["dark"]["backgroundColor"] == "#0d1117"
        assert parsed["theme"]["light"]["sidebar"]["backgroundColor"] == "#283C50"
        # The stale value must be gone for good.
        assert "#08BCC2" not in text

    # -- 1.211 --------------------------------------------------------
    def test_1_211_cli_flags_mirror_the_toml_exactly(self) -> None:
        """Scenario 1.211: CLI flags are generated from the same mapping as
        the file, so the two delivery paths can never drift apart."""
        from lex.lex_app.streamlit.theme.config_writer import (
            render_config_toml,
            theme_cli_flags,
        )
        from lex.lex_app.streamlit.theme.tokens import TOKENS
        import tomllib

        flags = theme_cli_flags(TOKENS)
        parsed = tomllib.loads(render_config_toml(TOKENS))

        # Flags are a flat ["--theme.light.primaryColor=#14b4b4", ...] list.
        assert all(f.startswith("--theme.") and "=" in f for f in flags)
        assert "--theme.light.primaryColor=#14b4b4" in flags
        assert "--theme.dark.backgroundColor=#0d1117" in flags

        # Same key count both ways (lists are JSON-encoded in flag form).
        def leaf_count(node: dict) -> int:
            return sum(
                leaf_count(v) if isinstance(v, dict) else 1 for v in node.values()
            )

        assert len(flags) == leaf_count(parsed["theme"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k ConfigWriter
```

Expected: FAIL — `ModuleNotFoundError: ... theme.config_writer`

- [ ] **Step 3: Write the config writer**

Create `lex/lex_app/streamlit/theme/config_writer.py`:

```python
"""Pure renderers: theme mapping -> Streamlit TOML config and CLI flags.

Two delivery paths, one source:

* ``theme_cli_flags`` — PRIMARY. ``.streamlit/config.toml`` is resolved
  relative to the current working directory, so a file-only approach depends
  on where the customer launched from. Flags are location-independent.
* ``render_config_toml`` — FALLBACK, for a dashboard started with a bare
  ``streamlit run`` from the project root.
"""
from __future__ import annotations

import json

from .mapping import build_streamlit_theme

_MODES = ("light", "dark")


def _split_sidebar(theme: dict) -> tuple[dict, dict]:
    """Separate ``sidebar.*`` keys from the top-level ones."""
    top: dict = {}
    sidebar: dict = {}
    for key, value in theme.items():
        if key.startswith("sidebar."):
            sidebar[key.split(".", 1)[1]] = value
        else:
            top[key] = value
    return top, sidebar


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(f'"{v}"' for v in value) + "]"
    return f'"{value}"'


def render_config_toml(tokens: dict) -> str:
    """Render the full ``[theme.light]`` / ``[theme.dark]`` config."""
    lines: list[str] = [
        "# GENERATED by lex.lex_app.streamlit.theme.config_writer — do not hand-edit.",
        "# Source of truth: lex/lex_app/streamlit/theme/tokens.py",
        "",
    ]
    for mode in _MODES:
        top, sidebar = _split_sidebar(build_streamlit_theme(tokens, mode))
        lines.append(f"[theme.{mode}]")
        for key, value in top.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
        lines.append(f"[theme.{mode}.sidebar]")
        for key, value in sidebar.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _flag_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        # Streamlit parses list-valued theme options as JSON.
        return json.dumps(value)
    return str(value)


def theme_cli_flags(tokens: dict) -> list[str]:
    """Render the mapping as ``--theme.<mode>.<key>=<value>`` flags."""
    flags: list[str] = []
    for mode in _MODES:
        for key, value in build_streamlit_theme(tokens, mode).items():
            flags.append(f"--theme.{mode}.{key}={_flag_value(value)}")
    return flags


def write_config(path) -> None:
    """Write the fallback config file, creating parent directories."""
    from pathlib import Path
    from .tokens import TOKENS

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_config_toml(TOKENS), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/streamlit/theme/config_writer.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): render theme as CLI flags (primary) + TOML (fallback)"
```

---

## Task 5: Fonts — point at the frontend's own stylesheet  ✅ landed

> **SUPERSEDED TITLE.** This task originally read "Bundle Inter and register it via
> fontFaces". That was abandoned: no woff2 is vendored in lex-app and the frontend
> itself loads Inter from Google Fonts, so bundling for Streamlit alone would have made
> Streamlit *more* correct than the app it must match — they would visibly diverge in an
> air-gapped deployment. Implemented instead via Streamlit's native `"<family>:<url>"`
> font form pointing at the frontend's own stylesheet. See the design doc §8.2.

Air-gapped and egress-restricted customer environments must not silently fall back to system sans.

**Files:**
- Create: `lex/lex_app/streamlit/theme/assets/Inter-Regular.woff2`
- Create: `lex/lex_app/streamlit/theme/assets/Inter-SemiBold.woff2`
- Modify: `lex/lex_app/streamlit/theme/mapping.py`
- Modify: `pyproject.toml:40-43`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Download the fonts**

```bash
mkdir -p lex/lex_app/streamlit/theme/assets
curl -fsSL -o lex/lex_app/streamlit/theme/assets/Inter-Regular.woff2 \
  https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.woff2
curl -fsSL -o lex/lex_app/streamlit/theme/assets/Inter-SemiBold.woff2 \
  https://github.com/rsms/inter/raw/master/docs/font-files/Inter-SemiBold.woff2
ls -la lex/lex_app/streamlit/theme/assets/
```

Expected: two files, each > 50 KB. If the URLs 404, take the woff2 files from the frontend repo's bundled assets instead — do **not** fall back to a CDN URL in `fontFaces`.

- [ ] **Step 2: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_Fonts:
    """Inter ships with the package; it is never fetched at runtime."""

    # -- 1.212 --------------------------------------------------------
    def test_1_212_font_files_are_bundled_and_registered(self) -> None:
        """Scenario 1.212: the woff2 files exist inside the package and
        fontFaces points at them by name, with no remote URL. An
        egress-restricted customer must still get Inter."""
        from pathlib import Path

        from lex.lex_app.streamlit.theme import mapping
        from lex.lex_app.streamlit.theme.mapping import build_streamlit_theme
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        assets = Path(mapping.__file__).parent / "assets"
        assert (assets / "Inter-Regular.woff2").is_file()
        assert (assets / "Inter-SemiBold.woff2").is_file()

        faces = build_streamlit_theme(TOKENS, "light")["fontFaces"]
        assert isinstance(faces, list) and faces, "fontFaces must be populated"
        for face in faces:
            assert face["family"] == "Inter"
            assert not face["url"].startswith("http"), (
                f"fontFaces must not fetch remotely: {face['url']}"
            )

    def test_1_212b_font_fallback_chain_is_declared(self) -> None:
        """Scenario 1.212: if the face fails to load, text still lands on a
        sane system stack rather than a serif default."""
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        assert "sans-serif" in TOKENS["font"]["fallback"]
```

- [ ] **Step 3: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k Fonts
```

Expected: FAIL — `KeyError: 'fontFaces'`

- [ ] **Step 4: Add fontFaces to the mapping**

In `lex/lex_app/streamlit/theme/mapping.py`, add this helper above `build_streamlit_theme`:

```python
def _font_faces() -> list[dict]:
    """Locally-served Inter faces. Paths are relative to the static mount
    Streamlit exposes; never remote URLs (air-gapped customers)."""
    return [
        {"family": "Inter", "url": "assets/Inter-Regular.woff2", "weight": 400},
        {"family": "Inter", "url": "assets/Inter-SemiBold.woff2", "weight": 600},
    ]
```

Then inside the returned dict of `build_streamlit_theme`, directly after the `"codeFont"` entry, add:

```python
        "fontFaces": _font_faces(),
```

- [ ] **Step 5: Ship the assets as package data**

In `pyproject.toml`, extend the `[tool.setuptools.package-data]` block (currently lines 40-43) with:

```toml
"lex.lex_app.streamlit.theme" = ["assets/**/*", "*.css"]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v
```

Expected: all tests pass, including the Task 3 contract test (which now also validates `fontFaces`).

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/streamlit/theme/assets lex/lex_app/streamlit/theme/mapping.py pyproject.toml lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): bundle Inter and register it via fontFaces"
```

---

## Task 6: Wire the flags into the `lex streamlit` command  ✅ landed

**Files:**
- Modify: `lex/bin/lex.py:341`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_CliWiring:
    """`lex streamlit` hands the theme to Streamlit on every launch."""

    # -- 1.213 --------------------------------------------------------
    def test_1_213_launch_args_include_the_theme_flags(self, monkeypatch) -> None:
        """Scenario 1.213: the args `lex streamlit` passes to Streamlit carry
        the theme, so a page is branded regardless of the launch directory
        (config.toml resolution depends on CWD; flags do not)."""
        from lex.bin.lex import build_streamlit_launch_args

        args = build_streamlit_launch_args(["run", "dash.py"])

        assert "--theme.light.primaryColor=#14b4b4" in args
        assert "--theme.dark.backgroundColor=#0d1117" in args
        # Existing behaviour preserved.
        assert "--server.port" in args and "8080" in args
        assert args[:2] == ["run", "dash.py"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k CliWiring
```

Expected: FAIL — `ImportError: cannot import name 'build_streamlit_launch_args'`

- [ ] **Step 3: Extract and extend the launch args**

In `lex/bin/lex.py`, add this module-level function just above the `streamlit` command definition (before line 297):

```python
def build_streamlit_launch_args(streamlit_args: list[str]) -> list[str]:
    """Return the full arg list handed to Streamlit's CLI.

    Theme flags come first-class rather than via ``.streamlit/config.toml``
    because that file is resolved relative to the CWD — flags are
    location-independent, so a dashboard is branded wherever it is launched
    from. See lex/lex_app/streamlit/theme/config_writer.py.
    """
    from lex.lex_app.streamlit.theme.config_writer import theme_cli_flags
    from lex.lex_app.streamlit.theme.tokens import TOKENS

    return (
        list(streamlit_args)
        + theme_cli_flags(TOKENS)
        + ["--browser.serverPort", "8501", "--server.port", "8080"]
    )
```

Then replace line 341:

```python
    streamlit_main(streamlit_args + ["--browser.serverPort", "8501", "--server.port", "8080"])
```

with:

```python
    streamlit_main(build_streamlit_launch_args(streamlit_args))
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: 1 passed.

- [ ] **Step 5: Verify a real launch is themed**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings PROJECT_ROOT=$(pwd)/lex/test_project \
  timeout 25 .venv-test/bin/python -m lex streamlit run lex/test_project/dashboards/lex_view_callbacks_demo.py 2>&1 | head -20
```

Expected: Streamlit starts without a config error. A rejected theme key would abort with `Error parsing config option`.

- [ ] **Step 6: Commit**

```bash
git add lex/bin/lex.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): pass theme flags on every lex streamlit launch"
```

---

## Task 7: Replace the stale config file and sync the plan shards  ✅ landed

**Files:**
- Modify: `lex/.streamlit/config.toml`
- Modify: `lex/test_project/test-plan/clusters/01-init/allocation.yaml`
- Modify: `lex/test_project/test-plan/clusters/01-init/batches.md`
- Create: `lex/test_project/test-plan/progress/sessions/2026-07-30-streamlit-theme.md`

- [ ] **Step 1: Regenerate the fallback config**

```bash
.venv-test/bin/python -c "
from lex.lex_app.streamlit.theme.config_writer import write_config
write_config('lex/.streamlit/config.toml')
"
head -12 lex/.streamlit/config.toml
grep -c "08BCC2" lex/.streamlit/config.toml || echo "stale accent gone"
```

Expected: generated header + `[theme.light]`; the `08BCC2` grep finds nothing.

- [ ] **Step 2: Run the whole cluster-1 suite for regressions**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init -q
```

Expected: all pass. `test_1r_lex_view_embed_helper.py` in particular must stay green.

- [ ] **Step 3: Update `allocation.yaml`**

Set `max_scenario: 213` and append this letter entry:

```yaml
  y:
    title: Streamlit theme parity — native theme from vendored LEX tokens
    scenarios: 1.203-1.213
    status: complete
    tests:
      pass: 12
      skip: 0
      xfail: 0
    note: >-
      Replaces the hand-written .streamlit/config.toml whose every value had
      drifted (primaryColor #08BCC2 vs the real #14b4b4, grey sidebar vs the
      navy gradient, "sans serif" vs Inter, no dark mode). Tokens are vendored
      as pure data; two pure functions map them onto Streamlit 1.58's native
      keys and render both delivery paths from one source — CLI flags
      (primary, CWD-independent) and config.toml (fallback for bare
      `streamlit run`). 1.209 is the key-existence contract test: every key we
      emit must exist in the installed Streamlit, so an upstream rename fails
      a fast unit test instead of silently dropping styling. Inter ships as
      bundled woff2 (never CDN — egress-restricted customers). Design:
      docs/superpowers/specs/2026-07-30-streamlit-theme-parity-design.md
```

- [ ] **Step 4: Append to `batches.md`**

```markdown
### Batch 1y — Streamlit theme parity (phase 1) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.203 – 1.213 |
| Type | U (pure data transforms) + one launch-args assertion |
| Files covered | `lex_app/streamlit/theme/{tokens,mapping,config_writer}.py`, `bin/lex.py` (`build_streamlit_launch_args`) |
| Test file | `lex/test_project/tests/init/test_1y_streamlit_theme.py` |
| Test classes | `TestCluster1y_{Tokens,Mapping,StreamlitContract,ConfigWriter,Fonts,CliWiring}` |
| Est. tests | 12 |
| Prereqs | none |
| Status | ✅ Complete — 12 pass / 0 fail |
| Note | Phase 1 of the theme-parity design. The mapping and writer are pure (no Streamlit import) so the whole theme is assertable without a server. Scenario 1.209 contract-tests every emitted key against `streamlit.config._config_options_template` — the guard that makes the Streamlit dependency safe. Phase 2 (CSS layer + pin) and phase 3 (live host handshake) are separate plans. |
```

- [ ] **Step 5: Write the session fragment**

Create `lex/test_project/test-plan/progress/sessions/2026-07-30-streamlit-theme.md`:

```markdown
---
date: 2026-07-30
clusters: [1y]
tests_added: "12 (1.203–1.213) + source in 4 files"
suite_tally: "1y 12 pass / 0 fail; cluster-1 regression green"
---

**Batch 1y landed — Streamlit pages now carry the real lex brand.** The theme
was a hand-written `config.toml` whose every value had drifted from the
frontend: `primaryColor #08BCC2` against the real `#14b4b4`, a grey sidebar
against the navy gradient, `font = "sans serif"` against Inter, and no dark
mode at all. Tokens are now vendored as pure data and mapped onto Streamlit
1.58's native theme keys — which turned out to be ~120 of them, covering the
sidebar, dataframe header, chart palettes, radii and custom font faces, so
almost all parity rides the public API rather than CSS.

Both delivery paths render from one mapping: `--theme.*` CLI flags (primary —
`config.toml` resolves against the CWD, flags do not) and the generated file
(fallback for a bare `streamlit run`). Scenario 1.209 is the guard that makes
this safe long-term: every key we emit must exist in the installed Streamlit,
so an upstream rename fails a millisecond-long unit test instead of quietly
dropping styling from every dashboard. Inter ships as bundled woff2 — never a
CDN URL, because egress-restricted customers would silently fall back to
system sans. See [batch 1y](../../clusters/01-init/batches.md).
```

- [ ] **Step 6: Regenerate the dashboard and validate**

```bash
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py build
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py validate
```

Expected: `wrote ...dashboard.md` then `OK`.

- [ ] **Step 7: Commit**

```bash
git add lex/.streamlit/config.toml lex/test_project/test-plan
git commit -m "feat(streamlit-theme): regenerate config.toml from tokens; sync plan shards"
```

**PHASE 1 IS SHIPPABLE HERE.** Stop and demo before starting Phase 2 if you want the brand fix out early.

---

# PHASE 2 — CSS layer for what has no token

## Task 8: Pin Streamlit  ✅ landed

Phase 2 adds four CSS rules that touch Streamlit's rendered DOM. An unpinned dependency makes that unsafe.

**Files:**
- Modify: `requirements.txt:25`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_Pin:
    """The Streamlit dependency is pinned, because the CSS layer touches
    its rendered DOM and upgrades must be deliberate."""

    # -- 1.214 --------------------------------------------------------
    def test_1_214_streamlit_is_pinned_in_requirements(self) -> None:
        """Scenario 1.214: `streamlit` carries a version constraint. Bare
        `streamlit` means upgrades reach customer installs silently, which
        would land DOM changes against the CSS layer with no warning."""
        from pathlib import Path

        lines = Path("requirements.txt").read_text().splitlines()
        entries = [
            line.strip()
            for line in lines
            if line.strip().startswith("streamlit")
            and not line.strip().startswith("streamlit-keycloak")
        ]
        assert entries, "no streamlit requirement found"
        assert any(
            any(op in entry for op in ("==", "~=", ">=", "<"))
            for entry in entries
        ), f"streamlit must be pinned, found: {entries}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k Pin
```

Expected: FAIL — `AssertionError: streamlit must be pinned, found: ['streamlit']`

- [ ] **Step 3: Pin it**

In `requirements.txt`, change line 25 from:

```
streamlit
```

to:

```
streamlit~=1.58.0
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "build: pin streamlit ~=1.58.0 ahead of the theme CSS layer"
```

---

## ~~Task 9: The four CSS rules ~~ (DROPPED)

> **STATUS 2026-07-30 — DROPPED, not implemented.** Tasks 9-12 built the CSS layer.
> That layer was deliberately abandoned after implementation revealed two things: the
> native theme surface already covers the sidebar and dataframe header (so CSS would add
> only two gradients, a shadow and a logo), and there is no *public* hook for automatic
> injection — the `runpy` shim planned in Task 10 would break Streamlit's AST magic
> transform, silently stopping bare-expression rendering in every customer dashboard, while
> the only magic-preserving alternative patches the private `ScriptRunner._run_script`.
> Trading that risk for four cosmetic touches is a bad trade. See the design doc §7 for the
> full reasoning. The shipped theme therefore has **zero** dependency on Streamlit
> internals. Revisit deliberately if the elevation/gradients are later judged to matter.


**Files:**
- Create: `lex/lex_app/streamlit/theme/overrides.css`

- [ ] **Step 1: Write the stylesheet**

Create `lex/lex_app/streamlit/theme/overrides.css`:

```css
/* LEX theme — the ONLY four things Streamlit's native theme cannot express.
   Scoped to data-testid hooks, which Streamlit treats as semi-public and
   which are far more stable than Emotion class hashes.

   This list is the complete fragile surface of the theme. Do not grow it
   without a deliberate decision: every rule here is something that can break
   on a Streamlit upgrade, guarded only by the pin and the DOM test (Task 11).

   Colours are duplicated from tokens.py because CSS cannot read Python.
   Keep them in sync — scenario 1.216 asserts it. */

/* 1. Card / container elevation — Streamlit has no shadow token. */
[data-testid="stMetric"],
[data-testid="stVerticalBlockBorderWrapper"] {
  box-shadow: 0 1px 3px rgba(16, 30, 54, 0.06);
}

/* 2. Gradient CTA — native primaryColor is a flat fill. */
[data-testid="stBaseButton-primary"] {
  background-image: linear-gradient(135deg, #14b4b4 0%, #0d9e9e 100%);
  border: none;
}

/* 3. Sidebar navy gradient — native sidebar.backgroundColor is a flat fill. */
[data-testid="stSidebar"] {
  background-image: linear-gradient(180deg, #283C50 0%, #1a2d3e 100%);
}

/* 4. Sidebar logo lockup — brand mark spacing above the nav. */
[data-testid="stSidebarHeader"] {
  padding-bottom: 0.75rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.10);
}
```

- [ ] **Step 2: Commit**

```bash
git add lex/lex_app/streamlit/theme/overrides.css
git commit -m "feat(streamlit-theme): add the four non-tokenisable CSS rules"
```

---

## ~~Task 10: Automatic CSS injection that preserves Streamlit magic ~~ (DROPPED)

> **STATUS 2026-07-30 — DROPPED, not implemented.** Tasks 9-12 built the CSS layer.
> That layer was deliberately abandoned after implementation revealed two things: the
> native theme surface already covers the sidebar and dataframe header (so CSS would add
> only two gradients, a shadow and a logo), and there is no *public* hook for automatic
> injection — the `runpy` shim planned in Task 10 would break Streamlit's AST magic
> transform, silently stopping bare-expression rendering in every customer dashboard, while
> the only magic-preserving alternative patches the private `ScriptRunner._run_script`.
> Trading that risk for four cosmetic touches is a bad trade. See the design doc §7 for the
> full reasoning. The shipped theme therefore has **zero** dependency on Streamlit
> internals. Revisit deliberately if the elevation/gradients are later judged to matter.


**Read the deviation note at the top of this plan before starting.**

**Files:**
- Create: `lex/lex_app/streamlit/theme/bootstrap.py`
- Modify: `lex/bin/lex.py` (inside the `streamlit` command)
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Confirm the patch target exists**

```bash
.venv-test/bin/python -c "
from streamlit.runtime.scriptrunner import script_runner as sr
import inspect
sig = inspect.signature(sr.ScriptRunner._run_script)
print('signature:', sig)
print('params:', list(sig.parameters))
"
```

Expected: a signature taking `self` plus one rerun-data argument. Record the exact parameter name — the wrapper below uses `*args, **kwargs` so it does not depend on it.

- [ ] **Step 2: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_CssInjection:
    """CSS reaches every page without customer code, and without breaking
    Streamlit's magic display."""

    # -- 1.215 --------------------------------------------------------
    def test_1_215_patch_target_still_exists(self) -> None:
        """Scenario 1.215: the ScriptRunner hook the injector wraps is still
        present. If Streamlit moves it, this fails fast instead of the CSS
        silently never being injected."""
        import inspect

        from streamlit.runtime.scriptrunner import script_runner as sr

        assert hasattr(sr, "ScriptRunner")
        assert callable(getattr(sr.ScriptRunner, "_run_script", None))
        # Wrapping is signature-agnostic, but it must accept arguments.
        assert len(inspect.signature(sr.ScriptRunner._run_script).parameters) >= 2

    # -- 1.216 --------------------------------------------------------
    def test_1_216_stylesheet_colours_match_the_tokens(self) -> None:
        """Scenario 1.216: the CSS duplicates token colours (CSS cannot read
        Python), so drift between them is a real risk. Pin it."""
        from pathlib import Path

        from lex.lex_app.streamlit.theme import bootstrap
        from lex.lex_app.streamlit.theme.tokens import TOKENS

        css = Path(bootstrap.__file__).with_name("overrides.css").read_text()

        assert TOKENS["brand"]["primary"] in css
        assert TOKENS["brand"]["primary_hover"] in css
        assert TOKENS["brand"]["sidebar_bg"] in css
        assert TOKENS["brand"]["sidebar_bg_end"] in css
        assert "#08BCC2" not in css

    # -- 1.217 --------------------------------------------------------
    def test_1_217_install_is_idempotent_and_reports_status(self) -> None:
        """Scenario 1.217: installing twice wraps once, and the installer
        reports whether it succeeded so a moved internal API is visible."""
        from lex.lex_app.streamlit.theme import bootstrap

        first = bootstrap.install_css_injection()
        second = bootstrap.install_css_injection()

        assert first is True
        assert second is False, "second install must be a no-op"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k CssInjection
```

Expected: FAIL — `ModuleNotFoundError: ... theme.bootstrap`

- [ ] **Step 4: Write the injector**

Create `lex/lex_app/streamlit/theme/bootstrap.py`:

```python
"""Automatic CSS injection for every Streamlit page.

WHY NOT A WRAPPER SCRIPT: ``ScriptRunner._run_script`` applies Streamlit's
*magic* AST transform to the file Streamlit itself compiles. Running the
customer script via ``runpy`` from a wrapper would give the transform to the
wrapper, so every dashboard relying on magic (a bare ``df`` rendering a table)
would silently render nothing. Instead we wrap ``_run_script`` and emit the
stylesheet inside the real run, leaving the customer script as the compiled
unit.

This is the design's second dependency on Streamlit internals (the first being
the ``data-testid`` selectors). Both are covered by the pin and by scenarios
1.215-1.218. If the internal API moves, ``install_css_injection`` returns
False and pages simply keep the native theme without the four extra rules —
degrading one rung, never breaking.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_INSTALLED = False
_SESSION_FLAG = "_lex_theme_css_emitted"


def stylesheet() -> str:
    """The stylesheet text shipped next to this module."""
    return Path(__file__).with_name("overrides.css").read_text(encoding="utf-8")


def emit_css() -> None:
    """Inject the stylesheet once per script run. Safe to call repeatedly."""
    try:
        import streamlit as st

        st.markdown(f"<style>{stylesheet()}</style>", unsafe_allow_html=True)
    except Exception:  # pragma: no cover — cosmetics must never break a page
        logger.warning("LEX theme: CSS injection failed", exc_info=True)


def install_css_injection() -> bool:
    """Wrap Streamlit's script runner so every page emits the stylesheet.

    Returns:
        True if the hook was installed, False if it was already installed or
        the internal API could not be found.
    """
    global _INSTALLED
    if _INSTALLED:
        return False

    try:
        from streamlit.runtime.scriptrunner import script_runner as sr

        original = sr.ScriptRunner._run_script
    except (ImportError, AttributeError):
        logger.warning(
            "LEX theme: Streamlit's ScriptRunner hook not found; pages keep the "
            "native theme without the CSS layer."
        )
        return False

    def _run_script_with_lex_css(self, *args, **kwargs):
        emit_css()
        return original(self, *args, **kwargs)

    _run_script_with_lex_css.__wrapped__ = original  # type: ignore[attr-defined]
    sr.ScriptRunner._run_script = _run_script_with_lex_css  # type: ignore[method-assign]
    _INSTALLED = True
    return True
```

- [ ] **Step 5: Install it from the CLI**

In `lex/bin/lex.py`, inside the `streamlit` command, immediately before the
`streamlit_main(...)` call added in Task 6, insert:

```python
    from lex.lex_app.streamlit.theme.bootstrap import install_css_injection
    install_css_injection()
```

- [ ] **Step 6: Run tests to verify they pass**

Same command as Step 3. Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/streamlit/theme/bootstrap.py lex/bin/lex.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "feat(streamlit-theme): inject the CSS layer automatically, preserving magic"
```

---

## ~~Task 11: DOM regression test — selectors match, magic still works ~~ (DROPPED)

> **STATUS 2026-07-30 — DROPPED, not implemented.** Tasks 9-12 built the CSS layer.
> That layer was deliberately abandoned after implementation revealed two things: the
> native theme surface already covers the sidebar and dataframe header (so CSS would add
> only two gradients, a shadow and a logo), and there is no *public* hook for automatic
> injection — the `runpy` shim planned in Task 10 would break Streamlit's AST magic
> transform, silently stopping bare-expression rendering in every customer dashboard, while
> the only magic-preserving alternative patches the private `ScriptRunner._run_script`.
> Trading that risk for four cosmetic touches is a bad trade. See the design doc §7 for the
> full reasoning. The shipped theme therefore has **zero** dependency on Streamlit
> internals. Revisit deliberately if the elevation/gradients are later judged to matter.


This is what keeps Task 9's four rules honest. Without it the pin is the only protection.

**Files:**
- Create: `lex/test_project/dashboards/theme_probe.py`
- Modify: `lex/test_project/tests/init/test_1y_streamlit_theme.py`

- [ ] **Step 1: Create the probe dashboard**

Create `lex/test_project/dashboards/theme_probe.py`:

```python
"""Probe dashboard for the theme DOM test (cluster 1y, scenario 1.218).

Renders one of every element the CSS layer targets, plus a bare expression so
Streamlit's *magic* display is exercised — the regression the wrapper-script
approach would have caused.

Run manually:
    lex streamlit run lex/test_project/dashboards/theme_probe.py
"""
import pandas as pd
import streamlit as st

st.set_page_config(layout="wide")

with st.sidebar:
    st.write("sidebar")

st.metric("NAV", "48.2M")
st.button("Run", type="primary")

_probe_frame = pd.DataFrame({"fund": ["Alpha", "Beta"], "nav": [18.4, 12.1]})

# Bare expression on its own line — Streamlit MAGIC must render this table.
_probe_frame
```

- [ ] **Step 2: Write the failing test**

Append to `test_1y_streamlit_theme.py`:

```python
class TestCluster1y_DomRegression:
    """The CSS selectors match real Streamlit output, and magic survives."""

    # -- 1.218 --------------------------------------------------------
    @pytest.mark.skipif(
        __import__("shutil").which("playwright") is None,
        reason="playwright not installed; run `playwright install chromium`",
    )
    def test_1_218_selectors_match_and_magic_renders(self) -> None:
        """Scenario 1.218: launch the probe dashboard and assert (a) every
        data-testid the stylesheet targets exists in the rendered DOM, and
        (b) the bare-expression table rendered — proving CSS injection did
        not cost us Streamlit's magic display.
        """
        import re
        import subprocess
        import time

        from playwright.sync_api import sync_playwright

        from lex.lex_app.streamlit.theme.bootstrap import stylesheet

        selectors = re.findall(r'\[data-testid="([^"]+)"\]', stylesheet())
        assert selectors, "stylesheet has no data-testid selectors to check"

        proc = subprocess.Popen(
            [
                "streamlit", "run",
                "lex/test_project/dashboards/theme_probe.py",
                "--server.port", "8599",
                "--server.headless", "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(12)  # Streamlit cold start
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto("http://localhost:8599", wait_until="networkidle")
                page.wait_for_selector('[data-testid="stMetric"]', timeout=30_000)

                missing = [
                    sel for sel in set(selectors)
                    if page.query_selector(f'[data-testid="{sel}"]') is None
                ]
                assert not missing, (
                    "stylesheet targets data-testid values Streamlit no longer "
                    f"renders: {sorted(missing)}"
                )

                # Magic display must still work.
                body = page.inner_text("body")
                assert "Alpha" in body and "Beta" in body, (
                    "the bare-expression DataFrame did not render — Streamlit "
                    "magic broke"
                )
                browser.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
```

- [ ] **Step 3: Install Playwright if needed**

```bash
.venv-test/bin/pip install playwright && .venv-test/bin/playwright install chromium
```

- [ ] **Step 4: Run the test**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init/test_1y_streamlit_theme.py -v -k DomRegression
```

Expected: PASS. If a selector is reported missing, fix `overrides.css` to a
`data-testid` that Streamlit 1.58 actually renders — do not delete the assertion.

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/dashboards/theme_probe.py lex/test_project/tests/init/test_1y_streamlit_theme.py
git commit -m "test(streamlit-theme): DOM regression — selectors match, magic preserved"
```

---

## ~~Task 12: Update the plan shards for Phase 2 ~~ (DROPPED)

> **STATUS 2026-07-30 — DROPPED, not implemented.** Tasks 9-12 built the CSS layer.
> That layer was deliberately abandoned after implementation revealed two things: the
> native theme surface already covers the sidebar and dataframe header (so CSS would add
> only two gradients, a shadow and a logo), and there is no *public* hook for automatic
> injection — the `runpy` shim planned in Task 10 would break Streamlit's AST magic
> transform, silently stopping bare-expression rendering in every customer dashboard, while
> the only magic-preserving alternative patches the private `ScriptRunner._run_script`.
> Trading that risk for four cosmetic touches is a bad trade. See the design doc §7 for the
> full reasoning. The shipped theme therefore has **zero** dependency on Streamlit
> internals. Revisit deliberately if the elevation/gradients are later judged to matter.


**Files:**
- Modify: `lex/test_project/test-plan/clusters/01-init/allocation.yaml`
- Modify: `lex/test_project/test-plan/clusters/01-init/batches.md`

- [ ] **Step 1: Run the full cluster-1 suite**

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/init -q
```

Expected: all pass. Record the count for the notes below.

- [ ] **Step 2: Update the `y` entry in `allocation.yaml`**

Set `max_scenario: 218`, change the `y` entry's `scenarios` to `1.203-1.218`,
its `tests.pass` to the count from Step 1's `1y` tests (17 = 12 from phase 1 + 5 here), and append to its note:

```
      Phase 2 adds the four non-tokenisable CSS rules (elevation, gradient CTA,
      sidebar gradient, logo lockup), injected by wrapping
      ScriptRunner._run_script — NOT by a wrapper script, which would have
      given Streamlit's magic AST transform to the wrapper and silently broken
      every bare-expression render. streamlit is pinned ~=1.58.0 because those
      rules touch rendered DOM. 1.215 asserts the patch target still exists,
      1.216 that the CSS colours match the tokens, 1.218 (Playwright) that
      every targeted data-testid is really rendered AND that magic still works.
```

- [ ] **Step 3: Append the Phase 2 batch note to `batches.md`**

```markdown
### Batch 1y (phase 2) — CSS layer + Streamlit pin ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.214 – 1.218 |
| Type | U + one E (Playwright DOM probe) |
| Files covered | `theme/overrides.css`, `theme/bootstrap.py`, `bin/lex.py`, `requirements.txt` |
| Test file | `lex/test_project/tests/init/test_1y_streamlit_theme.py` |
| Status | ✅ Complete |
| Note | The four rules Streamlit's native theme cannot express. Injection wraps `ScriptRunner._run_script` rather than using a wrapper script: the magic AST transform applies to whatever file Streamlit compiles, so a `runpy` wrapper would have silently broken every bare-expression render (verified during planning). If the internal hook moves, `install_css_injection()` returns False and pages keep the native theme minus four cosmetic rules — degrading one rung, never breaking. Phase 3 (live host handshake) is a separate plan. |
```

- [ ] **Step 4: Regenerate the dashboard and validate**

```bash
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py build
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py validate
```

Expected: `wrote ...dashboard.md` then `OK`.

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/test-plan
git commit -m "docs(test-plan): batch 1y phase 2 — CSS layer + Streamlit pin"
```

---

## Not in this plan

- **Phase 3 — live host handshake** (`SET_CUSTOM_THEME_CONFIG`). Touches the frontend repo and depends on Streamlit's origin allowlist working in a real deployment; that assumption must be verified first, since a negative result forces a fallback to reload-on-toggle. Separate plan.
- **Phase 4 — `tokens.json` + CI drift check.** Depends on the design-system repo's release pipeline. Until then `tokens.py` is the Python-side source of truth, and scenario 1.204 guards the values that actually drifted.
- **Screenshot-based visual regression** in frontend cluster F12. Task 11 asserts selectors and magic at DOM level inside lex-app, which catches the real failure mode (a selector no longer matching); pixel comparison belongs with the other frontend visual baselines.
