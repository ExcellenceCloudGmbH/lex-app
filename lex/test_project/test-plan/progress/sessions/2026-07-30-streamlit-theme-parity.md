---
date: 2026-07-30
clusters: [1ae]
tests_added: "20 scenarios / 30 tests (1.274–1.293)"
suite_tally: "init cluster 305 -> 307 pass / 13 skip / 0 fail"
---

**Batch 1y landed — Streamlit theme parity, phase 1 of the design.** Lex ships
two customer-visible surfaces: the React frontend (navy `#283C50` sidebar,
teal `#14b4b4` accent, Inter) and Streamlit dashboards embedded via
`lex_view`. The Streamlit side had drifted completely from the frontend it is
supposed to match — the only theme artifact in the repo,
`lex/.streamlit/config.toml`, was six hand-written lines nobody had touched
since it was first typed: `primaryColor="#08BCC2"` against the frontend's real
`#14b4b4`, a light grey `secondaryBackgroundColor` against the actual navy
sidebar, `textColor="#2D4262"`, `font="sans serif"` instead of Inter, and no
dark mode at all. There was no generator, so there was nothing to regenerate
from — the file just rotted in place.

Streamlit 1.58's native theme surface turned out to be big enough (~120 keys,
global + per-mode) that essentially all of this parity rides the PUBLIC config
API rather than any CSS override. `tokens.py` vendors the design tokens (brand
colours, fonts, radii, chart ramps, per-mode surfaces) as pure Python,
transcribed from the frontend's own design system with dated provenance
comments. `mapping.py` is a pure function, `build_streamlit_theme(tokens,
mode)`, that walks those tokens into the flat Streamlit key → value mapping
for `"light"` or `"dark"`; the 1.280 contract tests read Streamlit's own
config-option template directly, so a Streamlit upgrade that renames or
re-scopes a key fails the suite instead of just silently no-oping in
production.

`config_writer.py` renders that one mapping two ways. CLI flags
(`--theme.<path>=<value>`, appended on every `lex streamlit` launch by
`_safe_theme_flags` in `lex/bin/lex.py`) are primary, because they are
location-independent — a `.streamlit/config.toml` resolves relative to the
process's working directory, so a dashboard started from a different CWD
would silently launch unthemed if the file were the only path. The generated
`config.toml` exists as the fallback for a bare `streamlit run` that bypasses
the `lex` CLI entirely. Both come out of the same `build_full_config()` call,
so the two paths cannot disagree with each other.

Fonts point at the frontend's OWN Google Fonts stylesheet URL (Streamlit's
font key takes a `"<family>:<url>, <fallback>"` form) rather than a font
bundled for Streamlit alone. Bundling was deliberately rejected: it would make
the two surfaces diverge exactly where it's hardest to notice — an
air-gapped deployment where the frontend falls back to system sans while a
Streamlit-only woff2 kept rendering Inter regardless. Pointing both at the
same URL means both succeed together and both fall back together. Separately,
4 of the ~120 keys (the three chart-colour ramps plus `showSidebarBorder`)
exist ONLY at the top-level `[theme]` scope with no per-mode twin, because
Streamlit rejects unrecognised config options outright rather than ignoring
them — `GLOBAL_ONLY_KEYS` pins that set and 1.280b checks it against the
installed Streamlit's own template so an upgrade that changes it is caught
before it breaks a launch.

This session's own addition is the drift guard. Replacing the six wrong lines
fixes today but does nothing to stop the exact same rot next time — a
hand-edit, or a token change nobody regenerated for.
`TestCluster1y_CommittedConfig` (1.293/1.293b) asserts the committed
`lex/.streamlit/config.toml` is byte-identical to
`render_config_toml(build_full_config(TOKENS))`, plus a named check that the
specific stale values (`#08BCC2`, `#F5F5F5`, `#E0E0E0`, `#2D4262`,
`"sans serif"`) are gone, so a bad revert fails with a legible reason instead
of a generic diff. Verified the guard actually bites before committing:
hand-edited one colour in the regenerated file, watched 1.293 fail,
regenerated, watched it go green again. Also launched real Streamlit 1.58
against a copy of the generated file from a scratch directory to confirm
clean startup — the file this guard protects is one Streamlit actually
accepts, not merely one that
happens to parse as TOML.

This closes phase 1 of the design
([`docs/superpowers/specs/2026-07-30-streamlit-theme-parity-design.md`](../../../../../docs/superpowers/specs/2026-07-30-streamlit-theme-parity-design.md)):
token source of truth, native theme mapping onto Streamlit's public config
API, both delivery paths, and the regen/drift guard. Phases 2–4 (a CSS layer
for whatever the ~120 keys don't reach, a live host handshake so an embedded
dashboard can react to the frontend's own runtime theme instead of a static
snapshot, and generating `tokens.py` itself from the design system's
`tokens.json` with a CI drift check) are unstarted and tracked separately. See
[batch 1y](../../clusters/01-init/batches.md).

> **Renumbered on merge, 2026-08-28.** This batch was letter z / scenarios
> 1.211-1.236 when written. Both were taken on lex-app-v2 while the branch was
> open, so it landed as **ae / 1.274-1.299**.
