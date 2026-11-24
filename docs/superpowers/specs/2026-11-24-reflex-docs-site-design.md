# A docs site for lex-app's Reflex dashboards, built in Reflex

**Status:** design agreed 2026-11-24, not built. Branch `feat/reflex-docs-site`, stacked on the
lex.ui project-chrome step (#913).
**Why:** the Reflex stack (#902–#913) gives consultants a new way to build dashboards: `lex.ui`
pages, state and actions, RFDS's look, sign-in through lex-app. The six pages under
`docs/access-and-dashboards/reflex/` (mirrored from the Quartz site in `lex-app-docs`) describe the
older API and do not mention `lex.ui`. reflex.dev documents Reflex with a site that is itself a
Reflex app, where examples run on the page. We want the same for lex.ui: a reader sees a
`ui.record` page work, clicks its action, watches its job progress, next to the code that did it.

## Scope

- **In:** the Reflex dashboard architecture, for consultants building dashboards on lex-app.
- **Out:** the rest of lex-app's docs (modelling data, calculations, history and audit, operating).
  They stay on the Quartz site and its mirror and Copilot pipelines, unchanged. Moving them here
  is a possible later phase, not part of this design.
- **Out:** where the site is deployed. It is one container; choosing its home comes after the
  first pages exist.

## Decisions

1. **The site is a Reflex app in the lex-app repo, under `docs_site/`.** Demos import `lex.ui`
   from the same commit, so a PR that changes `lex.ui` updates its docs in the same diff, and CI
   fails the PR whose change breaks an example. A separate repo would only catch up after a
   release (see `lex Init` and `ScheduledCalculation`, documented and never real).
2. **Markdown pages, parsed by `reflex-docgen`, drawn by our own components.** `reflex-docgen`
   (Apache-2.0, maintained by the Reflex team, 0.10.0 in October 2026) parses Markdown and the
   fence forms reflex.dev uses. A transformer of ours, about 300 lines, turns each block into an
   RFDS component in lex-app's look, so the docs look like the dashboards a reader builds.
   Rejected: `reflex-docgen`'s stock components (the docs would look like reflex.dev, not lex);
   `flexdown` (retired, no licence); our own parser on `markdown-it-py` (rewrites fence handling,
   TOC and code execution that `reflex-docgen` already has). reflex.dev's own site repo,
   `reflex-web`, was archived in April 2026 and has no licence file: we take its pattern, not its
   code.
3. **Demos run in a sandbox, not against a lex-app instance.** No sign-in, no Keycloak, no
   database, no secrets: the site is one self-contained container and CI can build it anywhere.
   Demos show lex.ui's behaviour; they do not claim to show a real database or real permissions.
4. **One navigation source.** `docs_site/nav.py` declares sections, groups and pages. Landing
   cards, sidebar, breadcrumbs and prev/next all come from it. The build fails on a page missing
   from `nav.py` or a `nav.py` entry with no page.
5. **Every page is also Markdown.** For readers using an AI tool: `<page>.md`, `llms.txt`,
   `llms-full.txt` and a "Copy page" button, all from the same files.

## Layout

```
docs_site/
  rxconfig.py
  docs_site/
    app.py            # rx.App with RFDS; installs the sandbox resolver; registers pages from nav.py
    nav.py            # sections -> groups -> pages
    render/           # reflex-docgen blocks -> lex-look components; the fence handlers
    sandbox/          # demo user, grants, SandboxApi, sample data
    search.py         # builds the client-side search index at compile time
    llms.py           # .md routes, llms.txt, llms-full.txt
    reference.py      # API reference pages from lex.ui's docstrings
  content/
    index.md
    get-started/  pages/  state-and-actions/  data-and-permissions/  look-and-chrome/  ship-it/
  tests/              # build checks (see CI)
```

`docs_site/` is not part of the `lex-app` wheel: it is excluded from packaging, and model
discovery never sees it.

## What a reader sees

Like reflex.dev:

- **Landing page** with one card per section.
- **Left sidebar**: sections, with collapsible groups; the current page highlighted.
- **Right column**: "On this page", from the page's `##`/`###` headings (`reflex-docgen` already
  extracts them).
- **Bottom**: previous and next page, in `nav.py` order.
- **Header**: search, "Copy page", "View Markdown".

### Sections

| Section | Pages (first pass in bold) |
| --- | --- |
| Get started | **Your first dashboard**, **`lex reflex add`**, running it locally |
| Pages | **`@ui.page`**, **`ui.record`**, **`ui.list`**, **tables** |
| State & actions | **`ui.State`**, **`@ui.action`**, **`@ui.job`**, `@ui.load` |
| Data & permissions | `self.api`, `self.can`, grants from Keycloak |
| Look & chrome | lex-app's look, `ui.chrome`, embedding in lex-app |
| Ship it | the sign-in hand-off, deploying, `lex reflex check` |
| API reference | generated from `lex/ui.py` and `lex/lex_app/reflex/ui/` |

Pages not in bold start as short stubs (what the page will cover, with links to the code), so the
navigation is complete from the first release.

### Writing style

The same rules as the published docs: conversational, what a thing does and how to use it, minimal
internal names outside the API reference. Every claim is checked against the source on the same
commit.

## Fences

| Fence | Renders |
| --- | --- |
| ` ```python demo ` | the code with a copy button, and the live component in a framed card |
| ` ```python demo-only ` | the live component only |
| ` ```python exec ` | runs setup code (states, sample data); shows nothing |
| ` ```python ` | a highlighted code block |
| ` ```md alert info\|warning\|danger ` | a callout |
| ` ```md tabs ` | tabs, split on `##` |

- A demo's code runs in a module namespace per page, as `reflex-docgen` already does for `exec`.
  States a page defines get a page-scoped name, so two pages can both define `Tickets`.
- A demo that returns a page kind (`ui.record`/`ui.list`) is drawn with `ui.dashboard` inside a
  bordered frame, so it reads as a page and not as part of the docs. (A desktop/phone width toggle
  is later work.)

## Sandbox

`lex.ui` already lets something else supply each event's session:
`context.set_resolver(fn)` returns a `Session` with the user, the grants and an `api` stand-in
(`Session.api`). `ui.testing` fakes a user this way. The docs app installs its own resolver at
start-up instead of lex-app's sign-in hand-off.

- **User:** "Demo Consultant", with a fixed default set of grants.
- **`SandboxApi`:** the methods `self.api` has (`list`, `get`, `create`, `update`, `delete`, with
  the same filters and search arguments), over an in-memory store.
- **Sample data:** Funds, Investors and Commitments, with dated rows for history-style examples.
  Small (tens of rows), defined in Python, loaded from a seed function.
- **Per tab:** each browser tab gets its own copy of the store, keyed on Reflex's client token, and
  a reload resets it. One reader's edits never reach another reader. Nothing is persisted. Copies
  for idle tabs are dropped after 30 minutes.
- **Switching grants in a demo:** each demo card has a "Viewing as" switch with two roles, Editor
  (every grant on the sample models) and Viewer (`read` only). The role belongs to the tab, so a
  permission example really hides the button or refuses the action once the reader switches.
- **Guards:** `SandboxApi` refuses anything outside the sample models; a job demo is capped at a
  few seconds of simulated work.

### Settled while planning (2026-11-24)

- **Model-bound tables are not live in the first release.** `ui.table(Model)` reads
  `model._meta.concrete_fields` (a real Django model) and fetches its rows on the server through
  `LexTableState._fetch_page` → `lex_api(self)` → `LexApi(current_access_token(state))`, an HTTP
  call to lex-app's REST API with the signed-in user's token. It does not go through `self.api`
  or the lex.ui resolver, so the sandbox cannot answer it without a change in lex-app. Pages about
  `ui.table(Model)` show it as a plain code block; the live table demos use `@ui.rows` tables,
  which do go through `self.api`. Making model tables sandboxable (a seam in `lex_api`) is a later
  lex-app change with its own tests.
- **Sample models are plain classes with a `_meta` stand-in** (`app_label`, `model_name`). That is
  all `_resource_name` (grants), `model_id` and the `requires=` checks read.
- **Demos are components, not routes.** A demo that returns a page kind (`ui.record`, `ui.list`) is
  drawn with `ui.dashboard`; one that returns a part (`ui.table`, `ui.section`) with `ui.embed`.
  Both build without a route and without sign-in. `@ui.page` appears in code blocks only: it
  registers a signed-in route, which the docs app does not have.
- **`reflex-docgen` is pinned to 0.9.5.** 0.10.0 needs `reflex>=0.10`; RFDS 1.0.0 needs
  `reflex<0.10`, and lex-app pins `reflex==0.9.13`.
- **Search runs in the app's backend** (the site has one anyway), over an index built at start-up.
  Still no third-party service.
- **One backend process.** The per-tab sandbox store lives in the process's memory, so the site
  runs one backend worker with Reflex's in-memory state manager.

## Search

An index built at start-up from titles, headings and paragraph text, searched by a state in the
app's own backend as the reader types. No third-party service.

## CI and local checks

A `docs_site` job on PRs touching `lex/ui.py`, `lex/lex_app/reflex/**` or `docs_site/**`:

1. The app compiles (`app._compile(dry_run=True)`, as lex-app's own compiled-app tests do), with
   no network.
2. Every `exec` and `demo` block runs, and every demo returns a component.
3. `nav.py` and `content/` match, both ways.
4. Internal links and anchors resolve.

Screenshots, if added later, use headless Chrome on CPU (SwiftShader) inside bubblewrap, per this
workstation's rules. No live preview is opened in an editor or app browser pane.

## Not in this design

- Deploying the site, its domain and who may reach it.
- Versioned docs (one version per lex-app release). The site documents the commit it is built
  from; versioning comes when a second release line needs its own docs.
- Moving the Quartz content.
- Replacing the six mirrored Reflex pages on the Quartz site. Once this site is live, they become a
  short page that links here; that change is made upstream in `lex-app-docs`.
