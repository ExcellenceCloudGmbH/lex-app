# Streamlit Theme Parity — Design

> **Date:** 2026-07-30
> **Status:** Approved design, not yet implemented
> **Goal:** Streamlit pages served by lex-app look like part of lex-app — same brand,
> typography, surfaces and light/dark mode — so a customer moving between a grid page
> and a dashboard does not feel they changed products.

---

## 1. Problem

Streamlit is themed today by a single hand-written file, `lex/.streamlit/config.toml`:

```toml
[theme]
primaryColor="#08BCC2"
backgroundColor="#F5F5F5"
secondaryBackgroundColor="#E0E0E0"
textColor="#2D4262"
font="sans serif"
```

Every value has drifted from the actual frontend:

| Surface | Streamlit config | lex-app frontend |
|---|---|---|
| Brand accent | `#08BCC2` | `#14b4b4` |
| Sidebar | light grey `#E0E0E0` | navy gradient `#283C50 → #1a2d3e` |
| Font | `"sans serif"` | Inter |
| Dark mode | absent | fully supported (`body.dark`, MUI palette mode) |

This is not a one-off mistake, it is the predictable result of maintaining brand values by
hand in a second place. A LEX design system already exists and is published as
`@excellencecloudgmbh/lex-tokens` (0.2.0), described as the single source of truth — but it
is npm-only, so the Python side cannot consume it and re-types the values instead.

**Non-goal:** this design does not change what Streamlit *is*. Dashboards stay Streamlit
scripts written by customers. Only their presentation changes.

## 2. Decisions

Four decisions were taken during brainstorming and are settled inputs to this design:

| # | Decision | Chosen |
|---|---|---|
| 1 | Fidelity | **Deep parity** — native theme *plus* a small CSS layer, so pages read as one product |
| 2 | Delivery | **Automatic, no per-page escape hatch** — every Streamlit page is themed |
| 3 | Light/dark | **Follow the lex-app host live** — no reload, no lost dashboard state |
| 4 | Token source | **Design system emits `tokens.json`**; lex-app vendors a generated Python module with a CI drift check |

On decision 2: there is no opt-out *flag*, but CSS cascade still applies. A dashboard that
injects its own `st.markdown("<style>…")` runs after ours and wins for the properties it
sets. That is expected and documented behaviour, not a loophole to close — it means
deliberate custom styling is layered on top rather than bricked.

## 3. Two findings that shaped the design

Both were verified against the installed Streamlit (1.58.0), not assumed.

**Finding 1 — the native theme surface is large.** Streamlit 1.58 exposes ~120 theme config
keys, including separate `[theme.light]` / `[theme.dark]` blocks, per-surface `sidebar`
sub-blocks, `fontFaces` (custom font registration), `headingFont` / `codeFont`,
`baseRadius` / `buttonRadius`, `borderColor`, `linkColor`, `showWidgetBorder`,
`metricValueFontSize`, `dataframeHeaderBackgroundColor` / `dataframeBorderColor`, and chart
palettes (`chartCategoricalColors`, `chartSequentialColors`, `chartDivergingColors`).

Verify with:

```python
from streamlit import config
config.get_config_options()
sorted(k for k in config._config_options_template if k.startswith("theme"))
```

Consequence: most of what "deep parity" appeared to require in CSS — navy sidebar, table
header, radii, fonts, chart colours — is reachable through the **public** theme API.

**Finding 2 — live theme switching is natively supported; CSS variables are not.**
Streamlit's stylesheets define exactly one CSS custom property (`--overlay-top`); its
theming is compiled through Emotion into generated class names. A "swap the CSS variables"
approach is therefore impossible.

However the client bundle implements a host-communication channel:

```
parent → iframe:   { type: "SET_CUSTOM_THEME_CONFIG", themeName, themeInfo }
iframe → parent:   { type: "SET_THEME_CONFIG",        themeInfo }
```

The inbound message is dispatched to `themeChanged(themeName, themeInfo)` and re-themes the
running app in place. This is the mechanism Streamlit Cloud uses. Live mode-following
therefore needs **no reload and no CSS hacks** — and it reuses the postMessage plumbing
lex-app already has in `lexAppBridge` for the reverse direction (`useHostThemeListener`,
which syncs an embedded lex grid to a Streamlit host).

## 4. Architecture

Four stages. Layers 1–2 are the static base, 3 is the CSS layer, 4 is the live channel.

### Layer 1 — build/release time: one source of truth

```
lex-design-system ──> tokens.json ──> lex/…/theme/tokens.py ──> CI drift check
   (npm package)      (new artifact)     (generated, vendored)   (fails on drift)
```

`tokens.json` is a new, language-neutral export published alongside the npm package.
lex-app vendors a generated `tokens.py`; CI fails when the vendored copy no longer matches
the published tokens. This check is the actual fix for section 1 — it converts silent rot
into a build failure.

### Layer 2 — server start: the static base

```
tokens.py ──> mapping.py ──> config_writer.py ──> theme config
              (pure)          (pure)               [theme] [theme.light] [theme.dark]
```

Covers standalone dashboards and first paint before any host message arrives. Both middle
steps are pure functions with no Streamlit import, so the whole mapping is unit-testable as
a data transform.

**How the config actually reaches Streamlit.** A `.streamlit/config.toml` is resolved
relative to the *current working directory* (or `~/.streamlit/`), which makes a file-only
approach dependent on where the customer happens to launch from — the existing
`lex/.streamlit/config.toml` is only picked up when CWD is the package root. Two delivery
paths, in precedence order:

1. **Primary — CLI flags.** The `lex streamlit` command already appends flags
   (`--browser.serverPort`, `--server.port`). The shim appends `--theme.<key>=<value>` for
   every mapped key. This is location-independent and deterministic, and it is how a page
   launched through lex-app always gets the right theme.
2. **Fallback — generated file.** `write_config()` also renders the same mapping to the
   project's `.streamlit/config.toml`, so a dashboard launched with bare `streamlit run`
   from the project root is still themed.

Both come from one `build_full_config()` call, so the two paths can never disagree.

### Layer 3 — page render: automatic, zero customer code

```
lex streamlit run dash.py ──> CLI shim ──> inject overrides.css ──> runpy(user script)
```

`lex streamlit` is the documented launch path, so wrapping it achieves decision 2 without
touching a single customer dashboard.

**Limitation, stated deliberately:** a dashboard launched with bare `streamlit run` still
receives full `config.toml` theming but not the CSS layer. It degrades to native-only
parity, which is acceptable and is the same rung as failure mode 1 below.

### Layer 4 — runtime: live mode following

```
user toggles dark in lex-app
      ──> lexAppBridge posts SET_CUSTOM_THEME_CONFIG to the Streamlit iframe
      ──> Streamlit re-themes in place (no reload, dashboard state intact)
```

We do not implement a receiver — Streamlit's own host-communication manager handles the
message. We enable it (origin allowlist) and send from the host.

## 5. Components and interfaces

New module, `lex/lex_app/streamlit/theme/`:

| File | Purpose | Interface | Pure? |
|---|---|---|---|
| `tokens.py` | Generated token data. Never hand-edited. | `TOKENS: dict`, `TOKENS_HASH: str` | yes (data) |
| `mapping.py` | lex tokens → Streamlit theme keys | `build_streamlit_theme(tokens, mode) -> dict`, `build_full_config(tokens) -> dict` | yes |
| `config_writer.py` | theme dict → TOML text/file | `render_config_toml(cfg) -> str`, `write_config(path) -> None` | yes |
| `overrides.css` | The thin CSS layer (4 rules, §7) | — | n/a |
| `bootstrap.py` | Inject CSS once per session | `apply() -> None` | **no** (touches `st`) |

`bootstrap.py` is the only file that imports Streamlit. Frontend side adds one function to
`lexAppBridge`: `postStreamlitTheme(iframe, mode, themeInfo)`.

### The self-describing payload

The `themeInfo` shape is **not** reverse-engineered or hard-coded. Streamlit announces its
own active theme outbound on mount (`SET_THEME_CONFIG`). The host:

1. listens for that announcement and keeps it as a template,
2. overrides the colour fields from lex tokens for the requested mode,
3. posts the result back as `SET_CUSTOM_THEME_CONFIG`.

The shape therefore always originates from the *running* Streamlit version. A version bump
that changes the structure cannot silently break the handshake — which turns the most
upgrade-fragile part of the design into the most resilient.

## 6. Token → native key mapping

| lex token | Streamlit key(s) |
|---|---|
| brand teal `#14b4b4` | `primaryColor` |
| surface page / raised | `backgroundColor` / `secondaryBackgroundColor` |
| text primary | `textColor` |
| hairline border | `borderColor`, `showWidgetBorder` |
| navy `#283C50` / `#dfe7ee` / teal | `sidebar.backgroundColor` / `sidebar.textColor` / `sidebar.primaryColor` |
| Inter, via the frontend's own Google Fonts stylesheet | `font`, `headingFont` (Streamlit's `"<name>:<url>"` form) |
| Fira Code | `codeFont` |
| card radius 12 / control radius 10 | `baseRadius` / `buttonRadius` |
| grid header `#F6F8FA`, hairline | `dataframeHeaderBackgroundColor`, `dataframeBorderColor` |
| brand chart ramp | `chartCategoricalColors`, `chartSequentialColors`, `chartDivergingColors` |
| success / warning / error | `green*`, `orange*`, `red*` colour triplets |
| link colour | `linkColor`, `linkUnderline` |

Every key above also receives a `[theme.dark]` twin from the dark token set, plus
`[theme.light.sidebar]` / `[theme.dark.sidebar]` where a sidebar-specific value exists.

## 7. What the CSS layer owns

Exhaustive list — these four have no native token:

1. Card/container **elevation** (Streamlit has no shadow token)
2. The **gradient CTA** (native `primaryColor` is a flat fill)
3. The sidebar **navy gradient** (native `sidebar.backgroundColor` is a flat fill)
4. The sidebar **logo lockup**

All four are scoped to `data-testid` hooks (`stSidebar`, `stMetric`, `stDataFrame`, …),
which Streamlit treats as semi-public and which are markedly more stable than Emotion
class hashes. This list is the complete fragile surface of the design and should not grow
without a deliberate decision.

## 8. Failure modes

The safety property: **every failure degrades one rung, never to broken.**

```
live handshake  →  config.toml theme  →  Streamlit default
```

| Failure | User-visible result | Guardrail |
|---|---|---|
| Streamlit upgrade changes internals | Missing shadow or flat button; page still brand-themed | Pinned version; 4 CSS rules only; a vanished `data-testid` makes the rule a no-op |
| Origin allowlist misconfigured | Correct brand, possibly wrong light/dark | Host logs a console warning when no `SET_THEME_CONFIG` announcement arrives within 3 s — visible instead of silent |
| `tokens.json` not published yet | Nothing; vendored `tokens.py` is committed | Drift check runs in **warn** mode until phase 4 |
| Customer injects own `<style>` | Their styling wins | Expected (cascade order); documented |
| Font stylesheet unreachable (air-gapped) | System sans — **and the frontend falls back identically**, so the two stay consistent | Same source as the frontend by construction (§8.2) |
| Theme toggled mid-script-run | Nothing breaks | Streamlit re-themes client-side only; no rerun triggered |

### Two required changes this design depends on

1. **Pin `streamlit`.** It is unpinned in `requirements.txt` today, so upgrades reach
   customer installs silently. That is acceptable for config-only theming and not
   acceptable once four CSS rules touch internals. Pin `~=1.58.0` and upgrade
   deliberately, with the visual-regression suite as the gate.
2. **Use the frontend's own font source — do NOT bundle (CORRECTED 2026-07-30).**
   This originally required vendoring the Inter woff2, reasoning that air-gapped customers
   would otherwise lose the typography. That premise was wrong. Verified: **no woff2 is
   vendored anywhere in lex-app, and the frontend itself loads Inter from Google Fonts**
   (`fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap`).

   So an air-gapped frontend *already* falls back to system sans. Bundling for Streamlit
   alone would make Streamlit **more** correct than the app it must match, and the two
   would visibly diverge — Streamlit in Inter, the frontend in system sans. That is worse
   parity, not better.

   Streamlit's `theme.font` / `headingFont` / `codeFont` natively accept a
   `"<name>:<url>"` CSS-stylesheet reference, so we point at the *same* stylesheet the
   frontend uses. Parity then holds both ways: connected, both render Inter; air-gapped,
   both fall back together. This needs no `fontFaces`, no `server.enableStaticServing`,
   and no font file written into customer project directories — Streamlit only serves a
   `static/` dir from the **app's** directory, which for a customer dashboard is theirs,
   not ours.

   Bundling Inter for **both** surfaces stays a legitimate future improvement (it would
   drop an external dependency) but it is a cross-repo change, out of scope here.

## 9. Testing

**Key-existence contract test (cheapest, highest value).** Assert every Streamlit key the
mapping emits exists in `streamlit.config._config_options_template`. Runs in milliseconds
and converts "an upgrade renamed `dataframeHeaderBackgroundColor`" from silently-lost
styling into a failed unit test.

| Layer | Test | Owner |
|---|---|---|
| `mapping.py`, `config_writer.py` | Pure data transforms: light+dark completeness, every token consumed, valid TOML, key-existence contract | Backend cluster owning the Streamlit surface (cluster 1 holds `test_1r_lex_view_embed_helper` today); allocate via the **lex-testing** skill |
| `overrides.css` | Visual regression: reference dashboard rendered headless in light + dark, screenshot-compared | Frontend cluster **F12 `embed_streamlit`** |
| Layer 4 handshake | E2E: embed a Streamlit page, toggle dark in lex-app, assert the iframe re-themed **without a reload** and dashboard state survived | Frontend cluster **F12** |

The visual-regression suite is what keeps the four CSS rules honest across Streamlit
upgrades; without it, the pin is the only protection.

## 10. Rollout

| Phase | Deliverable | Risk | Independently shippable |
|---|---|---|---|
| 1 | Vendored tokens + `mapping.py` + `config_writer.py` + generated `config.toml` (light + dark) | Very low — no CSS, no handshake | **Yes** — removes the `#08BCC2` drift immediately |
| 2 | CLI shim + `overrides.css` (the 4 rules) + visual regression + Streamlit pin | Low | Yes |
| 3 | Live handshake: origin allowlist, host bridge, E2E | Medium — verify the allowlist end-to-end first | Yes |
| 4 | `tokens.json` in the design system; drift check warn → **fail** | Cross-repo dependency | Yes |

Phase 1 delivers most of the visible win at very low risk and does not depend on the
design-system repo. Phase 3 is the first phase that must prove the origin allowlist works
in a real deployment; that verification should happen at the *start* of the phase, since a
negative result forces a fallback to reload-on-toggle.

**Scope of the first implementation plan: phases 1 and 2 only.** Those are confined to
lex-app and are enough to close the visible brand gap. Phase 3 (live handshake) touches the
frontend repo and hinges on an unverified deployment assumption; phase 4 depends on another
repo's release pipeline. Each gets its own plan so neither blocks the visible fix.

## 11. Out of scope

- Changing how dashboards are authored or introducing lex-styled Streamlit widget wrappers.
  Only presentation of existing Streamlit primitives changes.
- The reverse direction (an embedded lex grid following a Streamlit host) — already built
  as `useHostThemeListener`.
- Restyling third-party Streamlit components; they are not reachable through either the
  native theme or `data-testid` hooks.
- Chart libraries that ignore Streamlit's theme (e.g. a hard-coded Matplotlib palette).
  Native `chart*Colors` covers Streamlit's own chart elements and Altair/Vega defaults only.
