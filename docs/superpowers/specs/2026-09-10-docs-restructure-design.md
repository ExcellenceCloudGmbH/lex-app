---
title: "Docs restructure — design and gap register"
date: 2026-09-10
updated: 2026-09-14
status: implemented (restructure, figures, reference sweep); 1 register item blocked
---

# Docs restructure — design and gap register

## The problem

`lex-app-docs` had 66 published pages and ~48,000 words. Depth was not the
problem — `features/` averaged 973 words a page, `reference/` 824, `tutorial/`
940. Three things were:

1. **The top level mixed three organising ideas.** Subject (`features`,
   `interface`), content type (`reference`, `tutorial`) and situation
   (`migration`). Nothing about it told a reader where they were or what came
   next.
2. **The entry funnel was thin and doubled.** `index.md` (229 words) and
   `getting started.md` (179) both tried to be the front door. `getting
   started` then routed "business analysts" to
   `features/processing/calculations` and `features/access-and-ui/permissions`
   — developer API pages. That routing was wrong, not merely unhelpful.
3. **Gaps against the shipped product**, listed in the register below.

## Decisions

| Decision | Chosen | Why |
|---|---|---|
| Audience | Both, in one tree | Considered splitting build-vs-use at the top; judged not the main problem. |
| Top level | By task, in the order you hit it | The old level mixed taxonomies; a journey ordering answers "where am I, what's next". |
| Rewrite scope | Entries and landings only | Depth was fine. Effort goes where the deficiency is. |
| Register order | Blocking-first, then newest | Fix "not understandable" before "not complete". |
| Execution | Move + mechanical link rewrite + aliases, staged | 252 of 269 wikilinks carry explicit paths, so 93% break on a move. |
| Screenshots | Deferred to commented `📸 TODO` placeholders | Per instruction; the capture pipeline exists separately. |

## The tree

```
index.md                    home — one front door (aliases: home, getting started, features/index)
start-here/                 installation · project structure · running your app · tutorial/
model-your-data/            ← features/data-pipeline
calculations/               ← features/processing  (+ scheduled calculations)
history-and-audit/          ← features/tracking
access-and-dashboards/      ← features/access-and-ui  (+ widgets)
ship-and-operate/           NEW
using-the-app/              ← interface
reference/                  unchanged
migrating-from-v1/          ← migration
```

Order is enforced by `explorerOptions.sortFn` in `quartz.layout.ts`.
Alphabetical sorting would put "Start Here" ninth and the tutorial above the
installation page it depends on. The ranking lives **inside** the function
because Explorer serialises it with `.toString()` and re-evaluates it in the
browser.

## Constraints discovered

- **`lex-app/docs/` is a read-only mirror** of `lex-app-docs/content`, driven
  by `docs/.docs-sync.yml`. A renamed section must be renamed in
  `managed_paths` or the sync mirrors nothing while stale local copies
  persist. Updated in `docs/restructure-mirror`.
- **`lex --help` lists 10 of ~26 commands.** `main()` skips Django bootstrap
  for help, so every management passthrough and the `ai-*` family are
  invisible. The docs are the only complete command reference; the home page
  now says so.
- **Branch ruleset allows creating a branch but not updating one.** Each stage
  pushes to a new branch (`…-s2`, `-s3`, …).

## Gap register

Blocking-first, then newest. Status as of 2026-09-10.

| # | Page | Evidence | Status |
|---|---|---|---|
| 1 | `ship-and-operate/` (6 pages) | 0 pages on backup, 1 on Kubernetes, 1 on troubleshooting; "running your app" 325 words | **done** |
| 2 | `ship-and-operate/upgrading` | No page existed; 2.1.11's `max_length` migration had no home | **done** |
| 3 | `access-and-dashboards/widgets` | `WidgetPage`, `WidgetSpecError`, `lex_calculation_log`, `lex_calculation_log_tree` exported and undocumented — the 2.2.0 flagship | **done** |
| 4 | `calculations/scheduled calculations` | Recorded as "guard-rejected, never published". It was an untracked *draft* for an API that does not exist | **withdrawn** — see item 11 |
| 5 | `access-and-dashboards/embedding` | Planned | **declined** — `lex_view callbacks` and `streamlit dashboards` already cover both directions |
| 6 | Screenshots | 9 placeholders | **7 done** — record page + 4 tabs, the grid, table settings, `lex --help`; `deploying` got a mermaid diagram instead |
| 7 | Analytics tab + widgets figures | Both prerequisites are now done — the fixture has `Fund.streamlit_main` and the harness starts `lex streamlit` behind `LEX_DOCSHOT_STREAMLIT=1`. Still blocked one level down: the Streamlit proxy wants a Keycloak JWT, the harness signs in with a Django admin session | blocked on fixture auth; capture exists and is skipped |
| 8 | `reference/` completeness sweep | 24 commands shipped / 4 documented; 126 env vars read / 66 listed | **done** — and turned into three CI checks, below |
| 9 | Backup and restore | Named as absent; no framework-side facts verified | **done** — `ship-and-operate/backup and restore` |
| 10 | `lex Init` is not a command | Found by the sweep. 34 occurrences, including step one of installation | **done** |
| 11 | `ScheduledCalculation` does not exist | Found by the sweep. Item 4 above was published unverified | **done** — page removed |

### What the sweep actually found

Item 8 was recorded as a completeness gap. It was, but the two worst things it
turned up were not gaps — they were pages that were confidently wrong.

**`lex Init` does not exist.** The command is `init`; `Init.py` was renamed and
the docs were never followed. Click does an exact lookup, so `lex Init` fails
with *No such command*. It appeared 34 times across 10 pages, including step
one of the installation guide and every tutorial part.

**`ScheduledCalculation` does not exist.** Register item 4 above says
"written in lex-app under a mirror-managed path; guard-rejected, never
published — **done**". That entry was wrong in the way that matters: the page
was an untracked *design draft* in lex-app, and "blocked by the guard" was read
as "finished but blocked". It was published in #176 with a parameter table and
three conflict modes for an API that has never existed. Removed in
lex-app-docs#180.

The lesson is narrow and worth keeping: **a draft that reads like documentation
is indistinguishable from documentation once it is moved.** Provenance —
untracked, never committed, no implementing code — was available and not
checked.

### The CI checks that replace the prose audit

Item 8 said the audit "should be a CI check rather than prose". Three now live
in `.github/scripts/`, wired into `docs_mirror_guard.yml` as a job with no
`docs-sync/*` exemption, because a sync PR is exactly when a bad reference
arrives from upstream:

| Script | Checks |
|---|---|
| `check_doc_imports.py` | every `from lex…` in the published docs resolves, by AST, against this repo's source |
| `check_doc_commands.py` | every `lex <command>` named exists — **and** every shipped command is documented |
| `check_doc_env_vars.py` | every environment variable the framework reads has a reference entry |

All three default to the mirror-owned paths in `docs/.docs-sync.yml`: that is
the published subset, and specs and plans under `docs/` legitimately name APIs
before they exist. None imports anything — resolution is AST-only, so they run
in a bare checkout with no dependencies and no Django settings.

Extraction for the env check covers both `os.getenv("NAME")` and the proxy's
`_env_bool("NAME", …)` wrappers. The first sweep used only the literal form and
missed ten variables; the check exists partly to stop that recurring.

### What the following two days found

| # | Page | Evidence | Status |
|---|---|---|---|
| 12 | Every published figure 404ed | `../../images/…` in the markdown. Quartz's `transformLink` computes the climb to the content root itself and keeps a hand-written one as an extra prefix, so every `<img>` resolved above the site's base path. Eleven of twelve, broken since figures first shipped | **done** — lex-app-docs#183 |
| 13 | No way to judge the rewrite | `compare_docs.py` measures two revisions on one ruler and runs these three gates against both | **done** — lex-app-docs#186 |
| 14 | The gates scanned a virtualenv | `lex/**` is the package in CI and also `.venv-test` + `.claude/worktrees` on a working copy: 126 variables became 467, 34 commands became 72, and the gate asked for docs on `ARROW_HOME` and `lex collectstatic` | **done** — #775 |
| 15 | The newest surface had no picture | `lex_view`, widgets and standalone dashboards each turned on a mechanism the prose could only assert | **done** — diagrams in lex-app-docs#187; screenshots written in process-admin-general-client#485 and blocked on the toolchain |

**The visual count was wrong twice, and both errors flattered.** A scan for
`![](images/…)` missed every figure written `../../images/…`; a scan for images
and video missed the 30 pages carrying a **mermaid diagram**, and
`compare_docs.py` shared that blind spot because it strips fenced code before
counting — a diagram *is* a fenced block. Reported 16% of pages carrying a
visual; the answer is 51%, now 54%. The number that is easiest to quote is the
one worth checking twice.

**The Streamlit auth blocker was narrower than its error message.** Item 7 has
said for weeks that the Analytics capture needs a Keycloak JWT. That is true of
the *embedded* path. `authenticate_from_proxy_or_jwt()` takes identity from four
plain headers the proxy sets, and treats a bearer token as the fallback when
they are absent — so the standalone app is capturable by supplying the same
input the proxy does. Nobody re-read the function; the error message was taken
as the finding.

Item 9 is written from framework facts only. What the framework backs up
(Keycloak authorization) is documented precisely, including that `--restore`
reports failure by printing it and exiting zero. What it does not back up (the
database, file storage) is named as such, with the decision left to whoever
operates them — writing that from assumption is how operational documentation
becomes dangerous.

## Verification

`scripts/check_links.py` in `lex-app-docs` gates every stage. Baseline before
the move and after every commit: 0 broken, 0 ambiguous, 0 missing. It found
four of its own bugs and two real ones (a dangling link to the declined
`embedding` page, and the `features/index` links left by the fold).

## Branches

| Branch | Repo | Contains |
|---|---|---|
| `docs/restructure-2026-09-s8` | lex-app-docs | The restructure and the figures (all stages) |
| `docs/restructure-mirror-s4` | lex-app | The mirror manifest, stale-copy removal, this spec |
| `docs/reference-completeness` | lex-app-docs | The command/import fixes, the complete reference, backup & restore (#180) |
| `docs/import-checker` | lex-app | The three CI checks and this register update |
| `feat/docs-figures-s3` | lex-app | The renderer and figures spec |
| `docs/figure-captures` | process-admin-general-client | The capture harness and the dashboard fixture |

## What the figure pipeline learned

Worth carrying into anything similar. Every one of these produced a wrong
figure before it produced a rule:

- A resolved anchor is not a usable screen. The Analytics capture made a
  clean, correct photograph of an authentication error.
- Measure and photograph in one settled layout, then check it did not move.
  A uniformly-offset figure is undetectable by eye once built.
- Clamp a box that overflows the picture; REJECT one entirely outside it.
  Conflating those marks controls that were never photographed.
- Place a mark by the target's size. Lanes suit a column of switches and are
  wrong for a row of tabs, where they drag leaders across the whole image.
