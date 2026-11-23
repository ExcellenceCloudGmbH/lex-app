# lex.ui Pages, Step 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project declares a record page and a list page by filling the named slots of a page kind with lex components — no styling argument, no layout component, no loading or error branch, no route string, no translation key.

**Architecture:** Every `ui.*` component is a *part*: a small object that only describes. A page function returns a *kind* (`ui.record`, `ui.list`) holding parts in named slots. lex-app builds the components from that description on RFDS's own floorplans, checks every slot, and registers the page's loads with the opener of the handler layer. `@ui.page` records the page; lex-app registers all declared pages once the project's module is loaded, so a page may link to one declared below it.

**Tech Stack:** Python 3.12, Reflex 0.9.13, RFDS 1.0.0 (floorplans `object_page`, `list_report`; `key_value`, `lozenge`, `inline_message`, `skeleton`, `data_table`), lex-app's handler layer (`lex.ui`: `ui.State`, `@ui.load`, the opener, `ui.running` / `ui.failed`), Django test runner through `lex pytest`.

**Spec:** `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. This plan is the spec's step 1 of four (section 7). Steps 2–4 get their own plans, written after this step's ports of real pages (Task 10), because those ports decide details the later steps depend on.

## Global Constraints

- **Run command** (called `RUN <file>` below), from the worktree root:
  `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd) .venv-oss/bin/python -m lex pytest <file> -v -p no:cacheprovider`
- **One test run at a time.** The test database is shared between worktrees.
- **After every run:** `rm -rf assets` (the runs create it; never commit it).
- **Worktree:** `/home/syscall/Documents/lex/.claude/worktrees/lex_ui_pages`, branch `feat/reflex-ui-pages`. `.venv-oss` there is a link to the handler layer's environment; `import lex` resolves to this worktree.
- **Tests:** cluster 1 (`init`), files `lex/test_project/tests/init/test_1c<letter>_*.py`, `pytestmark = pytest.mark.init`, a module docstring with the intent, a class `TestCluster01c<letter>_<Name>`, one scenario docstring (Given / When / Then) per test, and a human-readable message on every assertion. Scenario numbers are fixed by this plan: 1.786–1.807. Never renumber.
- **No styling arguments.** No `ui.*` component or kind takes `class_name`, `style`, spacing or `**props`.
- **Errors are part of the product.** A `PageError` names the page and the slot, says what it got, and says what to write. The tests assert the exact text.
- **No private names.** Nothing from any customer project — no name, path, model or function — in code, tests, comments, commit messages or the test plan. The design's evidence is "the reviewed customer project".
- **Git:** new commits only — never amend, never rebase, never push (the controller pushes), no `git stash`. Add explicit paths. End each commit message with a co-author line for the model that wrote it.
- **Never create or edit a `.html` file.**
- **RFDS and Reflex are read, not changed.** Their sources are under `.venv-oss/lib/python3.12/site-packages/` (`rfds`, `reflex`, `reflex_base`).

## What the prototype established

Every code block in this plan was run before the plan was written: the 22 tests below pass against it, and every Reflex batch passes with it in place (159 tests). Facts it settled:

1. **RFDS's floorplans carry the kinds.** `rfds.templates.object_page` takes a title, a path, a header component, `ObjectSection`s (an id, a title id and a title default, a content component) and actions. `rfds.templates.list_report` takes a title, a path, a table and actions. Both validate themselves.
2. **`rfds.t(sentence, sentence)` works**: RFDS accepts any non-empty string as a message id.
3. **"Not running" is not "loaded".** `ui.running(load)` is false before the load has started, so a page would show its content before its data. The handler layer gains `ui.loaded(handler)` (Task 1).
4. **A section's load must fail in place whatever the handler declares.** The handler layer decided "owns the error page" from the handler's own `optional=`. It now decides from where the load is registered for the page (Task 1).
5. **A page function cannot be called when it is decorated**: it may name a page declared below it. And lex-app needs the kind's title and loads before the app compiles. So `@ui.page` only records, and `declare.finalize()` — called by `create_app` after the project's module is loaded — calls each page function once and hands the page to `lex_page` (Task 7).
6. **RFDS places a page's actions itself**: one primary button; exactly one further action as a button; two or more further actions in a "More actions" menu. The kinds follow that rule (it replaces the spec's "more than three collapse into a menu").
7. **RFDS 1.0.0 drops a second header button.** It sorts actions with `action not in primary`, and Reflex components of one type compare equal, so a button beside a primary one vanished. The kind wraps that button in a fragment, which RFDS can tell apart (Task 6). To be reported upstream.
8. **A menu entry must be an RFDS menu item**, built by lex-app with the translated sentence; RFDS's own conversion of a button into a menu entry reads the label as plain text.
9. **`rx.redirect(address)`** is how a button opens a page: RFDS's button has no `href`.
10. **A test cannot see `on_mount` in the compiled text**; it reads `component.event_triggers`.
11. **A page with `requires=` starts no loads in the harness** unless the test answers the page-permission question (`patch.object(ui_state, "_page_permitted", return_value=True)`), because the harness's grants do not reach RFDS's identity.

## Changed by review while executing

Tasks 2 to 8 were reviewed and then fixed in four commits; the code and tests shown for them below are the fixed ones, and the later tasks are written against them:

- A value joined into an address is encoded (`links.piece`; a state value with `String(value ?? '')`); `links.info_of` refuses optional and catch-all route forms and parameter names that `ui.link` takes itself.
- `shown(ctx, component, visible)` takes the build context and accepts a permission, a non-empty list of them, `ui.signed_in`, a state value or a bool.
- `say()` raises `parts.NotASentence`, which `Part.build` turns into a `PageError`.
- `ui.fact` and `ui.column` take `currency=`; a whole number is shown without decimals, any other with two.
- `ui.column` checks its kind and its label is translated; `ui.table` is shown only to users who may read the model, and keeps the model's primary key in its rows (`lex_table(row_fields=)`).
- `Button.menu_entry(ctx)` and `Link.menu_entry(ctx)` build the action's entry in the page header's menu, keeping tone and icon.
- A part class may carry `ui_name` (its public name) and `nests` (whether it may sit inside a section).
- Facts that follow one another in a section become one key-value list; a section inside a section and `empty=` without a table are refused; a section hands `empty=` to a copy of a table.
- The kinds build their own parts first and then call RFDS's floorplan inside a guard: what RFDS refuses becomes a `PageError` that names the page. Two sections with one title, a blank title and an action called Save are refused by lex first.
- On a record page a section's `visible=` takes a permission, which RFDS shows as a heading with the reason; a state value there is refused with the advice to use `ui.switch` inside the section.
- A link or button keeps RFDS's marks when `visible=` wraps it (`Part._wrap`), so a primary action with `visible=` stays primary; the kind marks the first action primary on a copy.
- `@ui.page` checks `requires=`, `params=` and a route taken twice when the page is declared; `finalize()` checks that every `load=` is a `@ui.load` handler given once, and wraps an error from a page function in a `PageError`. `@ui.dashboard` passes its arguments through, so it works under `@classmethod`.
- Task 7's test names `tables.table`, because `ui.table` is exported in Task 8. Task 8 was written by the controller while checking the plan's code against the fixed tree.
- Task 8's review: the example page's section load reads what the page load wrote, so the end-to-end test proves their order; `ui.list` is left out of `__all__`; an RFDS refusal keeps its cause and lex's checks of title and actions run first.

## Not in this step

- **A table over rows held in a state**, and **filters**. RFDS's paged table needs a table state of its own, and how such a table stays fresh when a handler changes the rows is a design question the port of a search list (Task 10) will inform. `ui.table` takes a model in this step. Both move to step 2.
- `ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=`, the button that knows its handler, `ui.goto(page)` — step 2, as in the spec.
- `ui.overview`, `ui.figure`, `ui.tile`, `ui.chart`, `ui.grid` — step 3.
- `lex ui messages`, the new `lex reflex check` rules, `ui.testing.page`, the scaffold, the reference example — step 4.
- `kind=` in this step is one of `text`, `number`, `money`, `percent`, `datetime`. `date`, `year`, `bool` and `choice` arrive with the form fields in step 2.

## File structure

```text
lex/lex_app/reflex/ui/pages/
    __init__.py     the package's docstring
    parts.py        Part, Build, PageError, say(), shown(), take()        (Task 2)
    vocab.py        text, fact, status, message                           (Task 2)
                    link, button, menu_entry                              (Task 3)
                    custom_block, each, switch, CONTENT, build_all        (Task 5)
    links.py        PageInfo, info_of, href, row_href                     (Task 3)
    tables.py       table, column                                         (Task 4)
    kinds.py        states, section                                       (Task 5)
                    record, list_, Kind                                   (Task 6)
    declare.py      page, dashboard, finalize                             (Task 7)
lex/lex_app/reflex/ui/state.py, runs.py, handlers.py   ui_loaded          (Task 1)
lex/lex_app/reflex/app.py                              finalize()         (Task 7)
lex/ui.py                                              the public names   (Tasks 1, 8)
```

One file, one responsibility: `parts.py` knows nothing of RFDS's floorplans; `kinds.py` is the only file that calls them; `links.py` is the only file that builds an address; `declare.py` is the only file that talks to `lex_page`.

---

### Task 1: `ui.loaded`, and the page registry decides who owns the error page

**Files:**
- Modify: `lex/lex_app/reflex/ui/state.py`, `lex/lex_app/reflex/ui/runs.py`, `lex/lex_app/reflex/ui/handlers.py`, `lex/ui.py`, `lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py`
- Test: `lex/test_project/tests/init/test_1cn_ui_loaded.py`

**Interfaces:**
- Consumes: the handler layer's `State.ui_open(page)`, `handlers.register_page(route, required, optional, requires)`, `handlers.page_of(route)`, `handlers.load_id(handler)`, `runs.key_of(handler)`, `testing.Harness`.
- Produces: `State.ui_loaded: dict[str, bool]` (load key → finished); `runs.loaded(handler)` → a view value; `ui.loaded`; a load started by the opener owns the error page only if it is in the page's *required* list.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cn_ui_loaded.py`:

```python
"""lex.ui: a load says when it has finished, and its page decides whether it owns the error page.

Intent: a page shows a skeleton until its load has finished, so the layer has to say "finished" --
"not running" is also true before the load has started, which showed content before any data.
And a page kind starts the loads of its sections as the page's optional loads whatever the
handler declares, so where a load is registered, not its own ``optional=``, decides whether its
failure takes over the page. A regression is a page that flashes its empty content before
loading, or a failed section that replaces the whole page with the error page.

Cluster 1cn -- scenarios 1.786-1.787. Type: I.
Covers: lex/lex_app/reflex/ui/state.py (ui_loaded, ui_open), ui/runs.py (loaded),
ui/handlers.py (load).
Run: python -m lex pytest lex/test_project/tests/init/test_1cn_ui_loaded.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import handlers, runs, testing
from lex.lex_app.reflex.ui import state as ui_state

pytestmark = pytest.mark.init

#: Open while the test lets the required load finish; closed, the load waits, so the test can look mid-run.
GATE = threading.Event()


class LoadedDesk(ui.State):
    """One required load that succeeds, one section-style load that refuses."""

    rows: list[str] = []
    notes: list[str] = []

    @ui.load
    def open(self):
        GATE.wait(5)
        self.rows = ["a"]

    @ui.load
    def load_notes(self):
        raise ui.Refuse("Notes are not available.")


async def _open(h: testing.Harness, route: str) -> dict:
    opener = await h.send(ui.State, "ui_open", page=route)
    await opener
    for child in list(opener.children):
        await child
        for grandchild in list(child.children):
            await grandchild
    return await h.fields(ui.State)


class TestCluster01cn_Loaded(SimpleTestCase):
    """1.786-1.787: the loaded status, and the page registry deciding who owns the error page."""

    def setUp(self) -> None:
        pages = patch.dict(handlers._PAGES)
        pages.start()
        self.addCleanup(pages.stop)
        permitted = patch.object(ui_state, "_page_permitted", return_value=True)
        permitted.start()
        self.addCleanup(permitted.stop)

    # -- 1.786 ---------------------------------------------------------
    def test_1_786_a_load_says_when_it_has_finished(self) -> None:
        """
        Scenario 1.786: the loaded status.
        Given: a page whose required load succeeds.
        When:  the page is opened, and opened again.
        Then:  ui.loaded(handler) is a view value that is false until the load has finished and
               true afterwards; opening the page again puts it back to false before the load
               runs, so a revisit shows the skeleton and not the last visit's content.
        """
        handlers.register_page("/desk", [LoadedDesk.open], [], "")
        self.assertIn("ui_loaded", str(runs.loaded(LoadedDesk.open)), "ui.loaded reads the state's ui_loaded")
        self.assertIs(ui.loaded, runs.loaded, "exported as ui.loaded")

        async def scenario():
            async with testing.Harness() as h:
                await h.open_tab("/desk")
                GATE.set()
                first = await _open(h, "/desk")
                GATE.clear()                                   # the second visit's load waits
                opener = await h.send(ui.State, "ui_open", page="/desk")
                await opener
                during = await h.fields(ui.State)
                GATE.set()
                for child in list(opener.children):
                    await child
                return first, during, await h.fields(ui.State)

        self.addCleanup(GATE.set)
        first, during, second = asyncio.run(scenario())
        self.assertEqual(first["ui_loaded"], {"LoadedDesk.open": True}, "true once the load has finished")
        self.assertEqual(during["ui_loaded"], {"LoadedDesk.open": False},
                         "opening the page again puts it back to false while the load runs")
        self.assertEqual(second["ui_loaded"], {"LoadedDesk.open": True}, "and true again after the second run")

    # -- 1.787 ---------------------------------------------------------
    def test_1_787_where_a_load_is_registered_decides_whether_it_owns_the_error_page(self) -> None:
        """
        Scenario 1.787: a section's load fails in place.
        Given: a page that registers one load as required and another -- not declared
               optional= -- as optional, the way a page kind registers a section's load.
        When:  the page is opened and the second load refuses.
        Then:  its message is in ui_failed, it is not marked loaded, and neither the error page
               nor a redirect follows: the page stays, the section reports in place.
        """
        handlers.register_page("/desk", [LoadedDesk.open], [LoadedDesk.load_notes], "")

        async def scenario():
            async with testing.Harness() as h:
                await h.open_tab("/desk")
                GATE.set()
                return await _open(h, "/desk"), [r for r in h.results if isinstance(r, ui.goto("/x").__class__)]

        fields, gotos = asyncio.run(scenario())
        self.assertEqual(fields["ui_failed"], {"LoadedDesk.load_notes": "Notes are not available."},
                         "the refusal is recorded for the view")
        self.assertEqual(fields["ui_loaded"], {"LoadedDesk.open": True, "LoadedDesk.load_notes": False},
                         "a failed load is not loaded")
        self.assertEqual((fields["ui_error"], gotos), ({}, []), "no error page: the load was registered as optional")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cn_ui_loaded.py`
Expected: `AttributeError: module 'lex.lex_app.reflex.ui.runs' has no attribute 'loaded'`.

- [ ] **Step 3: Implement**

Apply these edits to the handler layer (three files):

```diff
diff --git a/lex/lex_app/reflex/ui/handlers.py b/lex/lex_app/reflex/ui/handlers.py
index 07fea644..f1f7da78 100644
--- a/lex/lex_app/reflex/ui/handlers.py
+++ b/lex/lex_app/reflex/ui/handlers.py
@@ -621,6 +621,8 @@ def load(fn: Callable[..., Any] | None = None, *, requires: Any = None, optional
                     message = outcome.message if outcome is not None and current else ""
                     if started:
                         runs.mark_finished(self, key, arg, error=message)
+                    if current and succeeded:
+                        self.ui_loaded = {**dict(self.ui_loaded), key: True}
                     elif message:
                         self.ui_failed = {**dict(self.ui_failed), key: message}
                     # Started on mount, or optional: the failure stays in place, in ui_failed.
@@ -678,7 +680,10 @@ def load(fn: Callable[..., Any] | None = None, *, requires: Any = None, optional
                             else:
                                 runs.mark_started(self, key, arg)
                                 started = True
-                    page_bound = by_opener and not optional
+                        self.ui_loaded = {**dict(self.ui_loaded), key: False}
+                        entry = page_of(self._ui_page) if by_opener else None
+                        required_here = ident in {load_id(h) for h in (entry.required if entry else ())}
+                    page_bound = by_opener and required_here
                     if failure is not None:
                         raise failure
                     inv = context.Invocation(handler=key, kind="load", session=session,
diff --git a/lex/lex_app/reflex/ui/runs.py b/lex/lex_app/reflex/ui/runs.py
index 02171805..c7bf2652 100644
--- a/lex/lex_app/reflex/ui/runs.py
+++ b/lex/lex_app/reflex/ui/runs.py
@@ -138,6 +138,13 @@ def running(handler: Any, arg: Any = None) -> Any:
     return active.length() > 0 if arg is None else active.contains(_as_text(arg))
 
 
+def loaded(handler: Any) -> Any:
+    """In a view: whether the load ``handler`` has finished since the page opened."""
+    from lex.lex_app.reflex.ui.state import State
+
+    return State.ui_loaded.get(key_of(handler), False)
+
+
 def failed(handler: Any) -> Any:
     """In a view: the failure message of ``handler``'s last run, or ''."""
     from lex.lex_app.reflex.ui.state import State
diff --git a/lex/lex_app/reflex/ui/state.py b/lex/lex_app/reflex/ui/state.py
index 56f24175..6886c784 100644
--- a/lex/lex_app/reflex/ui/state.py
+++ b/lex/lex_app/reflex/ui/state.py
@@ -75,6 +75,8 @@ class State(rx.State):
     #: Handler key -> the failure message of its last run.
     ui_failed: dict[str, str] = {}
     ui_progress: dict[str, float] = {}
+    #: Load key -> whether its last run finished; a page shows its skeleton until then.
+    ui_loaded: dict[str, bool] = {}
     ui_progress_message: dict[str, str] = {}
     #: Counts page openings in this tab; loads and jobs compare it with the visit they started in.
     ui_visit: int = 0
@@ -121,6 +123,11 @@ class State(rx.State):
         if entry is None or not _page_permitted(self, entry.requires):
             self._ui_pending_loads, self._ui_optional_loads, self._ui_started = [], [], []
             return None
+        from lex.lex_app.reflex.ui import runs as ui_runs
+
+        # A page shows its skeleton until its loads have finished in this visit.
+        self.ui_loaded = {**dict(self.ui_loaded),
+                          **{ui_runs.key_of(h): False for h in (*entry.required, *entry.optional)}}
         self._ui_pending_loads = [handlers.load_id(h) for h in entry.required]
         self._ui_optional_loads = [handlers.load_id(h) for h in entry.optional] if entry.required else []
         starting = list(entry.required or entry.optional)
```

In `lex/ui.py`, add `loaded` to the import from `runs` and to `__all__`:

```python
from lex.lex_app.reflex.ui.runs import cancel, failed, loaded, progress_of, running
```

and add `"loaded",` to the `__all__` list (keep it sorted). Add one row to the module docstring's table, under the row for `ui.running/failed/`:

```text
``ui.loaded``               whether a load has finished since the page opened
```

`test_1cm` pins the namespace; it must allow the names this plan adds. Apply:

```diff
diff --git a/lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py b/lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py
index 82464e9a..339dc1d7 100644
--- a/lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py
+++ b/lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py
@@ -159,7 +159,10 @@ class TestCluster01cm_Namespace(TestCase):
         }
         for name, obj in expected.items():
             self.assertIs(getattr(ui, name), obj, f"ui.{name}")
-        self.assertEqual(sorted(ui.__all__), sorted(expected), "nothing else is public")
+        pages = {"page", "dashboard", "record", "list", "section", "text", "fact", "status", "message", "table", "column",
+                 "button", "link", "each", "switch", "custom_block", "loaded", "PageError"}   # batch 1cu covers these
+        self.assertEqual(sorted(set(ui.__all__) - pages), sorted(expected),
+                         "nothing else is public beside the page names")
 
     # -- 1.784 ---------------------------------------------------------
     def test_1_784_the_scaffold_templates_are_written_on_the_layer(self) -> None:
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cn_ui_loaded.py`
Expected: 2 passed. Then run, one at a time, `RUN lex/test_project/tests/init/test_1cj_ui_load.py` (12 passed), `RUN lex/test_project/tests/init/test_1cg_ui_state_and_status.py` (3 passed) and `RUN lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py` (3 passed): the handler layer is unchanged for pages declared with `lex_page`.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/state.py lex/lex_app/reflex/ui/runs.py lex/lex_app/reflex/ui/handlers.py lex/ui.py lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py lex/test_project/tests/init/test_1cn_ui_loaded.py
git commit -m "feat(reflex): ui.loaded - a load says when it has finished; the page registry decides who owns the error page"
```

End the message with the co-author line for your own model.

---

### Task 2: Parts, sentences, and the first four components

**Files:**
- Create: `lex/lex_app/reflex/ui/pages/__init__.py`, `lex/lex_app/reflex/ui/pages/parts.py`, `lex/lex_app/reflex/ui/pages/vocab.py`
- Test: `lex/test_project/tests/init/test_1co_ui_page_parts.py`

**Interfaces:**
- Consumes: `guard.Requirement` (what `ui.read(Model)` returns: `.target`, `.scope`), `access.permission(target, scope)`, `rfds.auth.can`, `rfds.t`, `rfds.kv_item`, `rfds.lozenge`, `rfds.inline_message`, `rfds.number`, `rfds.money`, `rfds.percent`, `rfds.timestamp`.
- Produces: `parts.PageError`; `parts.Build(page, route=None, slot="", place="body", mounts=False)` with `.at(slot, place=None)` and `.fail(problem, instead)`; `parts.Part` with `.visible` and `.build(ctx)`; `parts.say(sentence, **values)`; `parts.shown(component, visible)`; `parts.slug(text)`; `parts.take(ctx, value, kinds, instead) -> list`; `vocab.text`, `vocab.fact`, `vocab.status`, `vocab.message` and their classes `Text`, `Fact`, `Status`, `Message`; `vocab.TONES`, `vocab.KINDS`, `vocab.shape(value, kind)`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1co_ui_page_parts.py`:

```python
"""lex.ui pages: parts describe, sentences are translated, and a wrong part is named.

Intent: a page is made of parts that only describe what is on it; lex-app builds the components.
That is what lets a slot refuse anything outside the vocabulary with a message that says what to
write, lets the author write the sentence the user reads (lex-app passes it to RFDS's translation
as both id and default), and lets a value say what it is (kind=) and who sees it (visible=)
without a styling argument anywhere. A regression is a raw component slipping into a slot, an
untranslated label, or an error that does not say what to do.

Cluster 1co -- scenarios 1.788-1.790. Type: U.
Covers: lex/lex_app/reflex/ui/pages/parts.py, ui/pages/vocab.py (text, fact, status, message).
Run: python -m lex pytest lex/test_project/tests/init/test_1co_ui_page_parts.py -v
"""

from __future__ import annotations

import inspect

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import parts, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class PartsDesk(ui.State):
    name: str = ""
    amount: float = 0.0
    count: int = 0
    stamp: str = ""
    open_now: bool = False


CTX = Build(page="fund", route="/funds/[key]")


class TestCluster01co_PageParts(SimpleTestCase):
    """1.788-1.790: sentences, values and visibility; the slot check."""

    # -- 1.788 ---------------------------------------------------------
    def test_1_788_the_sentence_is_the_message(self) -> None:
        """
        Scenario 1.788: text.
        Given: ui.text, ui.message and ui.status written with English sentences, one with a
               named value, and a text whose content is a state value.
        When:  they are built.
        Then:  each sentence reaches RFDS's translation as both the id and the English default,
               with its values; a state value is shown as it is, untranslated; a tone outside
               the five is refused when the part is declared.
        """
        built = str(vocab.text("{count} open requests", count=3).build(CTX))
        self.assertIn('rfdsI18n.t("{count} open requests", "{count} open requests", {"count": 3})', built,
                      "the sentence is the id and the default; values are passed")
        self.assertIn('rfdsI18n.t("Careful", "Careful"', str(vocab.message("Careful", tone="warning").build(CTX)),
                      "a message's sentence is translated")
        self.assertIn('rfdsI18n.t("Open", "Open"', str(vocab.status("Open", tone="info").build(CTX)),
                      "a status word is translated")
        shown = str(vocab.text(PartsDesk.name).build(CTX))
        self.assertIn("name_rx_state_", shown, "a state value is shown")
        self.assertNotIn("rfdsI18n", shown, "and never translated")
        with self.assertRaises(ValueError, msg="a tone outside the five is refused"):
            vocab.status("Open", tone="blue")
        with self.assertRaises(PageError) as no_words:
            vocab.text(3).build(CTX)
        self.assertEqual(str(no_words.exception),
                         "Page `fund`: `ui.text` got `3` where its words belong.\n"
                         "Write the sentence the user reads, in English: `ui.text(\"3 open requests\")`. "
                         "A state value is shown as it is.", "something that is not a sentence is named, with what to write")
        with self.assertRaises(PageError, msg="an empty label is not a sentence either"):
            vocab.fact("", 1).build(CTX)

    # -- 1.789 ---------------------------------------------------------
    def test_1_789_a_value_says_what_it_is_and_who_sees_it(self) -> None:
        """
        Scenario 1.789: kind= and visible=.
        Given: facts of each kind, and parts with visible= a permission and a state value.
        When:  they are built.
        Then:  each kind goes through RFDS's own formatter for it; a whole number is shown
               without decimals and any other with two; money takes a currency; an unknown
               kind, and a currency without kind="money", are refused when the fact is
               declared. visible= takes a permission (RFDS's check, with the string a page's
               requires= gives), a list of them (all must hold), ui.signed_in (always shown),
               or a state value; anything else, and an empty list, is refused with what to
               write. No component of the vocabulary takes a styling argument.
        """
        money = str(vocab.fact("Net asset value", PartsDesk.amount, kind="money", currency="EUR").build(CTX))
        self.assertIn('rfdsI18n.t("Net asset value"', money, "the label is a sentence")
        self.assertIn('"data-slot":"rfds-money"', money, "money goes through RFDS's money formatter")
        self.assertIn("EUR", money, "in the currency given")
        for kind, value, mark in (("number", PartsDesk.amount, "rfds-number"), ("percent", PartsDesk.amount, "rfds-percent"),
                                  ("datetime", PartsDesk.stamp, "rfds-timestamp"), ("money", PartsDesk.amount, "rfds-money")):
            with self.subTest(kind=kind):
                self.assertIn(f'"data-slot":"{mark}"', str(vocab.fact("X", value, kind=kind).build(CTX)),
                              f"kind={kind} uses RFDS's formatter for it")
        self.assertNotIn("data-slot\":\"rfds-number", str(vocab.fact("Name", PartsDesk.name).build(CTX)),
                         "text is shown as it is")
        self.assertEqual((vocab._whole(3), vocab._whole(PartsDesk.count), vocab._whole(2.5), vocab._whole(PartsDesk.amount),
                          vocab._whole(True)), (True, True, False, False, False),
                         "a whole number is an int or a state value typed int")
        whole, decimal = (str(vocab.shape(value, "number")) for value in (PartsDesk.count, PartsDesk.amount))
        self.assertNotEqual(whole.replace("count", "amount"), decimal, "whole numbers and decimals are formatted differently")
        with self.assertRaises(ValueError, msg="an unknown kind is refused"):
            vocab.fact("X", 1, kind="colour")
        with self.assertRaises(ValueError, msg="a currency goes with money only"):
            vocab.fact("X", 1, kind="number", currency="EUR")

        guarded = str(vocab.text("Editors only", visible=ui.edit(Group)).build(CTX))
        self.assertIn("auth.Group:edit", guarded, "a permission in visible= is checked with the page's permission string")
        stateful = str(vocab.text("While open", visible=PartsDesk.open_now).build(CTX))
        self.assertIn("open_now_rx_state_", stateful, "a state value in visible= conditions the part")
        plain_text = str(vocab.text("While open").build(CTX))
        self.assertNotIn("open_now_rx_state_", plain_text, "and only when given")
        both = str(vocab.text("While open", visible=[ui.read(Group), ui.edit(Group)]).build(CTX))
        self.assertIn("auth.Group:read", both, "a list of permissions: the first must hold")
        self.assertIn("auth.Group:edit", both, "and the second")
        self.assertEqual(str(vocab.text("While open", visible=ui.signed_in).build(CTX)), plain_text,
                         "every user of the dashboards is signed in: always shown")
        with self.assertRaises(PageError) as wrong:
            vocab.text("While open", visible="editors").build(CTX)
        self.assertEqual(str(wrong.exception),
                         "Page `fund`: `visible=` got `'editors'`.\nGive a permission -- `visible=ui.edit(Model)` -- "
                         "or a state value: `visible=State.is_open`.", "anything else is refused with what to write")
        with self.assertRaises(PageError) as empty:
            vocab.text("While open", visible=[]).build(CTX)
        self.assertEqual(str(empty.exception),
                         "Page `fund`: `visible=` got an empty list.\nList at least one permission: "
                         "`visible=[ui.read(Model)]`.", "an empty list is refused, not shown to everyone")

        for factory in (vocab.text, vocab.fact, vocab.status, vocab.message):
            with self.subTest(component=factory.__name__):
                names = set(inspect.signature(factory).parameters)
                self.assertFalse(names & {"class_name", "style", "props", "gap", "width", "padding"},
                                 f"ui.{factory.__name__} takes no styling argument")

    # -- 1.790 ---------------------------------------------------------
    def test_1_790_a_slot_names_what_it_refuses_and_what_to_write(self) -> None:
        """
        Scenario 1.790: the slot check.
        Given: a list slot that accepts facts.
        When:  it is given a raw component, another lex component, and nothing.
        Then:  the error names the page and the slot, says what it got, and says what to write
               instead; a single part and a list are both accepted; nothing gives an empty list.
        """
        ctx = CTX.at("facts")
        instead = "Use `ui.fact(label, value)`."
        with self.assertRaises(PageError) as raw:
            parts.take(ctx, [rx.text("x")], (vocab.Fact,), instead)
        self.assertEqual(str(raw.exception),
                         "Page `fund`, slot `facts`: got a raw component (`Text`).\nUse `ui.fact(label, value)`.",
                         "the message names page, slot, what it got and what to write")
        with self.assertRaises(PageError) as other:
            parts.take(ctx, [vocab.status("Open")], (vocab.Fact,), instead)
        self.assertIn("got `ui.status`", str(other.exception), "another lex component is named by its ui name")
        with self.assertRaises(PageError) as nothing:
            parts.take(ctx, [None], (vocab.Fact,), instead)
        self.assertIn("got nothing (`None`).", str(nothing.exception), "a None inside the list is named as nothing")
        self.assertTrue(parts.called("x" * 200).endswith("...`"), "a long value is cut before its closing mark")
        one = vocab.fact("A", 1)
        self.assertEqual((parts.take(ctx, one, (vocab.Fact,), instead), parts.take(ctx, [one], (vocab.Fact,), instead),
                          parts.take(ctx, None, (vocab.Fact,), instead)), ([one], [one], []),
                         "one part, a list and nothing are all accepted")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1co_ui_page_parts.py`
Expected: `ModuleNotFoundError: No module named 'lex.lex_app.reflex.ui.pages'`.

- [ ] **Step 3: Implement**

`lex/lex_app/reflex/ui/pages/__init__.py`:

```python
"""lex.ui pages: page kinds with named slots, filled from a closed vocabulary.

Import the public names from :mod:`lex.ui`. Design:
``docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md``.
"""
```

`lex/lex_app/reflex/ui/pages/parts.py`:

```python
"""What a page is made of: parts that describe, and the context they are built in."""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Any, ClassVar

import reflex as rx


class PageError(Exception):
    """A page is declared wrongly. The message names the page and the slot and says what to write."""


@dataclasses.dataclass
class Build:
    """Where a part is being built: which page, which slot, and how things there look."""

    page: str                           # the page function's name
    route: str | None = None            # None on a dashboard
    slot: str = ""                      # the slot being filled, for messages
    place: str = "body"                 # "body" or "actions": a link in actions is a button
    mounts: bool = False                # a dashboard: a load starts when its part mounts

    def at(self, slot: str, place: str | None = None) -> "Build":
        return dataclasses.replace(self, slot=slot, place=place or self.place)

    def fail(self, problem: str, instead: str) -> PageError:
        where = f"Page `{self.page}`" + (f", slot `{self.slot}`" if self.slot else "")
        return PageError(f"{where}: {problem}\n{instead}")


class NotASentence(TypeError):
    """A text argument is not a sentence. :meth:`Part.build` turns it into a :class:`PageError` for its page."""

    def __init__(self, value: Any):
        super().__init__(f"expected a sentence, got {value!r}")
        self.value = value


class Part:
    """Something a slot accepts. A part only describes; :meth:`build` makes the component."""

    #: The public name, when it is not the class name in lower case (``custom_block``).
    ui_name: ClassVar[str] = ""
    #: Whether the part may sit inside a section. A section may not: sections do not nest.
    nests: ClassVar[bool] = True
    visible: Any = None

    def build(self, ctx: Build) -> rx.Component:
        try:
            component = self._render(ctx)
        except NotASentence as wrong:
            raise ctx.fail(f"{called(self)} got {called(wrong.value)} where its words belong.",
                           "Write the sentence the user reads, in English: `ui.text(\"3 open requests\")`. "
                           "A state value is shown as it is.") from None
        return self._wrap(ctx, component)

    def _render(self, ctx: Build) -> rx.Component:
        raise NotImplementedError

    def _wrap(self, ctx: Build, component: rx.Component) -> rx.Component:
        """``component`` as the page shows it: only to whom, and when, ``visible`` says."""
        return shown(ctx, component, self.visible)


def shown(ctx: Build, component: rx.Component, visible: Any) -> rx.Component:
    """``component``, shown only when ``visible`` holds.

    ``visible`` is a permission (what ``requires=`` takes: ``ui.read(Model)``...), a list of
    them (all must hold), a state value, or a plain ``True``/``False``. It decides what is
    shown, never what is allowed: the handler still enforces.
    """
    from lex.lex_app.reflex.ui import guard

    if visible is None or visible is guard.signed_in:      # every user of the dashboards is signed in
        return component
    if isinstance(visible, guard.Requirement):
        from rfds.auth import can

        from lex.lex_app.reflex.access import permission

        return can(permission(visible.target, visible.scope), component)
    if isinstance(visible, (list, tuple)):
        if not visible:
            raise ctx.fail("`visible=` got an empty list.", "List at least one permission: `visible=[ui.read(Model)]`.")
        for one in visible:
            component = shown(ctx, component, one)
        return component
    if isinstance(visible, (bool, rx.Var)):
        return rx.cond(visible, component)
    raise ctx.fail(f"`visible=` got {called(visible)}.",
                   "Give a permission -- `visible=ui.edit(Model)` -- or a state value: `visible=State.is_open`.")


def say(sentence: Any, **values: Any) -> Any:
    """A sentence the user reads, translated in the browser. A state value is shown as it is.

    The sentence is the message: it is passed to RFDS's translation as the id and as the English
    text. ``{name}`` in it is filled from ``values``; a literal brace is written ``'{'``.
    """
    if isinstance(sentence, rx.Var):
        return sentence
    if not isinstance(sentence, str) or not sentence:
        raise NotASentence(sentence)
    import rfds

    return rfds.t(sentence, sentence, **values)


def slug(text: str) -> str:
    """``text`` as an id: its letters without accents, in lower case, joined by dashes."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^0-9a-z]+", "-", plain.lower()).strip("-") or "part"


def called(value: Any) -> str:
    """How an unexpected value is named in a message."""
    if isinstance(value, rx.Component):
        return f"a raw component (`{type(value).__name__}`)"
    if isinstance(value, Part):
        return f"`ui.{type(value).ui_name or type(value).__name__.lower()}`"
    if value is None:
        return "nothing (`None`)"
    text = repr(value)
    return f"`{text[:60]}...`" if len(text) > 60 else f"`{text}`"


def take(ctx: Build, value: Any, kinds: tuple[type, ...], instead: str) -> list:
    """The parts given for a list slot, checked: each must be one of ``kinds``."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if not isinstance(item, kinds):
            raise ctx.fail(f"got {called(item)}.", instead)
    return items
```

`lex/lex_app/reflex/ui/pages/vocab.py`:

```python
"""The vocabulary: the components a slot accepts. None takes a styling argument."""

from __future__ import annotations

import dataclasses
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui.pages.parts import Build, NotASentence, Part, called, say, shown

TONES = ("neutral", "info", "success", "warning", "danger")
KINDS = ("text", "number", "money", "percent", "datetime")


def _tone(tone: str) -> str:
    if tone not in TONES:
        raise ValueError(f"tone= must be one of {', '.join(TONES)}; got {tone!r}")
    return tone


def _whole(value: Any) -> bool:
    """Whether ``value`` is a whole number: an int, or a state value typed int."""
    if isinstance(value, rx.Var):
        return value._var_type is int
    return isinstance(value, int) and not isinstance(value, bool)


def shape(value: Any, kind: str, currency: Any = None) -> Any:
    """``value`` shown as its ``kind``, formatted for the user's locale."""
    import rfds

    if kind == "text":
        return value
    if kind == "number":
        return rfds.number(value, digits=0 if _whole(value) else 2)
    if kind == "money":
        return rfds.money(value, currency)
    if kind == "percent":
        return rfds.percent(value)
    if kind == "datetime":
        return rfds.timestamp(value)
    raise ValueError(f"kind= must be one of {', '.join(KINDS)}; got {kind!r}")


@dataclasses.dataclass(eq=False)
class Text(Part):
    sentence: Any
    values: dict
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        return rx.text(say(self.sentence, **self.values))


def text(sentence: Any, *, visible: Any = None, **values: Any) -> Text:
    """A sentence or a paragraph, written in English; it is translated for the user.

    ``{name}`` in it is filled from ``values`` (a value cannot be called ``visible``). A state
    value is shown as it is.
    """
    return Text(sentence, values, visible)


@dataclasses.dataclass(eq=False)
class Fact(Part):
    label: Any
    value: Any
    kind: str = "text"
    visible: Any = None
    currency: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.kv_item(say(self.label), shape(self.value, self.kind, self.currency))


def fact(label: Any, value: Any, *, kind: str = "text", currency: Any = None, visible: Any = None) -> Fact:
    """A label and its value.

    ``kind`` says what the value is, and lex-app formats it for the user's locale:
    ``"number"`` (a whole number without decimals, any other with two), ``"money"`` (in
    ``currency=``, a code such as ``"EUR"`` or a state value; else the home currency),
    ``"percent"`` (a ratio: 0.6 shows 60%), ``"datetime"`` (an instant with its time zone, shown
    in the user's), or ``"text"``.
    """
    if kind not in KINDS:
        raise ValueError(f"kind= must be one of {', '.join(KINDS)}; got {kind!r}")
    if currency is not None and kind != "money":
        raise ValueError(f"ui.fact(currency=) goes with kind=\"money\"; got kind={kind!r}")
    return Fact(label, value, kind, visible, currency)


@dataclasses.dataclass(eq=False)
class Status(Part):
    word: Any
    tone: str = "neutral"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.lozenge(say(self.word), tone=self.tone)


def status(word: Any, *, tone: str = "neutral", visible: Any = None) -> Status:
    """A small badge."""
    return Status(word, _tone(tone), visible)


@dataclasses.dataclass(eq=False)
class Message(Part):
    sentence: Any
    values: dict
    tone: str = "info"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.inline_message(say(self.sentence, **self.values), tone=self.tone)


def message(sentence: Any, *, tone: str = "info", visible: Any = None, **values: Any) -> Message:
    """A notice in place. ``{name}`` in the sentence is filled from ``values`` (not ``tone`` or ``visible``)."""
    return Message(sentence, values, _tone(tone), visible)


def _marked(inner: rx.Component, wrapper: rx.Component) -> rx.Component:
    """``wrapper`` carrying the RFDS metadata of the control it wraps.

    The one place lex.ui reaches into RFDS's private metadata. RFDS's page header reads a
    control's kind and intent from it -- which action is the primary one, what is a menu entry --
    and a ``visible=`` wrapper has none of its own, so the control would lose its role.
    """
    from rfds.components.actions._common import get_meta, set_meta

    return wrapper if wrapper is inner else set_meta(wrapper, **get_meta(inner))
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1co_ui_page_parts.py`
Expected: 3 passed, 7 subtests passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/test_project/tests/init/test_1co_ui_page_parts.py
git commit -m "feat(reflex): lex.ui pages - parts that describe, translated sentences, text/fact/status/message"
```

End the message with the co-author line for your own model.

---

### Task 3: Links by page, `ui.link`, `ui.button`

**Files:**
- Create: `lex/lex_app/reflex/ui/pages/links.py`
- Modify: `lex/lex_app/reflex/ui/pages/vocab.py` (append)
- Test: `lex/test_project/tests/init/test_1cp_ui_page_links.py`

**Interfaces:**
- Consumes: `parts.Build`, `parts.Part`, `parts.say`, `parts.shown`; `embed.lex_app_base_url()`; `rfds.link`, `rfds.button`, `rfds.components.actions.menu.menu_item`, `rfds.components.actions._common.get_meta/set_meta`; `rx.redirect`.
- Produces: `links.PageInfo(name, route, arguments, params)`; `links.info_of(route, name, params=())`; `links.page_info(fn)` (reads `fn.__ui_page__`); `links.href(ctx, fn, values) -> str | Var`; `links.row_href(ctx, fn, row_key) -> str`; `links.as_text(value)`; `vocab.link(label, *, to=, record=, href=, visible=, **values)` → `Link` with `.target(ctx) -> (address, outside)` and `.click(ctx)`; `vocab.button(label, handler=None, *, to=, tone=, icon=, visible=, **values)` → `Button` with `.primary`, `.tone`, `.label`, `.visible`, `.click(ctx)`; `vocab.menu_entry(ctx, label, click, visible)`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cp_ui_page_links.py`:

```python
"""lex.ui pages: a link names a page function or a record, never a route string.

Intent: routes copied between modules break silently when one is renamed. A link points at the
page function; its parameters are checked against the page's route and declared query
parameters when the page is built, and the error names both pages. A button that opens a page
follows the same rule, and an outside site is the only place an address is written out. A
regression is a link that builds a wrong address without complaint, or an in-app link that
opens a new tab.

Cluster 1cp -- scenarios 1.791-1.793. Type: U.
Covers: lex/lex_app/reflex/ui/pages/links.py, ui/pages/vocab.py (link, button, menu_entry).
Run: python -m lex pytest lex/test_project/tests/init/test_1cp_ui_page_links.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import links, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class LinksDesk(ui.State):
    fund: str = ""
    count: int = 0
    tags: list[str] = []

    @ui.action
    def close(self):
        return ui.toast("Closed")


def fund_page():
    """Stands for a page declared with @ui.page("/funds/[key]", params=["tab"])."""


fund_page.__ui_page__ = links.info_of("/funds/[key]", "fund_page", ["tab"])


def funds_page():
    """Stands for a page declared with @ui.page("/funds")."""


funds_page.__ui_page__ = links.info_of("/funds", "funds_page")

CTX = Build(page="home", route="/")


class TestCluster01cp_PageLinks(SimpleTestCase):
    """1.791-1.793: addresses from page functions; the three kinds of link; buttons."""

    # -- 1.791 ---------------------------------------------------------
    def test_1_791_an_address_is_built_from_the_page_and_its_parameters(self) -> None:
        """
        Scenario 1.791: links.href.
        Given: a page with one route argument and one declared query parameter.
        When:  an address is asked for with fixed values, with state values, with a missing
               argument, with an unknown parameter, and for something that is not a page.
        Then:  fixed values give a plain address; state values give an expression that joins
               them into it (a number is turned into text); a missing or unknown parameter is
               an error naming the linking page, the target page, its route and what it takes;
               a function that is not a page is refused with what to pass instead; a table's
               row link is the route with its one argument replaced by the row's key.
        """
        self.assertEqual(links.href(CTX, fund_page, {"key": "7"}), "/funds/7", "a fixed value gives a plain address")
        self.assertEqual(links.href(CTX, fund_page, {"key": 7, "tab": "docs"}), "/funds/7?tab=docs",
                         "a declared query parameter is appended")
        self.assertEqual(links.href(CTX, funds_page, {}), "/funds", "a page without arguments needs none")
        joined = str(links.href(CTX, fund_page, {"key": LinksDesk.fund}))
        self.assertIn('"/funds/"+', joined, "a state value is joined into the address")
        self.assertIn("fund_rx_state_", joined, "the state value is in the address")
        self.assertIn("encodeURIComponent(String(", joined, "and the browser encodes it, so it stays one piece")
        self.assertIn("?? ''", str(links.href(CTX, fund_page, {"key": LinksDesk.count})),
                      "a 0 from the state stays a 0 in the address (only a missing value is empty)")
        self.assertEqual(links.href(CTX, fund_page, {"key": "a/b", "tab": "x&y=1"}), "/funds/a%2Fb?tab=x%26y%3D1",
                         "a fixed value is encoded: it cannot change the page or add a parameter")
        for bad in (None, True, "", ".."):
            with self.subTest(value=bad), self.assertRaises(PageError) as refused:
                links.href(CTX, fund_page, {"key": bad})
            self.assertEqual(str(refused.exception),
                             f"Page `home`: the value for `key` in a link is {bad!r}.\n"
                             "Pass text or a number: `key=row.id`, `key=7`.", "a value that cannot be an address piece is refused")
        with self.assertRaises(PageError, msg="a list from the state cannot be an address piece"):
            links.href(CTX, fund_page, {"key": LinksDesk.tags})
        for route in ("/docs/[[...slug]]", "/docs/[...slug]", "/posts/[[v]]"):
            with self.subTest(route=route), self.assertRaises(ValueError, msg="only plain route arguments"):
                links.info_of(route, "docs")
        with self.assertRaises(ValueError) as reserved:
            links.info_of("/funds/[to]", "fund", ["label"])
        self.assertIn("cannot be named to, label", str(reserved.exception), "a parameter named like a link's own word is refused")
        self.assertIn("count_rx_state_", str(links.href(CTX, fund_page, {"key": LinksDesk.count})),
                      "a number from the state is accepted")
        with self.assertRaises(PageError) as wrong:
            links.href(CTX, fund_page, {"tab": "x", "page": 2})
        self.assertEqual(str(wrong.exception),
                         "Page `home`: a link to page `fund_page` (/funds/[key]) is missing key and does not accept "
                         "page.\nPage `fund_page` takes: key, tab. Write `to=fund_page, key=...`.",
                         "the error names both pages, what the target takes and what to write")
        with self.assertRaises(PageError) as not_a_page:
            links.href(CTX, lambda: None, {})
        self.assertIn("Pass a function declared with `@ui.page`", str(not_a_page.exception), "what to pass instead")
        self.assertEqual(links.row_href(CTX, fund_page, "id"), "/funds/{id}", "a row link fills the page's one argument")
        with self.assertRaises(PageError, msg="a page without exactly one argument cannot be opened from a row"):
            links.row_href(CTX, funds_page, "id")

    # -- 1.792 ---------------------------------------------------------
    def test_1_792_a_link_leads_to_a_page_a_record_or_an_outside_site(self) -> None:
        """
        Scenario 1.792: ui.link.
        Given: links with to=, record= and href=.
        When:  they are declared and built, in text and among a page's actions.
        Then:  to= stays in the app; record= opens lex-app's own page of that record; href=
               takes only a full outside address and opens it as external; exactly one of the
               three is required; among actions the same link is drawn as a button.
        """
        inside = str(vocab.link("Open", to=fund_page, key="7").build(CTX))
        self.assertIn('"/funds/7"', inside, "to= builds the page's address")
        self.assertIn('rfdsI18n.t("Open", "Open"', inside, "the label is a sentence")
        record = vocab.link("Open in lex-app", record=(Group, 5))
        where, outside = record.target(CTX)
        self.assertTrue(str(where).endswith("/group/5/show"), f"record= leads to lex-app's record page: {where}")
        self.assertTrue(outside, "which opens outside the dashboards")
        self.assertIn("_blank", str(record.build(CTX)), "an outside link opens a new tab")
        self.assertNotIn("_blank", inside, "a link to a page stays in the app")
        for bad_record in (Group, (Group,), (Group, None), ("group", 5)):
            with self.subTest(record=str(bad_record)[:20]), self.assertRaises(ValueError, msg="record= takes (Model, pk)"):
                vocab.link("X", record=bad_record)
        with self.assertRaises(ValueError, msg="parameters belong to to="):
            vocab.link("Docs", href="https://example.org", key="7")
        with self.assertRaises(ValueError) as mail:
            vocab.link("Mail", href="mailto:a@example.org")
        self.assertIn("Mail and phone links are not supported yet", str(mail.exception), "the refusal says so")
        docs = vocab.link("Docs", href="https://example.org/docs")
        self.assertEqual(docs.target(CTX), ("https://example.org/docs", True), "href= is an outside site")
        for bad in ({}, {"to": fund_page, "href": "https://example.org"}):
            with self.subTest(given=sorted(bad)), self.assertRaises(ValueError, msg="exactly one target"):
                vocab.link("X", **bad)
        with self.assertRaises(ValueError, msg="href= is not for in-app paths"):
            vocab.link("X", href="/funds")
        as_action = str(vocab.link("All funds", to=funds_page).build(CTX.at("actions", "actions")))
        self.assertIn("rfds-button", as_action, "among actions a link is drawn as a button")
        self.assertNotIn("rfds-button", inside, "and in text as a link")

    # -- 1.793 ---------------------------------------------------------
    def test_1_793_a_button_runs_a_handler_or_opens_a_page(self) -> None:
        """
        Scenario 1.793: ui.button.
        Given: buttons with a handler, with to=, with both and with neither; a danger tone;
               and an action drawn as an entry of the page header's menu.
        When:  they are declared and built.
        Then:  a handler button calls the handler on click; a to= button goes to the page's
               address; both or neither is refused; tone="danger" gives RFDS's danger intent;
               the first action of a page is the primary one; a menu entry carries the
               translated sentence and the same click.
        """
        run = vocab.button("Close fund", LinksDesk.close)
        built = run.build(CTX)
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', str(built), "the label is a sentence")
        self.assertNotIn('"data-rfds-intent":"primary"', str(built), "a button is not primary unless the kind says so")
        self.assertIs(run.click(CTX), LinksDesk.close, "a handler button calls the handler")
        go = vocab.button("New fund", to=fund_page, key="new")
        self.assertIn("/funds/new", str(go.click(CTX)), "a to= button goes to the page")
        for bad in ((LinksDesk.close, {"to": funds_page}), (None, {})):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError, msg="a handler or to=, one of them"):
                vocab.button("X", bad[0], **bad[1])
        danger = vocab.button("Delete", LinksDesk.close, tone="danger")
        self.assertIn('"data-rfds-intent":"danger"', str(danger.build(CTX)), "danger intent")
        first = vocab.button("Save", LinksDesk.close)
        first.primary = True
        self.assertIn('"data-rfds-intent":"primary"', str(first.build(CTX)), "the kind marks the first action primary")
        with self.assertRaises(ValueError, msg="parameters belong to to="):
            vocab.button("X", LinksDesk.close, key="7")
        with self.assertRaises(PageError, msg="a to= button checks the page's parameters like a link"):
            vocab.button("New", to=fund_page).click(CTX)
        from rfds.components.actions._common import get_meta

        with self.assertRaises(PageError) as no_words:
            vocab.button(3, LinksDesk.close).menu_entry(CTX.at("actions", "actions"))
        self.assertEqual(str(no_words.exception),
                         "Page `home`, slot `actions`: an action's label is `3`.\n"
                         "Write the words: `ui.button(\"Close fund\", ...)`.", "a menu entry without words names its page")
        entry = run.menu_entry(CTX)
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', str(entry), "a menu entry is translated too")
        self.assertEqual(get_meta(entry).get("kind"), "menu_item", "RFDS recognises it as a menu entry")
        hidden = vocab.button("Close fund", LinksDesk.close, visible=LinksDesk.fund != "").menu_entry(CTX)
        self.assertEqual(get_meta(hidden).get("kind"), "menu_item", "and still does when visible= wraps it")
        self.assertIn("fund_rx_state_", str(hidden), "the wrapper carries the condition")
        self.assertNotEqual(str(danger.menu_entry(CTX)).replace("Delete", "Close fund"), str(entry),
                            "a dangerous action keeps its tone in the menu")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cp_ui_page_links.py`
Expected: `ImportError: cannot import name 'links' from 'lex.lex_app.reflex.ui.pages'`.

- [ ] **Step 3: Implement**

`lex/lex_app/reflex/ui/pages/links.py`:

```python
"""Where things lead: a link names a page function or a record, never a route string."""

from __future__ import annotations

import dataclasses
import re
import typing
from typing import Any, Callable
from urllib.parse import quote

from lex.lex_app.reflex.ui.pages.parts import Build

_ARGUMENT = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


@dataclasses.dataclass(frozen=True)
class PageInfo:
    """What ``@ui.page`` knows about a page: enough to link to it."""

    name: str
    route: str
    arguments: tuple[str, ...]          # the names in the route's brackets
    params: tuple[str, ...] = ()        # further query parameters the page accepts


#: Words ``ui.link`` and ``ui.button`` take themselves: a page parameter of that name could not be passed.
RESERVED = ("to", "record", "href", "visible", "label", "handler", "tone", "icon")


def info_of(route: str, name: str, params: Any = ()) -> PageInfo:
    """What a link needs to know about the page at ``route``; refuses what a link could not address."""
    if "[[" in route or "[..." in route:
        raise ValueError(f"ui.page({route!r}): only plain route arguments are supported, such as "
                         "\"/things/[key]\"; optional and catch-all arguments are not.")
    arguments, params = tuple(_ARGUMENT.findall(route)), tuple(params)
    taken = [word for word in (*arguments, *params) if word in RESERVED]
    if taken:
        raise ValueError(f"ui.page({route!r}): a page parameter cannot be named {', '.join(taken)}; "
                         "`ui.link` and `ui.button` use that word themselves. Rename it.")
    return PageInfo(name, route, arguments, params)


def page_info(target: Any) -> PageInfo | None:
    return getattr(target, "__ui_page__", None)


def href(ctx: Build, target: Callable[..., Any], values: dict) -> Any:
    """The address of the page ``target`` with ``values``; a str, or a Var when a value is one."""
    info = page_info(target)
    if info is None:
        raise ctx.fail(f"`to=` got {getattr(target, '__name__', target)!r}, which is not a page.",
                       "Pass a function declared with `@ui.page`. For an outside site use `href=\"https://...\"`.")
    missing = [name for name in info.arguments if name not in values]
    unknown = [name for name in values if name not in info.arguments and name not in info.params]
    if missing or unknown:
        accepted = ", ".join([*info.arguments, *info.params]) or "none"
        problem = " and ".join(part for part in (
            f"is missing {', '.join(missing)}" if missing else "",
            f"does not accept {', '.join(unknown)}" if unknown else "") if part)
        example = "".join(f", {name}=..." for name in info.arguments)
        raise ctx.fail(f"a link to page `{info.name}` ({info.route}) {problem}.",
                       f"Page `{info.name}` takes: {accepted}. Write `to={info.name}{example}`.")
    path: Any = ""
    rest = info.route
    for name in info.arguments:
        before, _, rest = rest.partition(f"[{name}]")
        path = path + before + piece(ctx, name, values[name])
    path = path + rest
    query = [(name, values[name]) for name in info.params if name in values]
    for index, (name, value) in enumerate(query):
        path = path + ("?" if index == 0 else "&") + name + "=" + piece(ctx, name, value)
    return path


def row_href(ctx: Build, target: Callable[..., Any], row_key: str) -> str:
    """The row-link template of a table whose rows open ``target``: its one argument is the row's key."""
    info = page_info(target)
    if info is None or len(info.arguments) != 1:
        raise ctx.fail(f"`open=` needs a page with exactly one route argument, got {getattr(target, '__name__', target)!r}.",
                       "Declare the page as `@ui.page(\"/things/[key]\")`.")
    return info.route.replace(f"[{info.arguments[0]}]", "{" + row_key + "}")


def piece(ctx: Build, name: str, value: Any) -> Any:
    """``value`` as one piece of an address, encoded so that it stays one piece.

    A value from data must not be able to change the page or add a parameter: a fixed value is
    quoted here, a state value by the browser.
    """
    import reflex as rx

    instead = f"Pass text or a number: `{name}=row.id`, `{name}=7`."
    if isinstance(value, rx.Var):
        kind = typing.get_origin(value._var_type) or value._var_type
        if kind in (bool, list, dict, tuple, set):
            raise ctx.fail(f"the value for `{name}` in a link is a {getattr(kind, '__name__', kind)} from the state.", instead)
        # String(x ?? '') keeps a 0 as "0"; Reflex's own to-text turns it into nothing.
        return rx.Var(f"encodeURIComponent(String({value!s} ?? ''))", _var_data=value._get_all_var_data()).to(str)
    if value is None or isinstance(value, bool) or str(value) in ("", ".", ".."):
        raise ctx.fail(f"the value for `{name}` in a link is {value!r}.", instead)
    return quote(str(value), safe="")
```

In `vocab.py`, add `from lex.lex_app.reflex.ui.pages import links` above the import from `parts`, then append:

```python
@dataclasses.dataclass(eq=False)
class Link(Part):
    label: Any
    to: Any = None
    values: dict = dataclasses.field(default_factory=dict)
    record: Any = None
    href: str | None = None
    visible: Any = None

    def target(self, ctx: Build) -> tuple[Any, bool]:
        """Where the link leads, and whether that is an outside site."""
        if self.to is not None:
            return links.href(ctx, self.to, self.values), False
        if self.record is not None:
            from lex.lex_app.reflex.embed import lex_app_base_url

            model, pk = self.record
            return (lex_app_base_url().rstrip("/") + "/" + model._meta.model_name + "/"
                    + links.piece(ctx, "pk", pk) + "/show"), True
        return self.href, True

    def click(self, ctx: Build) -> Any:
        where, outside = self.target(ctx)
        return rx.redirect(where, is_external=outside)

    def menu_entry(self, ctx: Build) -> rx.Component:
        return menu_entry(ctx, self.label, self.click(ctx), self.visible)

    def _wrap(self, ctx: Build, component: rx.Component) -> rx.Component:
        return _marked(component, shown(ctx, component, self.visible))

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        where, outside = self.target(ctx)
        if ctx.place == "actions":
            return rfds.button(say(self.label), intent="secondary", on_click=rx.redirect(where, is_external=outside))
        return rfds.link(say(self.label), href=where, external=outside)


def link(label: Any, *, to: Any = None, record: Any = None, href: str | None = None, visible: Any = None,
         **values: Any) -> Link:
    """Goes somewhere: ``to=`` a page (with its parameters by name), ``record=(Model, pk)``, or ``href=`` an outside site."""
    given = [name for name, value in (("to", to), ("record", record), ("href", href)) if value is not None]
    if len(given) != 1:
        raise ValueError("ui.link() takes exactly one of to=, record= or href=; got " + (", ".join(given) or "none")
                         + ". Write `ui.link(\"Open\", to=some_page, key=...)`.")
    if values and to is None:
        raise ValueError(f"ui.link(): {', '.join(values)} can only be given with to=, as the page's parameters.")
    if href is not None and not (isinstance(href, str) and href.startswith(("http://", "https://"))):
        raise ValueError(f"ui.link(href=) is for an outside site and takes a fixed address starting with https://; "
                         f"got {href!r}. For a page of the dashboards use to=. Mail and phone links are not supported yet.")
    if record is not None and not (isinstance(record, tuple) and len(record) == 2 and hasattr(record[0], "_meta")
                                   and record[1] is not None):
        raise ValueError("ui.link(record=) takes (Model, pk): `record=(Fund, row.id)`.")
    return Link(label, to, values, record, href, visible)


@dataclasses.dataclass(eq=False)
class Button(Part):
    label: Any
    handler: Any = None
    to: Any = None
    values: dict = dataclasses.field(default_factory=dict)
    tone: str = "neutral"
    icon: str | None = None
    visible: Any = None
    primary: bool = False               # set by the kind: the first action of a page

    def click(self, ctx: Build) -> Any:
        return rx.redirect(links.href(ctx, self.to, self.values)) if self.to is not None else self.handler

    def menu_entry(self, ctx: Build) -> rx.Component:
        return menu_entry(ctx, self.label, self.click(ctx), self.visible, tone=self.tone, icon=self.icon)

    def _wrap(self, ctx: Build, component: rx.Component) -> rx.Component:
        return _marked(component, shown(ctx, component, self.visible))

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        intent = "danger" if self.tone == "danger" else "primary" if self.primary else "secondary"
        return rfds.button(say(self.label), intent=intent, icon=self.icon, on_click=self.click(ctx))


def button(label: Any, handler: Any = None, *, to: Any = None, tone: str = "neutral", icon: str | None = None,
           visible: Any = None, **values: Any) -> Button:
    """Runs a handler, or opens a page (``to=``, with the page's parameters by name)."""
    if (handler is None) == (to is None):
        raise ValueError("ui.button() takes a handler or to=, one of them: `ui.button(\"Close fund\", State.close)` "
                         "or `ui.button(\"New fund\", to=new_fund)`.")
    if values and to is None:
        raise ValueError(f"ui.button(): {', '.join(values)} can only be given with to=, as the page's parameters.")
    return Button(label, handler, to, values, _tone(tone), icon, visible)


def menu_entry(ctx: Build, label: Any, click: Any, visible: Any, *, tone: str = "neutral",
               icon: str | None = None) -> rx.Component:
    """An action as an entry of the page header's "More actions" menu."""
    from rfds.components.actions.menu import menu_item

    try:
        words = say(label)
    except NotASentence:
        raise ctx.fail(f"an action's label is {called(label)}.",
                       "Write the words: `ui.button(\"Close fund\", ...)`.") from None
    entry = menu_item(words, on_select=click, icon=icon, tone="danger" if tone == "danger" else "neutral")
    return _marked(entry, shown(ctx, entry, visible))
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cp_ui_page_links.py`
Expected: 3 passed, 4 subtests passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/test_project/tests/init/test_1cp_ui_page_links.py
git commit -m "feat(reflex): lex.ui pages - links name a page function or a record; ui.link and ui.button"
```

End the message with the co-author line for your own model.

---

### Task 4: `ui.table` over a model, and `ui.column`

**Files:**
- Create: `lex/lex_app/reflex/ui/pages/tables.py`
- Test: `lex/test_project/tests/init/test_1cr_ui_page_tables.py`

**Interfaces:**
- Consumes: `data.lex_table(model, *, columns=, **table_props)` (lex-app's server-paged table; passes `row_href` and `empty` through to `rfds.data_table`), `data.lex_columns(model, fields, **overrides)`, `links.row_href`, `rfds.empty_state(kind=, title=, description=)`.
- Produces: `tables.table(source, *, columns=(), open=None, empty=None, visible=None)` → `Table` (a `Part`) with a settable `.empty`; `tables.column(key, *, label=None, kind=None)` → `Column`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cr_ui_page_tables.py`:

```python
"""lex.ui pages: a table over a model, paged on the server, whose rows open a page.

Intent: finding things is the commonest page. ui.table over a model is lex-app's server-paged
table through the API as the user -- the same component and look everywhere -- with the columns
named by field, a row that opens a page named by its function, and a sentence for "nothing here".
A regression is a table that reads all rows into the browser, a row link written as a string, or
an empty table with no words.

Cluster 1cr -- scenarios 1.797-1.798. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (table, column).
Run: python -m lex pytest lex/test_project/tests/init/test_1cr_ui_page_tables.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.test import SimpleTestCase

from lex.lex_app.reflex.ui.pages import links, tables
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


def group_page():
    """Stands for a page declared with @ui.page("/groups/[key]")."""


group_page.__ui_page__ = links.info_of("/groups/[key]", "group_page")

CTX = Build(page="groups", route="/groups")


class TestCluster01cr_PageTables(SimpleTestCase):
    """1.797-1.798: the model table; opening a page from a row, the empty sentence, columns."""

    # -- 1.797 ---------------------------------------------------------
    def test_1_797_a_table_over_a_model_is_the_server_paged_table(self) -> None:
        """
        Scenario 1.797: ui.table(Model).
        Given: a model and the names of the columns to show.
        When:  the table is built.
        Then:  it is RFDS's data table over lex-app's paged table state for that model, showing
               the named columns under the model's own field labels; something that is not a
               model is refused with what to pass.
        """
        built = str(tables.table(Group, columns=["name"]).build(CTX))
        self.assertIn("rfds-data-table", built, "RFDS's data table")
        self.assertIn("page_rows", built, "rows come a page at a time from the table's state")
        self.assertIn('"Name"', built, "the named column is shown under the field's own label")
        self.assertIn("auth.Group:read", built, "the table is shown only to users who may read the model")
        self.assertNotIn("auth.Group:read", str(tables.table(Group, columns=["name"], visible=True).build(CTX)),
                         "unless visible= says otherwise")
        with self.assertRaises(PageError) as wrong:
            tables.table([{"name": "a"}], columns=["name"]).build(CTX.at("table"))
        self.assertEqual(str(wrong.exception),
                         "Page `groups`, slot `table`: `ui.table` takes a model class and reads its rows from the "
                         "server; it cannot show rows you hold yourself (a list, or a state value) yet.\n"
                         "Pass a model: `ui.table(Fund, columns=[...])`.", "the refusal says what a table can show")
        with self.assertRaises(PageError) as unknown:
            tables.table(Group, columns=["nmae"]).build(CTX.at("table"))
        self.assertEqual(str(unknown.exception),
                         "Page `groups`, slot `table`: Group has no column nmae.\n"
                         "Its columns are: id, name. A many-to-many field cannot be a column.",
                         "a wrong column name lists the model's columns")

    # -- 1.798 ---------------------------------------------------------
    def test_1_798_rows_open_a_page_an_empty_table_says_so_and_a_column_can_be_relabelled(self) -> None:
        """
        Scenario 1.798: open=, empty= and ui.column.
        Given: a table with open= a page, empty= a sentence, and a column with its own label.
        When:  it is built.
        Then:  each row links to the page's address with the row's key in place of the page's
               argument; the empty sentence is translated and shown by RFDS's empty state; the
               column carries the given label; open= a page without exactly one argument is
               refused.
        """
        built = str(tables.table(Group, columns=[tables.column("name", label="Team", kind="text")], open=group_page,
                                 empty="No groups yet.").build(CTX))
        self.assertIn('"/groups/"', built, "rows link to the page")
        keyed = str(tables.table(Session, columns=["expire_date"], open=group_page, visible=True).build(CTX))
        self.assertIn("session_key", keyed,
                      "a model whose primary key is not `id` builds, and its rows are keyed and linked by that key")
        self.assertIn('rfdsI18n.t("No groups yet.", "No groups yet."', built, "the empty sentence is a message")
        self.assertIn('rfdsI18n.t("Team", "Team"', built,
                      "the column's own label is used, and translated like every other sentence")
        self.assertNotIn('"Name"', built, "in place of the field's label")
        for bad in (dict(kind="colour"), dict(label=""), dict(currency="EUR"), dict(kind="money", currency="euro")):
            with self.subTest(bad=bad), self.assertRaises(ValueError, msg="a column is checked when it is declared"):
                tables.column("name", **bad)
        self.assertEqual(tables._RFDS_KIND["datetime"], "instant", "datetime is RFDS's instant")

        def no_argument():
            """A page without a route argument."""

        no_argument.__ui_page__ = links.info_of("/groups", "no_argument")
        with self.assertRaises(PageError) as no_key:
            tables.table(Group, columns=["name"], open=no_argument).build(CTX)
        self.assertEqual(str(no_key.exception),
                         "Page `groups`: `open=` needs a page with exactly one route argument, got 'no_argument'.\n"
                         "Declare the page as `@ui.page(\"/things/[key]\")`.", "open= needs a page with one argument")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cr_ui_page_tables.py`
Expected: `ImportError: cannot import name 'tables' from 'lex.lex_app.reflex.ui.pages'`.

- [ ] **Step 3: Implement**

`lex/lex_app/reflex/ui/pages/tables.py`:

```python
"""ui.table and ui.column: rows, paged on the server."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, say


#: What a column's kind is called in RFDS's column spec.
_RFDS_KIND = {"text": "text", "number": "number", "money": "money", "percent": "percent", "datetime": "instant"}


@dataclasses.dataclass(frozen=True)
class Column:
    key: str
    label: str | None = None
    kind: str | None = None
    currency: str | None = None


def column(key: str, *, label: str | None = None, kind: str | None = None, currency: str | None = None) -> Column:
    """A table column that needs more than its name: its own label, or what its values are.

    ``kind`` is one of the words ``ui.fact`` takes; ``currency`` (a code such as ``"EUR"``) goes
    with ``kind="money"``.
    """
    if kind is not None and kind not in _RFDS_KIND:
        raise ValueError(f"ui.column({key!r}, kind=) must be one of {', '.join(_RFDS_KIND)}; got {kind!r}")
    if label is not None and not (isinstance(label, str) and label):
        raise ValueError(f"ui.column({key!r}, label=) takes a sentence; got {label!r}")
    if currency is not None and kind != "money":
        raise ValueError(f"ui.column({key!r}, currency=) goes with kind=\"money\"")
    if currency is not None and not (isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency)):
        raise ValueError(f"ui.column({key!r}, currency=) takes a three-letter code such as \"EUR\"; got {currency!r}")
    return Column(key, label, kind, currency)


@dataclasses.dataclass(eq=False)
class Table(Part):
    source: Any                         # a model class
    columns: tuple = ()
    open: Any = None
    empty: Any = None
    visible: Any = None

    def build(self, ctx: Build) -> rx.Component:
        # A table reads through the API as the user; without the grant it could only show an error.
        from lex.lex_app.reflex.ui import guard

        if self.visible is None and isinstance(self.source, type) and hasattr(self.source, "_meta"):
            return dataclasses.replace(self, visible=guard.read(self.source)).build(ctx)
        return super().build(ctx)

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        from lex.lex_app.reflex import data

        model = self.source
        if not (isinstance(model, type) and hasattr(model, "_meta")):
            raise ctx.fail("`ui.table` takes a model class and reads its rows from the server; it cannot show rows "
                           "you hold yourself (a list, or a state value) yet.",
                           "Pass a model: `ui.table(Fund, columns=[...])`.")
        known = {field.name for field in model._meta.concrete_fields}
        keys = [c.key if isinstance(c, Column) else str(c) for c in self.columns]
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise ctx.fail(f"{model.__name__} has no column {', '.join(unknown)}.",
                           f"Its columns are: {', '.join(sorted(known))}. A many-to-many field cannot be a column.")
        overrides: dict[str, dict] = {}
        for c in self.columns:
            if not isinstance(c, Column):
                continue
            options: dict[str, Any] = {}
            if c.label is not None:
                options.update(header=c.label, header_id=c.label)      # the label is the message, as everywhere
            if c.kind is not None:
                options["kind"] = _RFDS_KIND[c.kind]
            if c.currency is not None:
                options["ccy"] = c.currency
            if options:
                overrides[c.key] = options
        key = model._meta.pk.name
        props: dict[str, Any] = {"row_key": key, "row_fields": (key,)}     # the key stays in each row, shown or not
        if self.open is not None:
            props["row_href"] = links.row_href(ctx, self.open, key)
        if self.empty is not None:
            props["empty"] = rfds.empty_state(kind="no_data", title=say(self.empty),
                                              description=say("Nothing has been added here yet."))
        return data.lex_table(model, columns=data.lex_columns(model, keys or None, **overrides), **props)


def table(source: Any, *, columns: Any = (), open: Any = None, empty: Any = None, visible: Any = None) -> Table:
    """A model's rows, paged on the server and read as the user.

    ``columns`` names the fields to show (``ui.column`` for one that needs its own label or kind);
    ``open=`` a page makes each row a link to it; ``empty=`` is the sentence shown when the model
    has no rows at all. The table is shown only to users who may read the model, unless
    ``visible=`` says otherwise.
    """
    return Table(source, tuple(columns), open, empty, visible)
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cr_ui_page_tables.py`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/test_project/tests/init/test_1cr_ui_page_tables.py
git commit -m "feat(reflex): lex.ui pages - ui.table over a model, rows that open a page, ui.column"
```

End the message with the co-author line for your own model.

---

### Task 5: `ui.section` with its own loading and failure; `ui.each`, `ui.switch`, `ui.custom_block`

**Files:**
- Create: `lex/lex_app/reflex/ui/pages/kinds.py`
- Modify: `lex/lex_app/reflex/ui/pages/vocab.py` (append)
- Test: `lex/test_project/tests/init/test_1cq_ui_page_sections.py`

**Interfaces:**
- Consumes: `runs.loaded(handler)`, `runs.failed(handler)` (Task 1); `tables.Table`; `parts.take`; `rfds.stack`, `rfds.skeleton(loading=True, variant=)`, `rfds.inline_message`.
- Produces: `vocab.custom_block(component, *, reason, visible=None)`, `vocab.each(items, render, *, visible=None)`, `vocab.switch(value, cases, *, visible=None)`, `vocab.CONTENT` (the part classes a section accepts, besides `tables.Table`), `vocab.build_all(ctx, parts)`; `kinds.states(ctx, load, content, shape)`; `kinds.section(title, *content, load=None, empty=None, visible=None)` → `Section` with `.title`, `.load`, `.build(ctx)`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cq_ui_page_sections.py`:

```python
"""lex.ui pages: a section shows its own loading and failure; repetition and choice without raw Reflex.

Intent: the author never writes a loading, failed or empty branch. A section with its own load
shows a skeleton until that load has finished and its failure in place; without a load it is
just its content. What a page does not already handle is covered by ui.each and ui.switch, and
anything outside the vocabulary goes through ui.custom_block, which must say why. A regression
is content shown before its data, a failed section that leaves a blank, or raw components
entering a page unmarked.

Cluster 1cq -- scenarios 1.794-1.796. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (section, states), ui/pages/vocab.py (each, switch,
custom_block).
Run: python -m lex pytest lex/test_project/tests/init/test_1cq_ui_page_sections.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import kinds, parts, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class SectionDesk(ui.State):
    documents: list[str] = []
    mode: str = "a"

    @ui.load
    def load_documents(self):
        self.documents = ["x"]


CTX = Build(page="fund", route="/funds/[key]")


class TestCluster01cq_PageSections(SimpleTestCase):
    """1.794-1.796: a section's own states; each and switch; the marked way out."""

    # -- 1.794 ---------------------------------------------------------
    def test_1_794_a_section_with_a_load_shows_its_skeleton_then_its_content_or_its_failure(self) -> None:
        """
        Scenario 1.794: ui.section.
        Given: a section without a load, one with a load on a page, and the same on a dashboard.
        When:  they are built.
        Then:  without a load the section is its content; with one, the content is shown only
               once ui.loaded says so, before that the failure message when there is one, else
               a skeleton (a table's, when the section is one table); on a page nothing is
               mounted (the opener starts the load); on a dashboard the load starts when the
               section mounts; a section's title is a sentence and comes first. Facts that
               follow one another form one key-value list; a section inside a section, and
               empty= without a table, are refused.
        """
        plain = str(kinds.section("Notes", vocab.text("Hello")).build(CTX))
        self.assertIn('rfdsI18n.t("Hello", "Hello"', plain, "the content is built")
        self.assertNotIn("ui_loaded", plain, "no load: no loading state")

        loading = kinds.section("Documents", vocab.text("Hello"), load=SectionDesk.load_documents)
        on_page = loading.build(CTX)
        text = str(on_page)
        loaded = text.index('ui_loaded_rx_state_?.["SectionDesk.load_documents"]')
        self.assertLess(loaded, text.index('rfdsI18n.t("Hello"'), "the content sits behind the loaded check")
        self.assertIn('ui_failed_rx_state_?.["SectionDesk.load_documents"]', text, "the failure is shown in place")
        self.assertIn("rfds-skeleton", text, "a skeleton until then")
        self.assertNotIn("on_mount", on_page.event_triggers, "on a page the opener starts the load")
        on_dashboard = loading.build(Build(page="main", mounts=True))
        self.assertIn("on_mount", on_dashboard.event_triggers, "on a dashboard the load starts when the section mounts")
        with self.assertRaises(TypeError, msg="a section's title comes first and is a sentence"):
            kinds.section(vocab.text("Hello"))
        facts = str(kinds.section("Key facts", vocab.fact("A", 1), vocab.fact("B", 2), vocab.text("Hello")).build(CTX))
        self.assertEqual(facts.count('"data-slot":"rfds-key-value"'), 1,
                         "facts that follow one another are one key-value list, which is what RFDS's items need")
        only_table = kinds.section("Groups", tables.table(Group, columns=["name"]), load=SectionDesk.load_documents)
        self.assertIn("rfds-skeleton-row", str(only_table.build(CTX)), "a section that is one table waits as a table")
        self.assertNotIn("rfds-skeleton-row", text, "any other section waits as a block")
        with self.assertRaises(PageError) as nested:
            kinds.section("Outer", kinds.section("Inner", vocab.text("x"))).build(CTX.at("sections[0]"))
        self.assertEqual(str(nested.exception),
                         "Page `fund`, slot `sections[0]`: a section inside a section.\n"
                         "Sections do not nest: give the page each of them, in `sections=[...]`.",
                         "a nested section is refused in its own words")
        with self.assertRaises(PageError) as no_table:
            kinds.section("Notes", vocab.text("x"), empty="Nothing yet.").build(CTX.at("sections[0]"))
        self.assertEqual(str(no_table.exception),
                         "Page `fund`, slot `sections[0]`: `empty=` is the sentence for a table without rows, and this "
                         "section has no table.\nRemove `empty=`, or put a `ui.table(...)` in the section.",
                         "empty= without a table is refused")

    # -- 1.795 ---------------------------------------------------------
    def test_1_795_each_repeats_and_switch_chooses(self) -> None:
        """
        Scenario 1.795: ui.each and ui.switch.
        Given: a list in the state and a mode in the state.
        When:  ui.each renders a part per item and ui.switch picks a part by the mode.
        Then:  each item is rendered through the part the function returns; a function that
               returns a raw component is refused with what to return; switch shows the case
               matching the value and nothing otherwise; a case that is not a lex component is
               refused.
        """
        repeated = str(vocab.each(SectionDesk.documents, lambda name: vocab.text(name)).build(CTX))
        self.assertIn("documents_rx_state_", repeated, "the list is iterated")
        self.assertIn("RadixThemesText", repeated, "each item is the part the function returned")
        with self.assertRaises(PageError) as raw:
            vocab.each(SectionDesk.documents, lambda name: rx.text(name)).build(CTX)
        self.assertIn("did not return a lex component", str(raw.exception), "the refusal says what was wrong")
        with self.assertRaises(PageError) as sectioned:
            vocab.each(SectionDesk.documents, lambda name: kinds.section("One", vocab.text(name))).build(CTX)
        self.assertEqual(str(sectioned.exception),
                         "Page `fund`: `ui.each` produced `ui.section`, and sections do not nest.\n"
                         "Give the page each section itself, in `sections=[...]`.", "each cannot produce sections")
        with self.assertRaises(TypeError, msg="each takes a function"):
            vocab.each(SectionDesk.documents, vocab.text("x"))
        with self.assertRaises(ValueError, msg="switch needs a case"):
            vocab.switch(SectionDesk.mode, {})
        chosen = str(vocab.switch(SectionDesk.mode, {"a": vocab.text("First"), "b": vocab.text("Second")}).build(CTX))
        self.assertIn("mode_rx_state_", chosen, "the value decides")
        self.assertIn('rfdsI18n.t("First"', chosen, "the first case is built")
        self.assertIn('rfdsI18n.t("Second"', chosen, "and the second")
        with self.assertRaises(PageError, msg="a case must be a lex component"):
            vocab.switch(SectionDesk.mode, {"a": rx.text("raw")}).build(CTX)

    # -- 1.796 ---------------------------------------------------------
    def test_1_796_anything_else_goes_through_the_marked_way_out(self) -> None:
        """
        Scenario 1.796: ui.custom_block and the section's slot check.
        Given: a raw component.
        When:  it is put into a section directly, and through ui.custom_block with and without
               a reason.
        Then:  directly it is refused, with the page, the slot and what to write; through
               ui.custom_block with a reason it is accepted and built as it is, and an error
               names it by its public name; without a reason, or with something that is not a
               component, custom_block is refused. A section hands its empty= sentence to a
               copy of its table, never to the author's part.
        """
        with self.assertRaises(PageError) as direct:
            kinds.section("Notes", rx.text("raw")).build(CTX.at("sections[0]"))
        self.assertEqual(str(direct.exception).splitlines()[0],
                         "Page `fund`, slot `sections[0]`: got a raw component (`Text`).",
                         "a raw component in a section is named")
        self.assertIn("ui.custom_block", str(direct.exception), "and the way out is named")
        marked = kinds.section("Notes", vocab.custom_block(rx.text("raw"), reason="A chart type the vocabulary lacks."))
        self.assertIn("raw", str(marked.build(CTX)), "a marked block is built as it is")
        block = vocab.custom_block(rx.text("raw"), reason="A chart type the vocabulary lacks.")
        self.assertEqual(parts.called(block), "`ui.custom_block`", "an error names it by its public name")
        shared = tables.table(Group, columns=["name"])
        kinds.section("First", shared, empty="No groups yet.").build(CTX)
        self.assertIsNone(shared.empty, "a section hands its empty sentence to a copy of the table, not to the part")
        with self.assertRaises(ValueError, msg="a custom block must say why"):
            vocab.custom_block(rx.text("raw"), reason=" ")
        with self.assertRaises(TypeError, msg="a custom block holds a component"):
            vocab.custom_block("not a component", reason="x")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cq_ui_page_sections.py`
Expected: `ImportError: cannot import name 'kinds' from 'lex.lex_app.reflex.ui.pages'`.

- [ ] **Step 3: Implement**

In `vocab.py`, change the typing import to `from typing import Any, Callable`, then append:

```python
@dataclasses.dataclass(eq=False)
class CustomBlock(Part):
    ui_name = "custom_block"

    component: Any
    reason: str
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        return self.component


def custom_block(component: Any, *, reason: str, visible: Any = None) -> CustomBlock:
    """The marked way out: a raw component, with one sentence saying why the vocabulary did not do."""
    if not isinstance(component, rx.Component):
        raise TypeError(f"ui.custom_block() takes a component, got {type(component).__name__}")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ui.custom_block() needs reason=: one sentence saying why the vocabulary did not do")
    return CustomBlock(component, reason.strip(), visible)


@dataclasses.dataclass(eq=False)
class Each(Part):
    items: Any
    render: Callable[[Any], Part]
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        def one(item: Any) -> rx.Component:
            part = self.render(item)
            if not isinstance(part, Part):
                raise ctx.fail("the function given to `ui.each` did not return a lex component.",
                               "Return one of the `ui.*` components, or `ui.custom_block(...)`.")
            _fits(ctx, part, "ui.each")
            return part.build(ctx)

        return rx.foreach(self.items, one)


def each(items: Any, render: Callable[[Any], Part], *, visible: Any = None) -> Each:
    """Repeats ``render`` for every item: a function of one item that returns a ``ui.*`` component."""
    if not callable(render):
        raise TypeError("ui.each(items, render) takes a function that returns a `ui.*` component for one item; "
                        f"got {type(render).__name__}")
    return Each(items, render, visible)


@dataclasses.dataclass(eq=False)
class Switch(Part):
    value: Any
    cases: dict
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        for part in self.cases.values():
            if not isinstance(part, Part):
                raise ctx.fail("a case of `ui.switch` is not a lex component.",
                               "Give each case one of the `ui.*` components, or `ui.custom_block(...)`.")
            _fits(ctx, part, "ui.switch")
        return rx.match(self.value, *[(key, part.build(ctx)) for key, part in self.cases.items()], rx.fragment())


def switch(value: Any, cases: dict, *, visible: Any = None) -> Switch:
    """Shows the case whose key equals ``value``; nothing when none does. The keys are fixed values."""
    if not cases:
        raise ValueError("ui.switch(value, cases) needs at least one case: `{\"open\": ui.text(...)}`")
    return Switch(value, dict(cases), visible)


def _fits(ctx: Build, part: Part, where: str) -> None:
    """Refuse a part that cannot sit inside a section, when `ui.each` or `ui.switch` produced it."""
    if not type(part).nests:
        raise ctx.fail(f"`{where}` produced {called(part)}, and sections do not nest.",
                       "Give the page each section itself, in `sections=[...]`.")


CONTENT = (Text, Fact, Status, Message, Link, Button, CustomBlock, Each, Switch)


def build_all(ctx: Build, parts: list) -> list:
    return [part.build(ctx) for part in parts]
```

`lex/lex_app/reflex/ui/pages/kinds.py`:

```python
"""The page kinds: each takes named slots and decides where they sit."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable, ClassVar

import reflex as rx

from lex.lex_app.reflex.ui import runs
from lex.lex_app.reflex.ui.pages import tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, NotASentence, Part, called, say, slug, take


def states(ctx: Build, load: Any, content: rx.Component, shape: str) -> rx.Component:
    """``content`` once ``load`` has finished; before that its skeleton, or its failure in place."""
    import rfds

    # The page waits as a whole: keeping its header while the body loads would show an empty
    # title and actions that can be clicked before the data they act on has arrived.
    if load is None:
        return content
    problem = runs.failed(load)
    waiting = rx.cond(problem != "", rfds.inline_message(problem, tone="danger"),
                      rfds.skeleton(loading=True, variant=shape))
    whole = rx.cond(runs.loaded(load), content, waiting)
    return rx.box(whole, on_mount=load, width="100%") if ctx.mounts else whole


def _stacked(ctx: Build, parts: list) -> list:
    """The parts built in order; facts that follow one another become one key-value list."""
    import rfds

    built: list = []
    run: list = []

    def close() -> None:
        if run:
            built.append(rfds.key_value(*vocab.build_all(ctx, run), columns=min(3, len(run))))
            run.clear()

    for part in parts:
        if isinstance(part, vocab.Fact):
            run.append(part)
            continue
        close()
        built.append(part.build(ctx))
    close()
    return built


@dataclasses.dataclass(eq=False)
class Section(Part):
    nests: ClassVar[bool] = False

    title: str
    content: tuple = ()
    load: Any = None
    empty: Any = None
    visible: Any = None

    def body(self, ctx: Build) -> rx.Component:
        import rfds

        if any(isinstance(part, Section) for part in self.content):
            raise ctx.fail("a section inside a section.",
                           "Sections do not nest: give the page each of them, in `sections=[...]`.")
        parts = take(ctx, list(self.content), (*vocab.CONTENT, tables.Table),
                     "A section holds `ui.text`, `ui.table`, `ui.message`, `ui.link` and the other `ui.*` "
                     "components, or `ui.custom_block(..., reason=\"...\")`.")
        has_table = any(isinstance(part, tables.Table) for part in parts)
        if self.empty is not None:
            if not has_table:
                raise ctx.fail("`empty=` is the sentence for a table without rows, and this section has no table.",
                               "Remove `empty=`, or put a `ui.table(...)` in the section.")
            # A table without its own sentence takes the section's: a copy, never the author's part.
            parts = [dataclasses.replace(part, empty=self.empty)
                     if isinstance(part, tables.Table) and part.empty is None else part for part in parts]
        shape = "table" if has_table and len(parts) == 1 else "rect"
        return states(ctx, self.load, rfds.stack(*_stacked(ctx, parts)), shape)

    def _render(self, ctx: Build) -> rx.Component:
        return self.body(ctx)


def section(title: str, *content: Any, load: Any = None, empty: Any = None, visible: Any = None) -> Section:
    """A titled part of the page. With ``load=`` it loads on its own and shows its own skeleton and failure."""
    if not isinstance(title, str) or not title:
        raise TypeError(f"ui.section() takes its title first, a sentence; got {title!r}")
    return Section(title, tuple(content), load, empty, visible)


#: An action RFDS treats as a Save: it wants a "last saved" time beside it, which a page header cannot give.
_SAVE = re.compile(r"\b(save|speichern|sichern)\b", re.IGNORECASE)
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cq_ui_page_sections.py`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/test_project/tests/init/test_1cq_ui_page_sections.py
git commit -m "feat(reflex): lex.ui pages - sections that show their own loading and failure; each, switch, custom_block"
```

End the message with the co-author line for your own model.

---

### Task 6: The record and list kinds

**Files:**
- Modify: `lex/lex_app/reflex/ui/pages/kinds.py` (append)
- Test: `lex/test_project/tests/init/test_1cs_ui_page_kinds.py`

**Interfaces:**
- Consumes: everything above; `rfds.templates.object_page(title=, path=, header=, sections=, navigation=, actions=)`, `rfds.templates.ObjectSection(id=, title_id=, title_default=, content=)`, `rfds.templates.list_report(title=, path=, table=, actions=)`, `rfds.key_value(*items, columns=)`.
- Produces: `kinds.Kind` with `.title`, `.load`, `.actions`, `.loads() -> (page loads, part loads)` and `.build(ctx)`; `kinds.record(*, title, load=None, status=None, facts=(), sections=(), tabs=False, actions=())`; `kinds.list_(*, title, load=None, table=None, actions=())`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cs_ui_page_kinds.py`:

```python
"""lex.ui pages: the record and list kinds place their slots; the author only fills them.

Intent: a page kind fixes where everything sits, on RFDS's own floorplan for that shape -- the
object page for one record, the list report for finding things -- so pages of one kind look
alike and nobody writes layout. The kind also shows a skeleton until the page's load has
finished, places the actions as RFDS's page header does (one primary button; one more as a
button, more in a "More actions" menu), and refuses what does not belong in a slot. A regression
is a page that renders without its data, a slot that accepts anything, or actions in the wrong
place.

Cluster 1cs -- scenarios 1.799-1.802. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (record, list_, Kind).
Run: python -m lex pytest lex/test_project/tests/init/test_1cs_ui_page_kinds.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import kinds, links, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class KindDesk(ui.State):
    name: str = ""
    amount: float = 0.0

    @ui.load
    def open(self):
        self.name = "Fund One"

    @ui.load
    def load_documents(self):
        self.name = self.name

    @ui.action
    def close(self):
        return ui.toast("Closed")


def group_page():
    """Stands for a page declared with @ui.page("/groups/[key]")."""


group_page.__ui_page__ = links.info_of("/groups/[key]", "group_page")

CTX = Build(page="fund", route="/funds/[key]")


def _record(**slots):
    slots.setdefault("title", KindDesk.name)
    slots.setdefault("sections", [kinds.section("Details", vocab.text("Hello"))])
    return kinds.record(**slots)


class TestCluster01cs_PageKinds(SimpleTestCase):
    """1.799-1.802: the record page; its errors; where actions go; the list page."""

    # -- 1.799 ---------------------------------------------------------
    def test_1_799_a_record_page_places_status_facts_sections_and_waits_for_its_load(self) -> None:
        """
        Scenario 1.799: ui.record.
        Given: a record with a title from the state, a load, a status, two facts and two
               sections, one of which loads on its own.
        When:  it is built for a page.
        Then:  it is RFDS's object page: the title in the page header, the status and the facts
               in the header area, each section under its translated heading; the whole page
               sits behind the load's loaded check, with a skeleton and the failure in place
               before it; the kind reports its page load and its sections' loads separately;
               tabs=True with two sections uses RFDS's tab navigation.
        """
        page = _record(load=KindDesk.open, status=vocab.status("Open", tone="info"),
                       facts=[vocab.fact("Net asset value", KindDesk.amount, kind="money"),
                              vocab.fact("Name", KindDesk.name)],
                       sections=[kinds.section("Holdings", vocab.text("Hello")),
                                 kinds.section("Documents", vocab.text("Later"), load=KindDesk.load_documents)])
        text = str(page.build(CTX))
        for needle, why in (("rfds-page-header", "RFDS's page header"), ("rfds-key-value", "the facts"),
                            ('rfdsI18n.t("Net asset value"', "a fact's label"), ("rfds-lozenge", "the status"),
                            ('rfdsI18n.t("Holdings", "Holdings"', "a section heading"),
                            ('rfdsI18n.t("Documents", "Documents"', "the second section heading"),
                            ("name_rx_state_", "the title from the state")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertLess(text.index('ui_loaded_rx_state_?.["KindDesk.open"]'), text.index("rfds-page-header"),
                        "the page sits behind its load's loaded check")
        self.assertIn("rfds-skeleton", text, "a skeleton before the load has finished")
        self.assertEqual(page.loads(), ([KindDesk.open], [KindDesk.load_documents]),
                         "the page load and the sections' loads are reported separately")
        bare = str(_record().build(CTX))
        self.assertNotIn("ui_loaded", bare, "without a load the page is shown at once")
        self.assertIn("name_rx_state_", bare, "the title comes from the state (this page has no other use of it)")
        odd = str(_record(sections=[kinds.section("2024 results", vocab.text("a")),
                                    kinds.section("Übersicht", vocab.text("b")),
                                    kinds.section("概要", vocab.text("c"))]).build(CTX))
        for anchor in ('"#section-2024-results"', '"#ubersicht"', '"#section-3"'):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, odd, "every title gives a section an id that is a valid anchor")
        gated = _record(sections=[kinds.section("Details", vocab.text("a"), visible=ui.edit(Group))])._sections(CTX)
        self.assertEqual([section.permission for section in gated], ["auth.Group:edit"],
                         "a section's permission is handed to RFDS, which keeps the heading and says why it is empty")
        tabbed = str(_record(tabs=True, sections=[kinds.section("A", vocab.text("a")),
                                                   kinds.section("B", vocab.text("b"))]).build(CTX))
        self.assertIn("rfds-tabs", tabbed, "tabs=True shows one section at a time")

    # -- 1.800 ---------------------------------------------------------
    def test_1_800_a_record_page_refuses_what_does_not_belong(self) -> None:
        """
        Scenario 1.800: the record page's checks.
        Given: record pages with a raw component among the facts, no section, no title, a
               status that is not a ui.status, and a section slot holding a text.
        When:  each is built.
        Then:  each is a PageError naming the page and the slot and saying what to write --
               also two sections with one title, a section hidden by a state value, a blank
               title, a title that is not words, and an action called Save; a limit of RFDS's
               own (five tables on one page) names the page and quotes RFDS's sentence.
        """
        cases = {
            "facts": (dict(facts=[rx.text("raw")]),
                      "Page `fund`, slot `facts`: got a raw component (`Text`).\n"
                      "Use `ui.fact(label, value)`."),
            "no section": (dict(sections=[]),
                           "Page `fund`, slot `sections`: a record page needs at least one section.\n"
                           "Add `sections=[ui.section(\"Details\", ...)]`."),
            "no title": (dict(title=""), "Page `fund`: the page has no title.\n"
                                         "Give the kind `title=\"...\"`, or a state value."),
            "status": (dict(status=vocab.text("Open")),
                       "Page `fund`, slot `status`: got `ui.text`.\n`status=` holds one `ui.status(word, tone=)`."),
            "sections": (dict(sections=[vocab.text("loose")]),
                         "Page `fund`, slot `sections`: got `ui.text`.\n`sections=` holds `ui.section(title, ...)`."),
        }
        cases["same title"] = (dict(sections=[kinds.section("Notes", vocab.text("a")), kinds.section("Notes", vocab.text("b"))]),
                               "Page `fund`, slot `sections`: two sections are both called `Notes`.\n"
                               "Give each section its own title.")
        cases["section by state"] = (
            dict(sections=[kinds.section("Notes", vocab.text("a"), visible=KindDesk.name != "")]),
            "Page `fund`, slot `sections[0]`: `visible=` on a section of a record page takes a permission.\n"
            "A section's heading always shows. To hide the section from some users write `visible=ui.read(Model)`; "
            "to show different content by state, put `ui.switch(...)` inside the section.")
        cases["blank title"] = (dict(title="   "), "Page `fund`: the page has no title.\n"
                                                    "Give the kind `title=\"...\"`, or a state value.")
        cases["title not words"] = (dict(title=5), "Page `fund`: the page's title is `5`.\n"
                                                    "Give the kind `title=\"...\"`, or a state value.")
        cases["save"] = (dict(actions=[vocab.button("Save changes", KindDesk.close)]),
                         "Page `fund`, slot `actions`: an action is called `Save changes`.\n"
                         "RFDS shows a last-saved time beside a Save action, which a page header cannot give yet. "
                         "Name the action for what it does (`\"Apply changes\"`); saving belongs to a form.")
        for name, (slots, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as raised:
                _record(**slots).build(CTX)
            self.assertEqual(str(raised.exception), message, f"{name}: the message says what to write")
        crowded = [kinds.section(f"Table {n}", tables.table(Group, columns=["name"])) for n in "abcde"]
        with self.assertRaises(PageError) as limit:
            _record(sections=crowded).build(CTX)
        self.assertTrue(str(limit.exception).startswith("Page `fund`: RFDS's page layout refuses this page: rfds."),
                        f"a limit of RFDS's own names the page and quotes RFDS: {limit.exception}")

    # -- 1.801 ---------------------------------------------------------
    def test_1_801_actions_are_placed_as_the_page_header_places_them(self) -> None:
        """
        Scenario 1.801: actions=.
        Given: record pages with one action, with two, with three of which one is dangerous,
               and with a dangerous action first.
        When:  they are built.
        Then:  the first action is the primary button -- unless it is dangerous, which is never
               primary; exactly one further action is a button; two or more further actions
               are entries of the "More actions" menu, with their translated sentences and
               their tone; a primary action with visible= stays primary; the author's part is
               not changed; something that is not a button or a link is refused.
        """
        one = str(_record(actions=[vocab.button("Edit", KindDesk.close)]).build(CTX))
        self.assertIn('"data-rfds-intent":"primary"', one, "the first action is primary")
        two = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                   vocab.button("Close fund", KindDesk.close, tone="danger")]).build(CTX))
        self.assertIn('"data-rfds-intent":"danger"', two, "one further action stays a button")
        self.assertNotIn("rfds-menu-item-label", two, "and is not put in a menu")
        three = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                     vocab.button("Close fund", KindDesk.close, tone="danger"),
                                     vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("Close fund", "Close fund"', three,
                      "two or more further actions are menu entries, translated")
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("All groups", "All groups"', three, "the link is an entry too")
        self.assertIn('"data-rfds-tone":"danger"', three, "a dangerous action keeps its tone in the menu")
        calm = str(_record(actions=[vocab.button("Edit", KindDesk.close), vocab.button("Close fund", KindDesk.close),
                                    vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertNotIn('"data-rfds-tone":"danger"', calm, "and only a dangerous one has it")
        edit = vocab.button("Edit", KindDesk.close, visible=ui.edit(Group))
        gated = str(_record(actions=[edit, vocab.button("Close fund", KindDesk.close, tone="danger")]).build(CTX))
        self.assertIn('"data-rfds-intent":"primary"', gated, "a primary action with visible= stays primary")
        self.assertIn('"data-rfds-intent":"danger"', gated, "and the action beside it is still built")
        self.assertFalse(edit.primary, "the kind marks a copy primary, never the author's part")
        danger_first = str(_record(actions=[vocab.button("Close fund", KindDesk.close, tone="danger")]).build(CTX))
        self.assertNotIn('"data-rfds-intent":"primary"', danger_first, "a dangerous action is never the primary one")
        with self.assertRaises(PageError) as wrong:
            _record(actions=[vocab.text("loose")]).build(CTX)
        self.assertIn("slot `actions`: got `ui.text`", str(wrong.exception), "what is not an action is named")

    # -- 1.802 ---------------------------------------------------------
    def test_1_802_a_list_page_is_a_title_a_table_and_its_actions(self) -> None:
        """
        Scenario 1.802: ui.list.
        Given: a list with a fixed title, a table over a model and one action that opens a page.
        When:  it is built.
        Then:  it is RFDS's list report with the translated title, the table and the primary
               action; a table slot holding anything but a ui.table is refused.
        """
        page = kinds.list_(title="Groups", table=tables.table(Group, columns=["name"], open=group_page),
                           actions=[vocab.button("First group", to=group_page, key="1")])
        text = str(page.build(Build(page="groups", route="/groups")))
        self.assertIn('rfdsI18n.t("Groups", "Groups"', text, "the title is a sentence")
        self.assertIn("rfds-data-table", text, "the table")
        self.assertIn('"data-rfds-intent":"primary"', text, "the action")
        self.assertEqual(page.loads(), ([], []), "a list without a load has none")
        with self.assertRaises(PageError) as wrong:
            kinds.list_(title="Groups", table=vocab.text("loose")).build(Build(page="groups", route="/groups"))
        self.assertEqual(str(wrong.exception),
                         "Page `groups`, slot `table`: got `ui.text`.\n"
                         "`table=` holds one `ui.table(Model, columns=[...])`.", "a wrong table is named by its ui name")
        with self.assertRaises(PageError) as no_table:
            kinds.list_(title="Groups").build(Build(page="groups", route="/groups"))
        self.assertIn("got nothing (`None`).", str(no_table.exception), "a list without a table says so")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cs_ui_page_kinds.py`
Expected: `AttributeError: module 'lex.lex_app.reflex.ui.pages.kinds' has no attribute 'record'`.

- [ ] **Step 3: Implement**

Append to `kinds.py`:

```python
def _apart(button: rx.Component) -> rx.Component:
    """``button`` in a wrapper RFDS can tell from the primary action.

    RFDS 1.0.0 sorts a page's actions with ``action not in primary``, and Reflex components of one
    type compare equal, so a second button beside a primary one was dropped. A fragment is another
    type; RFDS reads the button's kind and intent from the wrapper.
    """
    return vocab._marked(button, rx.fragment(button))


def _rfds(ctx: Build, build: Callable[[], Any]) -> Any:
    """Call one of RFDS's floorplans; a limit of its own becomes a ``PageError`` naming the page.

    ``build`` calls RFDS and nothing else: whatever it raises is reported as RFDS's refusal.
    """
    try:
        return build()
    except (ValueError, TypeError) as wrong:
        raise ctx.fail(f"RFDS's page layout refuses this page: {wrong}",
                       "The sentence above is RFDS's own: it names the limit the page goes over.") from wrong


def _section_ids(titles: list) -> list:
    """An id for each section: an anchor in the page's address, so letter-led and unlike the others."""
    ids: list = []
    for index, title in enumerate(titles):
        base = slug(title)
        if base == "part":
            base = f"section-{index + 1}"
        elif not base[0].isalpha():
            base = f"section-{base}"
        candidate, number = base, 2
        while candidate in ids:
            candidate, number = f"{base}-{number}", number + 1
        ids.append(candidate)
    return ids


class Kind:
    """A filled page: what `@ui.page` and `@ui.dashboard` functions return."""

    title: Any
    load: Any = None
    actions: tuple = ()

    def loads(self) -> tuple[list, list]:
        """(the page's load, the loads of its parts)."""
        return ([self.load] if self.load is not None else []), []

    def build(self, ctx: Build) -> rx.Component:
        raise NotImplementedError

    def _buttons(self, ctx: Build) -> list:
        """The page's actions as RFDS places them: one primary button; one more as a button, more in a menu."""
        ctx = ctx.at("actions", "actions")
        parts = take(ctx, list(self.actions), (vocab.Button, vocab.Link),
                     "`actions=` holds `ui.button(...)` (or `ui.link(...)`, shown as a button).")
        for part in parts:
            if isinstance(part.label, str) and _SAVE.search(part.label):
                raise ctx.fail(f"an action is called `{part.label}`.",
                               "RFDS shows a last-saved time beside a Save action, which a page header cannot give "
                               "yet. Name the action for what it does (`\"Apply changes\"`); saving belongs to a form.")
        first = parts[0] if parts and isinstance(parts[0], vocab.Button) and parts[0].tone != "danger" else None
        rest = [part for part in parts if part is not first]
        built: list = []
        if first is not None:
            built.append(dataclasses.replace(first, primary=True).build(ctx))     # a copy: the part is the author's
        if len(rest) == 1:
            built.append(_apart(rest[0].build(ctx)))
        else:
            built.extend(part.menu_entry(ctx) for part in rest)
        return built

    def _title(self, ctx: Build) -> Any:
        title = self.title
        if title is None or (isinstance(title, str) and not title.strip()):
            raise ctx.fail("the page has no title.", "Give the kind `title=\"...\"`, or a state value.")
        try:
            return say(title)
        except NotASentence:
            raise ctx.fail(f"the page's title is {called(title)}.",
                           "Give the kind `title=\"...\"`, or a state value.") from None


@dataclasses.dataclass(eq=False)
class Record(Kind):
    title: Any
    load: Any = None
    status: Any = None
    facts: tuple = ()
    sections: tuple = ()
    tabs: bool = False
    actions: tuple = ()

    def loads(self) -> tuple[list, list]:
        return ([self.load] if self.load is not None else []), [s.load for s in self.sections if s.load is not None]

    def _sections(self, ctx: Build) -> list:
        """The sections as RFDS's object page takes them, each checked first."""
        from rfds import templates

        from lex.lex_app.reflex.access import permission
        from lex.lex_app.reflex.ui import guard

        parts = take(ctx.at("sections"), list(self.sections), (Section,), "`sections=` holds `ui.section(title, ...)`.")
        if not parts:
            raise ctx.at("sections").fail("a record page needs at least one section.",
                                          "Add `sections=[ui.section(\"Details\", ...)]`.")
        titles = [part.title for part in parts]
        twice = sorted({title for title in titles if titles.count(title) > 1})
        if twice:
            raise ctx.at("sections").fail(f"two sections are both called `{twice[0]}`.",
                                          "Give each section its own title.")
        built: list = []
        for index, (part, ident) in enumerate(zip(parts, _section_ids(titles))):
            at = ctx.at(f"sections[{index}]")
            needs = None
            if isinstance(part.visible, guard.Requirement):
                needs = permission(part.visible.target, part.visible.scope)     # RFDS keeps the heading and says why
            elif not (part.visible is None or part.visible is guard.signed_in):
                raise at.fail("`visible=` on a section of a record page takes a permission.",
                              "A section's heading always shows. To hide the section from some users write "
                              "`visible=ui.read(Model)`; to show different content by state, put `ui.switch(...)` "
                              "inside the section.")
            content = part.body(at)
            built.append(_rfds(at, lambda part=part, ident=ident, content=content, needs=needs: templates.ObjectSection(
                id=ident, title_id=part.title, title_default=part.title, content=content, permission=needs)))
        return built

    def build(self, ctx: Build) -> rx.Component:
        import rfds
        from rfds import templates

        head: list = []
        if self.status is not None:
            (badge,) = take(ctx.at("status"), self.status, (vocab.Status,), "`status=` holds one `ui.status(word, tone=)`.")
            head.append(badge.build(ctx.at("status")))
        facts = take(ctx.at("facts"), list(self.facts), (vocab.Fact,), "Use `ui.fact(label, value)`.")
        if facts:
            head.append(rfds.key_value(*vocab.build_all(ctx.at("facts"), facts), columns=min(3, len(facts))))
        title, actions, sections = self._title(ctx), self._buttons(ctx), self._sections(ctx)   # lex's checks first
        header = rfds.stack(*head) if head else rx.fragment()
        page = _rfds(ctx, lambda: templates.object_page(
            title=title, path=ctx.route or "/", header=header, sections=sections,
            navigation="tabs" if self.tabs and len(sections) > 1 else "anchor", actions=actions))
        return states(ctx, self.load, page, "rect")


def record(*, title: Any, load: Any = None, status: Any = None, facts: Any = (), sections: Any = (),
           tabs: bool = False, actions: Any = ()) -> Record:
    """One thing: its key facts, then its parts.

    The first of ``actions`` is the primary one, unless it is dangerous or a link; one further
    action is a button, and two or more further actions go into a "More actions" menu.
    ``tabs=True`` shows one section at a time.
    """
    return Record(title, load, status, tuple(facts), tuple(sections), bool(tabs), tuple(actions))


@dataclasses.dataclass(eq=False)
class List(Kind):
    title: Any
    load: Any = None
    table: Any = None
    actions: tuple = ()

    def build(self, ctx: Build) -> rx.Component:
        from rfds import templates

        if not isinstance(self.table, tables.Table):
            raise ctx.at("table").fail(f"got {called(self.table)}.",
                                       "`table=` holds one `ui.table(Model, columns=[...])`.")
        title, actions, table = self._title(ctx), self._buttons(ctx), self.table.build(ctx.at("table"))
        page = _rfds(ctx, lambda: templates.list_report(title=title, path=ctx.route or "/", table=table, actions=actions))
        return states(ctx, self.load, page, "table")


def list_(*, title: Any, load: Any = None, table: Any = None, actions: Any = ()) -> List:
    """Finding things: a table to search, sort and open from. ``actions`` are placed as on a record page."""
    return List(title, load, table, tuple(actions))
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cs_ui_page_kinds.py`
Expected: 4 passed, 12 subtests passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/test_project/tests/init/test_1cs_ui_page_kinds.py
git commit -m "feat(reflex): lex.ui pages - the record and list kinds on RFDS's floorplans"
```

End the message with the co-author line for your own model.

---

### Task 7: `@ui.page`, `@ui.dashboard`, and registration once the module is loaded

**Files:**
- Create: `lex/lex_app/reflex/ui/pages/declare.py`
- Modify: `lex/lex_app/reflex/app.py` (`create_app`)
- Test: `lex/test_project/tests/init/test_1ct_ui_page_declare.py`

**Interfaces:**
- Consumes: `shell.lex_page(route, *, title, nav=, icon=, requires=)` (chrome, navigation entry, `rx.page`, the opener), `handlers.register_page`, `kinds.Kind`, `links.info_of`, `access.permission`.
- Produces: `declare.page(route, *, nav=None, icon=None, requires=None, params=())` (sets `fn.__ui_page__`, records the page in `declare._DECLARED`, returns `fn`); `declare.finalize()`; `declare.dashboard(fn)` → a function returning the built component, with `.__ui_dashboard__ = fn`.

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1ct_ui_page_declare.py`:

```python
"""lex.ui pages: @ui.page says where a page lives; lex-app registers it once the module is loaded.

Intent: a page function returns a page kind and may name pages declared further down, so it is
not called when it is decorated. Once the project's module is loaded, lex-app hands every
declared page its chrome, its navigation entry, its permission and its loads: the kind's load as
the page's required load and each section's load as an optional one. A dashboard is not a route:
@ui.dashboard builds the kind and starts its loads on mount. A regression is a page that cannot
link forward, a page whose loads never start, or a navigation entry without words.

Cluster 1ct -- scenarios 1.803-1.805. Type: U.
Covers: lex/lex_app/reflex/ui/pages/declare.py (page, dashboard, finalize),
lex/lex_app/reflex/app.py (create_app).
Run: python -m lex pytest lex/test_project/tests/init/test_1ct_ui_page_declare.py -v
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare, kinds, links, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import PageError

pytestmark = pytest.mark.init


class DeclareDesk(ui.State):
    name: str = ""

    @ui.load
    def open(self):
        self.name = "Fund One"

    @ui.load
    def load_documents(self):
        self.name = self.name

    @ui.action
    def touch(self):
        self.name = "touched"


class TestCluster01ct_PageDeclare(SimpleTestCase):
    """1.803-1.805: declaring pages; what finalize hands to lex-app; dashboards."""

    def setUp(self) -> None:
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", [])):
            target.start()
            self.addCleanup(target.stop)
        added = patch.object(rx, "page", lambda **kwargs: (lambda fn: self.pages.append((kwargs, fn)) or fn))
        self.pages: list = []
        added.start()
        self.addCleanup(added.stop)

    # -- 1.803 ---------------------------------------------------------
    def test_1_803_a_page_is_declared_without_being_called_and_may_link_forward(self) -> None:
        """
        Scenario 1.803: @ui.page.
        Given: two pages, the first linking to the second, which is declared below it.
        When:  they are decorated, and lex-app then registers the declared pages.
        Then:  decorating calls neither function and returns it unchanged, with what a link
               needs (route, its arguments, its declared query parameters); registration builds
               each page in lex-app's chrome under its route, with the permission from
               requires= as RFDS's permission string and the opener last in its load events. A
               route that does not start with "/", a requires= that is not one permission, a
               params= given as one string, and a second function at a route are refused when
               the page is declared.
        """
        calls: list = []

        @declare.page("/funds", nav="Funds")
        def funds():
            calls.append("funds")
            return kinds.list_(title="Funds", table=tables.table(Group, columns=["name"], open=fund),
                               actions=[vocab.button("First", to=fund, key="1")])

        @declare.page("/funds/[key]", requires=ui.read(Group), params=["tab"])
        def fund():
            calls.append("fund")
            return kinds.record(title=DeclareDesk.name, load=DeclareDesk.open,
                                sections=[kinds.section("Documents", vocab.text("x"), load=DeclareDesk.load_documents)])

        self.assertEqual(calls, [], "decorating does not call the page function")
        self.assertEqual(links.page_info(fund), links.PageInfo("fund", "/funds/[key]", ("key",), ("tab",)),
                         "the function carries what a link needs")
        declare.finalize()
        routes = {kwargs["route"]: (kwargs, fn) for kwargs, fn in self.pages}
        self.assertEqual(sorted(routes), ["/funds", "/funds/[key]"], "both pages are handed to Reflex under their routes")
        kwargs, body = routes["/funds"]
        self.assertEqual(kwargs["title"], "Funds", "the browser tab's title is the kind's title")
        self.assertIn("ui_open", str(kwargs["on_load"][-1]), "the opener is the last load event")
        self.assertIn("/funds/1", str(body()), "the forward link is resolved when the page is built")
        self.assertEqual(handlers.page_of("/funds/[key]").requires, "auth.Group:read",
                         "requires= becomes the page's permission string")
        with self.assertRaises(ValueError, msg="a route starts with /"):
            declare.page("funds")
        with self.assertRaises(TypeError) as wrong_requires:
            declare.page("/open", requires=[ui.read(Group)])
        self.assertIn("Leave it out for a page any signed-in user may see", str(wrong_requires.exception),
                      "a wrong requires= is refused when the page is declared, with the route and what to write")
        self.assertIn("'/open'", str(wrong_requires.exception), "the route is named")
        declare.page("/anyone", requires=ui.signed_in)(lambda: None)
        self.assertIsNone(declare._DECLARED["/anyone"].permission, "ui.signed_in is a page for any signed-in user")
        declare._DECLARED.pop("/anyone")
        with self.assertRaises(TypeError, msg="params= is a list of names, not one string"):
            declare.page("/funds", params="tab")
        with self.assertRaises(ValueError) as twice:
            @declare.page("/funds")
            def other_funds():
                return None
        self.assertIn("`other_funds` needs a route of its own", str(twice.exception),
                      "two functions cannot be the page at one route")

    # -- 1.804 ---------------------------------------------------------
    def test_1_804_registration_hands_over_the_navigation_entry_and_the_loads(self) -> None:
        """
        Scenario 1.804: declare.finalize.
        Given: a page in the navigation with a fixed title, a record page with a page load and a
               section load, a page in the navigation whose title is a state value, and a page
               function that returns a raw component.
        When:  lex-app registers the declared pages.
        Then:  the navigation entry carries the kind's title; the kind's load is registered as
               the page's required load and the section's load as an optional one; registering
               twice adds nothing; a navigation page without a fixed title, a load= that is not
               a @ui.load handler, one load given twice, a page function that raises, and a
               function that does not return a kind, are each refused with the page named.
        """
        @declare.page("/funds", nav="Funds", icon="list")
        def funds():
            return kinds.list_(title="All funds", table=tables.table(Group, columns=["name"]))

        @declare.page("/funds/[key]")
        def fund():
            return kinds.record(title=DeclareDesk.name, load=DeclareDesk.open,
                                sections=[kinds.section("Documents", vocab.text("x"), load=DeclareDesk.load_documents)])

        declare.finalize()
        declare.finalize()
        self.assertEqual([(e.route, e.label, e.section, e.icon) for e in shell.nav_entries()],
                         [("/funds", "All funds", "Funds", "list")], "one navigation entry, labelled with the title")
        entry = handlers.page_of("/funds/[key]")
        self.assertEqual(([h.fn.__name__ for h in entry.required], [h.fn.__name__ for h in entry.optional]),
                         (["open"], ["load_documents"]), "the page load is required, the section's load optional")
        self.assertEqual(len(self.pages), 2, "registering twice adds nothing")

        @declare.page("/moving", nav="Funds")
        def moving():
            return kinds.record(title=DeclareDesk.name, sections=[kinds.section("A", vocab.text("a"))])

        with self.assertRaises(PageError) as no_title:
            declare.finalize()
        self.assertEqual(str(no_title.exception),
                         "Page `moving`: a page listed in the navigation needs a fixed title.\n"
                         "Give the kind `title=\"...\"`, or leave `nav=` out.", "a navigation page needs fixed words")
        declare._DECLARED.pop("/moving")

        @declare.page("/acting")
        def acting():
            return kinds.record(title="Acting", load=DeclareDesk.touch, sections=[kinds.section("A", vocab.text("a"))])

        with self.assertRaises(PageError) as not_a_load:
            declare.finalize()
        self.assertTrue(str(not_a_load.exception).startswith("Page `acting`: `load=` got "),
                        f"a handler that is not a load is refused as the page's load: {not_a_load.exception}")
        self.assertIn("Pass a handler declared with `@ui.load`, without arguments", str(not_a_load.exception),
                      "with what to pass")
        declare._DECLARED.pop("/acting")

        @declare.page("/doubled")
        def doubled():
            return kinds.record(title="Doubled", load=DeclareDesk.open,
                                sections=[kinds.section("A", vocab.text("a"), load=DeclareDesk.open)])

        with self.assertRaises(PageError) as same_load:
            declare.finalize()
        self.assertIn("is given as a load twice", str(same_load.exception), "one load is the page's or a section's")
        declare._DECLARED.pop("/doubled")

        @declare.page("/broken")
        def broken():
            return kinds.record(title="Broken", sections=[kinds.section(5)])

        with self.assertRaises(PageError) as raised:
            declare.finalize()
        self.assertTrue(str(raised.exception).startswith("Page `broken`: its function raised TypeError:"),
                        f"an error inside a page function is given the page's name: {raised.exception}")
        declare._DECLARED.pop("/broken")

        @declare.page("/raw")
        def raw():
            return rx.text("not a kind")

        with self.assertRaises(PageError) as not_a_kind:
            declare.finalize()
        self.assertEqual(str(not_a_kind.exception),
                         "Page `raw`: it returned Text.\nReturn a page kind: `ui.record(...)`, `ui.list(...)`.",
                         "a function that does not return a kind is told what to return")

    # -- 1.805 ---------------------------------------------------------
    def test_1_805_a_dashboard_starts_its_loads_on_mount_and_create_app_registers_the_pages(self) -> None:
        """
        Scenario 1.805: @ui.dashboard and create_app.
        Given: a dashboard function returning a record with a load, and lex-app's create_app.
        When:  the dashboard is called, and create_app's source is read.
        Then:  the dashboard returns the built page, a component, whose load starts when it
               mounts (a dashboard has no opener); it is not a link target; on a model it sits
               under @classmethod and receives the model; create_app loads the project's
               module, then registers the declared pages, before it adds the home page.
        """
        @declare.dashboard
        def main():
            return kinds.record(title="Portfolio", load=DeclareDesk.open, sections=[kinds.section("A", vocab.text("a"))])

        built = main()
        self.assertIsInstance(built, rx.Component, "a dashboard returns the built page")
        self.assertIn("on_mount", built.event_triggers, "its load starts when it mounts")
        self.assertIsNone(links.page_info(main), "a dashboard is not a page to link to")
        self.assertEqual(main.__name__, "main", "it keeps its name, for lex-app's structure module")

        class Model:
            """Stands for a project model with a dashboard hook."""

            @classmethod
            @declare.dashboard
            def reflex_main(cls):
                return kinds.record(title=cls.__name__, sections=[kinds.section("A", vocab.text("a"))])

        self.assertIsInstance(Model.reflex_main(), rx.Component, "a model's hook is a class method above @ui.dashboard")
        self.assertIn('rfdsI18n.t("Model", "Model"', str(Model.reflex_main()), "and the function receives the model")
        source = inspect.getsource(lex_app_module.create_app)
        self.assertLess(source.index("load_structure()"), source.index("declare.finalize()"),
                        "the project's module is loaded before its pages are registered")
        self.assertLess(source.index("declare.finalize()"), source.index("dashboard_page(structure)"),
                        "and the pages are registered before the app's pages are added")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1ct_ui_page_declare.py`
Expected: `ImportError: cannot import name 'declare' from 'lex.lex_app.reflex.ui.pages'`.

- [ ] **Step 3: Implement**

`lex/lex_app/reflex/ui/pages/declare.py`:

```python
"""@ui.page and @ui.dashboard: where a page lives."""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from lex.lex_app.reflex.ui.pages import kinds, links
from lex.lex_app.reflex.ui.pages.parts import Build, PageError, called


@dataclasses.dataclass
class Declared:
    fn: Callable[[], Any]
    info: links.PageInfo
    nav: str | None
    icon: str | None
    permission: str | None
    done: bool = False


#: Route -> the page declared for it, until :func:`finalize` hands it to lex-app.
_DECLARED: dict[str, Declared] = {}


def _kind_of(fn: Callable[..., Any], *args: Any) -> kinds.Kind:
    """What the page function returns, checked; an error of its own is given the page's name."""
    try:
        kind = fn(*args)
    except PageError:
        raise
    except Exception as wrong:
        raise PageError(f"Page `{fn.__name__}`: its function raised {type(wrong).__name__}: {wrong}\n"
                        "A page function only describes the page: it returns `ui.record(...)` or "
                        "`ui.list(...)` and does nothing else.") from wrong
    if not isinstance(kind, kinds.Kind):
        raise PageError(f"Page `{fn.__name__}`: it returned {type(kind).__name__}.\n"
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`.")
    return kind


def _loads(name: str, kind: kinds.Kind) -> tuple[list, list]:
    """The kind's (page loads, part loads), each checked to be a load a page can start."""
    page_loads, part_loads = kind.loads()
    for load in (*page_loads, *part_loads):
        if getattr(getattr(load, "fn", None), "__ui_kind__", "") != "load":
            raise PageError(f"Page `{name}`: `load=` got {called(load)}.\n"
                            "Pass a handler declared with `@ui.load`, without arguments: `load=FundState.open`. "
                            "It reads the page's values from `self.params`.")
    seen: list = []
    for load in (*page_loads, *part_loads):
        if any(load is other for other in seen):
            raise PageError(f"Page `{name}`: `{load.fn.__qualname__}` is given as a load twice.\n"
                            "A load belongs to the page or to one section.")
        seen.append(load)
    return page_loads, part_loads


def _permission(route: str, requires: Any) -> str | None:
    """The page's permission string; ``None`` when any signed-in user may see it."""
    from lex.lex_app.reflex.access import permission
    from lex.lex_app.reflex.ui import guard

    if requires is None or requires is guard.signed_in:
        return None
    if isinstance(requires, guard.Requirement):
        return permission(requires.target, requires.scope)
    raise TypeError(f"ui.page({route!r}, requires=) takes one permission: ui.read(Model), ui.create(Model), "
                    f"ui.edit(Model), ui.delete(Model) or ui.permission(...); got {requires!r}. "
                    "Leave it out for a page any signed-in user may see.")


def page(route: str, *, nav: str | None = None, icon: str | None = None, requires: Any = None,
         params: Any = ()) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Declare a page: its address, its place in the navigation, who may see it.

    The function returns a page kind and does nothing else: lex-app calls it when it registers
    the pages -- after the whole module is loaded, so it may name pages declared further down --
    and again when the page is built. A page that is declared wrongly stops the app from starting,
    with an error that names it.
    """
    if not route.startswith("/"):
        raise ValueError(f"ui.page() routes start with '/', got {route!r}")
    if isinstance(params, str):
        raise TypeError(f"ui.page({route!r}, params=) takes a list of names: params=[{params!r}]")
    permission = _permission(route, requires)

    def decorator(fn: Callable[[], Any]) -> Callable[[], Any]:
        known = _DECLARED.get(route)
        if known is not None and (known.fn.__module__, known.fn.__qualname__) != (fn.__module__, fn.__qualname__):
            raise ValueError(f"ui.page({route!r}): `{known.fn.__name__}` already is the page at this route; "
                             f"`{fn.__name__}` needs a route of its own.")
        fn.__ui_page__ = links.info_of(route, fn.__name__, params)
        _DECLARED[route] = Declared(fn, fn.__ui_page__, nav, icon, permission)
        return fn

    return decorator


def finalize() -> None:
    """Hand every declared page to lex-app: chrome, navigation, permission and its loads.

    Called once the project's module is loaded, before the app is compiled.
    """
    import reflex as rx

    from lex.lex_app.reflex.shell import lex_page
    from lex.lex_app.reflex.ui import handlers

    for route, declared in list(_DECLARED.items()):
        if declared.done:
            continue
        name = declared.fn.__name__
        kind = _kind_of(declared.fn)
        title = kind.title
        if declared.nav and not isinstance(title, str):
            raise PageError(f"Page `{name}`: a page listed in the navigation needs a fixed title.\n"
                            "Give the kind `title=\"...\"`, or leave `nav=` out.")
        required, optional = _loads(name, kind)

        def body(fn: Callable[[], Any] = declared.fn, route: str = route) -> rx.Component:
            return _kind_of(fn).build(Build(page=fn.__name__, route=route))

        body.__name__ = name
        body.__qualname__ = declared.fn.__qualname__
        lex_page(route, title=title if isinstance(title, str) else name.replace("_", " ").capitalize(),
                 nav=declared.nav, icon=declared.icon, requires=declared.permission)(body)
        handlers.register_page(route, required, optional, declared.permission or "")
        declared.done = True


def dashboard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a page that is not a route: the project's ``main()``, or a model's dashboard hook.

    On a model the hook is a class method; ``@classmethod`` goes above ``@ui.dashboard``. A
    dashboard has no opener, so its loads start when their part of the page mounts.
    """

    def built(*args: Any) -> Any:
        kind = _kind_of(fn, *args)
        _loads(fn.__name__, kind)
        return kind.build(Build(page=fn.__name__, mounts=True))

    built.__name__ = fn.__name__
    built.__qualname__ = fn.__qualname__
    built.__doc__ = fn.__doc__
    built.__ui_dashboard__ = fn
    return built
```

In `lex/lex_app/reflex/app.py`:

```diff
diff --git a/lex/lex_app/reflex/app.py b/lex/lex_app/reflex/app.py
index 71a299e2..3ae39517 100644
--- a/lex/lex_app/reflex/app.py
+++ b/lex/lex_app/reflex/app.py
@@ -93,9 +93,13 @@ def create_app():
     from lex.lex_app.reflex.ui.state import State as UiState
 
     _load_lazy_states()  # rfds.toast defines states; imported mid-event, they would register too late
+    structure = load_structure()
+    from lex.lex_app.reflex.ui.pages import declare
+
+    declare.finalize()  # the project's module is loaded: hand its @ui.page pages to lex-app
     register_page("/", [], [], "")
     app.add_page(
-        dashboard_page(load_structure()),
+        dashboard_page(structure),
         route="/",
         on_load=[LexAccessState.load, LexDashboardState.resolve, UiState.ui_open("/")],
     )
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1ct_ui_page_declare.py`
Expected: 3 passed. Then `RUN lex/test_project/tests/init/test_1cj_ui_load.py` (12 passed): its test 1.778 reads `create_app`'s source.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/pages lex/lex_app/reflex/app.py lex/test_project/tests/init/test_1ct_ui_page_declare.py
git commit -m "feat(reflex): lex.ui pages - @ui.page and @ui.dashboard; pages are registered once the project's module is loaded"
```

End the message with the co-author line for your own model.

---

### Task 8: The public names, and two pages end to end

**Files:**
- Modify: `lex/ui.py`
- Test: `lex/test_project/tests/init/test_1cu_ui_pages_namespace.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `ui.page`, `ui.dashboard`, `ui.record`, `ui.list`, `ui.section`, `ui.text`, `ui.fact`, `ui.status`, `ui.message`, `ui.table`, `ui.column`, `ui.button`, `ui.link`, `ui.each`, `ui.switch`, `ui.custom_block`, `ui.PageError` (and `ui.loaded` from Task 1).

- [ ] **Step 1: Write the failing test**

Create `lex/test_project/tests/init/test_1cu_ui_pages_namespace.py`:

```python
"""lex.ui pages: one import for the page kinds and the vocabulary; two pages written end to end.

Intent: a project writes `from lex import ui` and finds the page decorators, the kinds and the
vocabulary beside the handler layer -- and nothing that takes a styling argument. Two pages
written only with those names, a list that opens a record, build, register and load exactly as
a project's would. A regression is a name missing from the namespace, a styling argument
creeping into the vocabulary, or a page that builds but never loads.

Cluster 1cu -- scenarios 1.806-1.807. Type: I.
Covers: lex/ui.py, lex/lex_app/reflex/ui/pages/.
Run: python -m lex pytest lex/test_project/tests/init/test_1cu_ui_pages_namespace.py -v
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init

PAGE_NAMES = ("page", "dashboard", "record", "list", "section", "text", "fact", "status", "message", "table",
              "column", "button", "link", "each", "switch", "custom_block", "loaded", "PageError")


class ExampleFund(ui.State):
    """The state of the example's record page."""

    name: str = ""
    members: int = 0
    notes: list[str] = []

    @ui.load(requires=ui.read(Group))
    def open(self):
        group = Group.objects.filter(pk=self.params.get("key", "0")).first()
        if group is None:
            raise ui.Refuse("This group does not exist.")
        self.name, self.members = group.name, group.user_set.count()

    @ui.load
    def load_notes(self):
        self.notes = [f"{self.name} was opened by {self.user.name}"]     # reads what the page load wrote


class TestCluster01cu_PagesNamespace(SimpleTestCase):
    """1.806-1.807: the namespace; a list and a record page, end to end."""

    databases = {"default"}

    # -- 1.806 ---------------------------------------------------------
    def test_1_806_one_import_gives_the_page_kinds_and_the_vocabulary(self) -> None:
        """
        Scenario 1.806: lex.ui.
        Given: `from lex import ui`.
        When:  the names of this piece are looked up.
        Then:  each exists and is listed in __all__ -- except ui.list, the page kind, which a
               star import must not put in place of Python's list; no component or kind takes
               a styling argument.
        """
        for name in PAGE_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(ui, name), f"ui.{name} exists")
                if name != "list":
                    self.assertIn(name, ui.__all__, f"ui.{name} is listed")
        self.assertEqual(ui.list.__name__, "list_", "ui.list is the page kind")
        self.assertNotIn("list", ui.__all__, "`from lex.ui import *` does not hide Python's list")
        styling = {"class_name", "style", "props", "gap", "width", "padding", "margin", "align", "justify"}
        for name in ("record", "list", "section", "text", "fact", "status", "message", "table", "column", "button",
                     "link", "each", "switch", "custom_block"):
            with self.subTest(component=name):
                self.assertFalse(set(inspect.signature(getattr(ui, name)).parameters) & styling,
                                 f"ui.{name} takes no styling argument")

    # -- 1.807 ---------------------------------------------------------
    def test_1_807_a_list_and_a_record_page_build_register_and_load(self) -> None:
        """
        Scenario 1.807: two pages, as a project writes them.
        Given: a list page over a model whose rows open a record page; the record page has a
               page load that reads the database (it declares what it requires), a section that
               loads on its own, a fact, a status shown only to editors, and an action.
        When:  lex-app registers them, builds them, and the record page is opened by a user
               with the grant.
        Then:  both build without one raw component in the page functions; the list links its
               rows to the record page; opening the record page runs the page load and then the
               section's load, both end loaded, and the state holds what they read.
        """
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", []), patch.object(rx, "page", lambda **kwargs: (lambda fn: fn)),
                       patch.object(ui_state, "_page_permitted", return_value=True)):
            target.start()
            self.addCleanup(target.stop)

        @ui.page("/groups", nav="Groups")
        def groups():
            return ui.list(title="Groups", table=ui.table(Group, columns=["name"], open=group, empty="No groups yet."))

        @ui.page("/groups/[key]", requires=ui.read(Group))
        def group():
            return ui.record(
                title=ExampleFund.name,
                load=ExampleFund.open,
                status=ui.status("Editable", tone="info", visible=ui.edit(Group)),
                facts=[ui.fact("Members", ExampleFund.members, kind="number")],
                sections=[ui.section("Notes", ui.each(ExampleFund.notes, lambda note: ui.text(note)),
                                     load=ExampleFund.load_notes)],
                actions=[ui.button("All groups", to=groups)],
            )

        declare.finalize()
        listing = str(groups().build(Build(page="groups", route="/groups")))
        self.assertIn("/groups/", listing, "the list's rows open the record page")
        record = str(group().build(Build(page="group", route="/groups/[key]")))
        self.assertIn('rfdsI18n.t("Members", "Members"', record, "the record page builds")
        for function in (groups, group):
            source = inspect.getsource(function)
            self.assertNotIn("rx.", source, f"{function.__name__} uses no raw Reflex component")
            self.assertNotIn("rfds.", source, f"{function.__name__} uses no raw RFDS component")

        made, created = Group.objects.get_or_create(name="Example group")
        self.addCleanup(made.delete)

        async def scenario():
            async with testing.Harness(user="ada", grants=["auth.Group:read"], params={"key": str(made.pk)}) as h:
                await h.open_tab(f"/groups/{made.pk}")
                opener = await h.send(ui.State, "ui_open", page="/groups/[key]")
                await opener
                for child in list(opener.children):
                    await child
                    for grandchild in list(child.children):
                        await grandchild
                return await h.fields(ui.State), await h.fields(ExampleFund)

        status, fields = asyncio.run(scenario())
        self.assertEqual(status["ui_loaded"], {"ExampleFund.open": True, "ExampleFund.load_notes": True},
                         "the page load and then the section's load both finished")
        self.assertEqual(status["ui_failed"], {}, "nothing failed")
        self.assertEqual((fields["name"], fields["members"]), ("Example group", 0), "the state holds what the page read")
        self.assertEqual(fields["notes"], ["Example group was opened by ada"],
                         "the section's load ran after the page's: it read the name the page load wrote")
```

- [ ] **Step 2: Run it and see it fail**

Run: `RUN lex/test_project/tests/init/test_1cu_ui_pages_namespace.py`
Expected: `AttributeError: module 'lex.ui' has no attribute 'page'`.

- [ ] **Step 3: Implement**

In `lex/ui.py`, add below the import from `handlers`:

```python
from lex.lex_app.reflex.ui.pages.declare import dashboard, page
from lex.lex_app.reflex.ui.pages.kinds import list_ as list  # noqa: A001 - ui.list is the page kind
from lex.lex_app.reflex.ui.pages.kinds import record, section
from lex.lex_app.reflex.ui.pages.parts import PageError
from lex.lex_app.reflex.ui.pages.tables import column, table
from lex.lex_app.reflex.ui.pages.vocab import button, custom_block, each, fact, link, message, status, switch, text
```

Replace `__all__` with:

```python
__all__ = [
    "ApiError", "PageError", "Refuse", "State", "action", "button", "cancel", "column", "create", "custom",
    "custom_block", "dashboard", "delete", "download", "each", "edit", "fact", "failed", "goto", "job",
    "link", "load", "loaded", "message", "page", "permission", "progress", "progress_of", "read",
    "record", "running", "section", "signed_in", "status", "switch", "table", "testing", "text", "toast",
]
```

Add to the module docstring, after the first example and before the table, a second example:

```text
    @ui.page("/funds/[key]", requires=ui.read(Fund))
    def fund():
        return ui.record(
            title=FundState.name,
            load=FundState.open,
            facts=[ui.fact("Vintage", FundState.vintage)],
            sections=[ui.section("Holdings", ui.table(Holding, columns=["name"]), empty="No holdings yet.")],
            actions=[ui.button("Close fund", FundState.close, tone="danger")],
        )
```

and these rows to the table:

```text
``@ui.page``                where a page lives: its route, navigation, who may see it
``@ui.dashboard``           a page that is not a route (the project's ``main()``)
``ui.record/list``          the page kinds: named slots, placed by lex-app (``ui.list`` is
                            left out of ``import *``, which would hide Python's ``list``)
``ui.section``              a titled part of a page; ``load=`` makes it load on its own
``ui.text/fact/status/      what fills the slots; none takes a styling argument
message/table/column/
button/link``
``ui.each/switch``          repetition and choice; ``visible=`` on every component
``ui.custom_block``         a raw component, marked for review with its reason
``ui.PageError``            a page declared wrongly: says the page, the slot, what to write
```

- [ ] **Step 4: Run it and see it pass**

Run: `RUN lex/test_project/tests/init/test_1cu_ui_pages_namespace.py`
Expected: 2 passed, 32 subtests passed. Then `RUN lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py` (3 passed) and `RUN lex/test_project/tests/init/test_1by_reflex_without_enterprise.py` (the no-Enterprise probe imports `lex.ui`; all pass).

- [ ] **Step 5: Commit**

```bash
git add lex/ui.py lex/test_project/tests/init/test_1cu_ui_pages_namespace.py
git commit -m "feat(reflex): lex.ui pages - the page kinds and the vocabulary in the lex.ui namespace"
```

End the message with the co-author line for your own model.

---

### Task 9: The test plan, and every Reflex batch together

**Files:**
- Modify: `lex/test_project/test-plan/clusters/01-init/allocation.yaml`, `batches.md`, `cluster.md`; `lex/test_project/test-plan/progress/dashboard.md` (generated)
- Create: `lex/test_project/test-plan/progress/sessions/2026-11-23-1cn-1cu-lex-ui-pages.md`

Read the last entries of each file first (`cm`, `1cm`) and match their shape exactly. Never hand-edit `dashboard.md`.

- [ ] **Step 1: Add the eight batches**

In `allocation.yaml`, set `max_scenario: 807` and append one entry per batch (`status: complete`, `tests.pass` = the count, `skip: 0`, `xfail: 0`):

| Letter | Title | Scenarios | Type | Tests | Test file |
| --- | --- | --- | --- | --- | --- |
| cn | lex.ui: a load says when it has finished; the page registry decides who owns the error page | 1.786-1.787 | I | 2 | `test_1cn_ui_loaded.py` |
| co | lex.ui pages: parts, translated sentences, text / fact / status / message | 1.788-1.790 | U | 3 | `test_1co_ui_page_parts.py` |
| cp | lex.ui pages: links by page function; ui.link and ui.button | 1.791-1.793 | U | 3 | `test_1cp_ui_page_links.py` |
| cq | lex.ui pages: sections with their own loading and failure; each, switch, custom_block | 1.794-1.796 | U | 3 | `test_1cq_ui_page_sections.py` |
| cr | lex.ui pages: ui.table over a model; rows that open a page | 1.797-1.798 | U | 2 | `test_1cr_ui_page_tables.py` |
| cs | lex.ui pages: the record and list kinds | 1.799-1.802 | U | 4 | `test_1cs_ui_page_kinds.py` |
| ct | lex.ui pages: @ui.page, @ui.dashboard, registration once the module is loaded | 1.803-1.805 | U | 3 | `test_1ct_ui_page_declare.py` |
| cu | lex.ui pages: the namespace; a list and a record page end to end | 1.806-1.807 | I | 2 | `test_1cu_ui_pages_namespace.py` |

Confirm each count with `grep -c "    def test_" <file>`. Each entry's `note` says, in two or three sentences, what the module does, which decision of the spec it implements (section number), and what the tests would catch — taken from the test file's module docstring and the module itself. In `batches.md`, append one block per batch in the shape of `1cm` (the property table, then Scenario | Title | Asserts with one row per scenario from the test's docstring). In `cluster.md`, append one section per batch in the shape of `1cm`.

Batch `1cm`'s scenario 1.783 changed its last assertion in Task 1 (the namespace now also holds the page names, covered by `1cu`): bring its row in `batches.md` and `cluster.md` into line with the test's docstring.

- [ ] **Step 2: Add the session fragment**

`lex/test_project/test-plan/progress/sessions/2026-11-23-1cn-1cu-lex-ui-pages.md`:

```markdown
---
date: 2026-11-23
clusters: [1]
tests_added: 22
suite_tally: "<from step 4: every Reflex batch, N pass / 0 fail, M subtests pass>"
---

# lex.ui pages, step 1: the record and list kinds (1cn-1cu)

A dashboard page is declared with `@ui.page` and returns a page kind -- `ui.record` or `ui.list` --
whose named slots hold lex components that only describe: no styling argument, no layout, no
loading branch, no route string, no translation key. lex-app builds the page on RFDS's floorplans,
checks every slot, and registers the page's loads. Batches
[1cn to 1cu](../../clusters/01-init/batches.md) cover each module. Design:
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.
```

- [ ] **Step 3: Regenerate the dashboard**

Run: `.venv-oss/bin/python .github/scripts/test_plan_aggregates.py build && .venv-oss/bin/python .github/scripts/test_plan_aggregates.py check`
Expected: `wrote ... dashboard.md`, then `OK`.

- [ ] **Step 4: Run every Reflex batch together**

```bash
T=lex/test_project/tests
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd) \
  .venv-oss/bin/python -m lex pytest $T/init/test_1ap_reflex_cli.py $T/init/test_1aq_reflex_dashboards.py \
  $T/init/test_1b[t-z]_*.py $T/init/test_1c[a-u]_*.py \
  $T/api_layer/test_10ag_reflex_handoff_audience.py $T/api_layer/test_10d_reflex_search_exclusion.py \
  $T/permissions/test_4n_reflex_permissions.py -p no:cacheprovider -q
rm -rf assets
```

Expected: 159 passed, 0 failed (137 before this plan + 22), 194 subtests passed. Put the real numbers into the session fragment's `suite_tally`, then run step 3 again. A failure here is a finding: report it with its output and stop.

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/test-plan
git commit -m "test(plan): lex.ui pages step 1, batches 1cn-1cu in the test plan"
```

End the message with the co-author line for your own model. Do not push.

---

### Task 10: Port two real pages (the controller does this; it is not delegated)

Customer code and names stay on this machine: the ports live in the session's scratchpad under neutral names and are never committed.

- [ ] **Step 1: Choose the pages**

From the reviewed customer project, one detail page (one thing, its facts, its parts, actions on it) and its search list. Read their view code and the states they use.

- [ ] **Step 2: Port them**

Write each as a `@ui.page` function returning `ui.record` / `ui.list`, with a `ui.State` beside it and stand-ins for the project's logic functions (as in the handler layer's proof). Use only names from `lex.ui`; where the vocabulary does not do, use `ui.custom_block(..., reason="...")` and write the reason down. Drive them with `ui.testing.Harness` from this worktree: `RUN <scratchpad test file> -p no:django`.

- [ ] **Step 3: Record, before and after, for each page**

Lines of view code; `class_name=` and `style=` arguments; lines of the project's own stylesheet the page needed; `rx.cond`, `rx.fragment` and `rfds.t` calls; and every `ui.custom_block`, with its reason.

- [ ] **Step 4: Write down what the ports decided**

Three questions this step leaves open, to be answered from the ports before step 2's plan is written:

1. **Rows held in a state.** How the search list's table gets its rows (a model through the API, or a list the page's load fills), whether it needs row links and row actions, and whether it must update when a handler changes the rows.
2. **Filters.** What the list filters on, and whether the filter values belong in the page's address.
3. **What did not fit.** Every `ui.custom_block`: is it a missing component, a missing slot, or a page that is an application of its own?

Add a "Proof, step 1" section to the review document with the before/after numbers and these answers, under neutral names.

---

## Self-review notes

- **Spec coverage (step 1).** 6.2 `ui.page`, `ui.dashboard` → Task 7. 6.3 record, list → Task 6 (filters deferred, see "Not in this step"). 6.4 states → Tasks 1, 5, 6. 6.5 text, fact, status, message → Task 2; link, button → Task 3; table, column → Task 4; section → Task 5. 6.6 visible → Task 2; each, switch, custom_block → Task 5. 6.7 links by page → Task 3 (`ui.goto(page)` is step 2). 6.8 the sentence as the message → Task 2 (`lex ui messages` is step 4). 6.9 build-time errors → Tasks 2, 3, 5, 6, 7 (the check rules are step 4). 6.10 tests → every task. Section 7's ports → Task 10.
- **Names used across tasks.** `parts.PageError/Build/Part/say/shown/slug/called/take`; `vocab.TONES/KINDS/shape/text/fact/status/message/link/button/menu_entry/custom_block/each/switch/CONTENT/build_all` and the classes `Text/Fact/Status/Message/Link/Button/CustomBlock/Each/Switch`; `links.PageInfo/info_of/page_info/href/row_href/as_text`; `tables.table/column/Table/Column`; `kinds.states/section/Section/Kind/record/Record/list_/List`; `declare.page/dashboard/finalize/_DECLARED/Declared`; `runs.loaded`; `State.ui_loaded`.
- **Changed while planning, to be written into the spec with this plan:** the action placement rule (fact 6); pages registered once the module is loaded, not when built (fact 5); tables over state rows and filters move to step 2; the kinds available in step 1.
