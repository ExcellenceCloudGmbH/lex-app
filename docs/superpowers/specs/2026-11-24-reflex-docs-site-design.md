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
- A full-page demo (a function decorated with `@ui.page`, or one returning `ui.record`/`ui.list`)
  is drawn in a frame sized like a desktop or a phone, with a toggle, so the page's own chrome
  does not clash with the docs layout.

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
- **Switching grants in a demo:** a page can render a demo as another user, e.g.
  `demo(as_grants=[(Fund, "read")])` in the fence options, so permission examples really hide the
  button or refuse the action.
- **Guards:** `SandboxApi` refuses anything outside the sample models; a job demo is capped at a
  few seconds of simulated work.

### Risk to settle first

Model-bound tables (`ui.table(Model)`) may fetch rows from lex-app's REST API in the browser rather
than through `self.api`. If they do, the sandbox also needs a small fake of that endpoint inside
the docs app, answering from the same per-tab store. The first task of the plan reads
`lex/lex_app/reflex/ui/pages/tables.py` and `lex/lex_app/reflex/data.py` and settles this before
anything else is built. The same task checks what the sample "models" must be for `_resource_name`
and the table code to accept them (plain classes, or Django models in an app that never migrates).

## Search

A client-side index built at compile time from titles, headings and paragraph text, shipped as one
static JSON asset and searched in the browser. No server and no third-party service.

## CI and local checks

A `docs_site` job on PRs touching `lex/ui.py`, `lex/lex_app/reflex/**` or `docs_site/**`:

1. The app compiles (`reflex export --no-zip` or the equivalent compile step), with no network.
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
