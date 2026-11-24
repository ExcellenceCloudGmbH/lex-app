# lex.ui pages in a project's own chrome

**Status:** built 2026-11-24 on `feat/reflex-ui-project-frame` (stacked on the tables step, #912).
**Why now:** moving the reviewed customer project to lex.ui. It has its own frame (header,
navigation, footer), its own gate (who is asking and which areas they may open, resolved by a load
event before a page reads anything), its own error page and its own app module (`rx.App(...)`
built by hand, not lex-app's `create_app`). `@ui.page` put a page in lex-app's chrome and only
`create_app` registered the pages, so the project could not write one page with `@ui.page`
without changing how it looks.

## What a project writes

```python
# the project's app module, after its own pages are added
ui.chrome(lambda page, route: shell.frame(page, section=section_of(route)),
          first=lambda route: [DeskState.guard(section_of(route))])
ui.install(app)
```

- `ui.chrome(frame, first=None)`: one call for the whole project.
  - `frame(page, route)` returns the built page inside the project's frame. It is used for every
    `@ui.page` and for `/ui-error`, the page a failed load goes to.
  - `first(route)` returns the project's own load events (a list, one event or `None`). They run
    after lex-app loads the user's grants and before the opener starts the page's `@ui.load`s, so
    a load can rely on what the gate resolved.
- `ui.install(app)`: lex.ui in a project's own app. It registers the declared `@ui.page` pages,
  adds `/ui-error`, starts the clock that ends abandoned jobs (once) and checks the routes. lex-app's
  `create_app` calls it too, so the two paths cannot drift.

Without `ui.chrome` nothing changes: pages are in lex-app's chrome.

## Decisions

1. **One project-wide hook, not a `frame=` on every page.** A project has one frame; repeating it
   on each page is the kind of argument a reader has to check is the same everywhere.
   `frame` receives the route, so a frame that differs by area (a section token) works out from it.
2. **The gate is load events, not a function lex-app calls.** The project's gate already is a
   Reflex load event (`DeskState.guard(section)`); lex-app only places it. It decides what is
   shown, never what is allowed: every handler still checks its own `requires=`, and lex-app's
   API acts as the user.
3. **The error page is lex-app's, in the project's frame.** A project-specific error page would be
   a second way to show a failed load; the frame is what makes it look like the project's.
4. **Nothing checks that `first` and `frame` work for every route** at registration: they are the
   project's functions. A frame that raises stops the build with the project's own traceback.

## Not in this step

- A per-page frame override.
- A project's own error page instead of `/ui-error`.
