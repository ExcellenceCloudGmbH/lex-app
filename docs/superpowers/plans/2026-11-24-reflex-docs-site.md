# Reflex docs site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A docs site for lex-app's Reflex dashboards, itself a Reflex app in `docs_site/`, where Markdown pages carry live `lex.ui` demos that run against an in-memory sandbox.

**Architecture:** `reflex-docgen` parses each Markdown page into blocks; our transformer turns the blocks into RFDS components in lex-app's look and runs `python exec`/`demo` fences in a module per page. A `lex.ui` session resolver gives every event a demo user, the tab's role and a `SandboxApi` over a per-tab copy of sample data. `nav.py` is the one source for the sidebar, landing cards and prev/next; backend routes serve each page as Markdown plus `llms.txt`.

**Tech Stack:** Python 3.12, Reflex 0.9.13, RFDS 1.0.0, `reflex-docgen` 0.9.5, `lex.ui` (this repo), pytest, Starlette routes (via `rfds.app_kwargs(extra_routes=)`).

**Spec:** `docs/superpowers/specs/2026-11-24-reflex-docs-site-design.md` (read its "Settled while planning" section first).

## Global Constraints

- Everything lives under `docs_site/`. **No file under `lex/` changes** in this plan (a change there needs paired cluster tests through the `lex-testing` skill; if a task finds it needs one, stop and ask).
- `docs_site/` is not packaged: `pyproject.toml`'s `[tool.setuptools.packages.find] include = ["lex*", "migrations_logic*"]` already leaves it out. Do not change that.
- Pins: `reflex==0.9.13` and `rfds==1.0.0` come from the root `requirements.txt`; `docs_site/requirements.txt` adds only `reflex-docgen==0.9.5` (0.10.0 needs `reflex>=0.10`, RFDS needs `reflex<0.10`).
- **Never `from __future__ import annotations`** in a module that defines Reflex states or components (Reflex silently drops event handlers annotated with a local type).
- States made at run time (from a demo's code) must live on a module registered in `sys.modules`, or Reflex cannot pickle them. Components built inside a factory need `__qualname__ = __name__`.
- Demos are components, never routes: page kinds through `ui.dashboard`, parts through `ui.embed`. `@ui.page` appears only in plain ` ```python ` blocks.
- `ui.table(Model)` is never a live demo (it calls lex-app's REST API with a real token). Live tables use `@ui.rows`.
- Sample models are plain classes with `_meta = SimpleNamespace(app_label="docs", model_name=...)`. Grant resource names are therefore `docs.Fund`, `docs.Investor`, `docs.Commitment`.
- Scopes are `read`, `create`, `edit`, `delete` (`lex/lex_app/reflex/ui/guard.py`).
- Docs text: conversational, what a thing does and how to use it; every claim checked against the source on this branch. No customer or project names, ever.
- **Workstation rules (`~/.claude/CLAUDE.md`):** never start `reflex run` and open it in the app's browser pane; never ask the user to. Any browser check runs headless Chrome on CPU inside `bwrap --dev-bind / / --tmpfs /dev/dri --ro-bind /dev/null /dev/kfd -- …`. This plan needs no browser: every check is a pytest compile/build test.
- Workflow files (`.github/workflows/*`) must be pushed over SSH (the `gh` token lacks the `workflow` scope).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## How to run the tests

From the worktree root (`/home/syscall/Documents/lex/.claude/worktrees/lex_reflex_docs`), once Task 1 has made the venv:

```bash
cd docs_site && ../.venv-docs/bin/python -m pytest -q
```

## File map

```
docs_site/
  requirements.txt              # reflex-docgen==0.9.5
  pytest.ini                    # pythonpath=., testpaths=tests
  rxconfig.py                   # rx.Config: app_name docs_site, lex-app's two RFDS plugins
  docs_site/
    __init__.py
    docs_site.py                # the app: rx.App, RFDS, sandbox, pages, routes, ui.install
    sandbox/
      __init__.py               # install(): the lex.ui resolver; ROLES
      models.py                 # Fund, Investor, Commitment (plain classes with _meta)
      data.py                   # seed(): the sample rows
      store.py                  # Store / TabData: a per-tab copy, idle copies dropped
      api.py                    # SandboxApi: self.api's methods over a TabData
      role.py                   # RoleState: the "Viewing as" switch
    content.py                  # Page records: read content/, front matter, headings, slugs
    nav.py                      # NAV: sections -> groups -> page slugs; checks
    render/
      __init__.py               # render_page(page) -> rx.Component; toc(page)
      blocks.py                 # LexDocTransformer: blocks/spans -> components
      live.py                   # PageModule: exec/demo/demo-only; to_component()
    layout.py                   # shell (header, sidebar, toc, prev/next), landing, add_pages(app)
    search.py                   # index + SearchState
    llms.py                     # Starlette routes: <slug>.md, llms.txt, llms-full.txt
    reference.py                # API reference pages from lex.ui.__all__
  content/
    index.md
    get-started/ pages/ state-and-actions/ data-and-permissions/ look-and-chrome/ ship-it/
  tests/
    conftest.py
    test_sandbox.py  test_content_nav.py  test_render.py  test_live.py
    test_layout_compile.py  test_llms.py  test_search.py  test_reference.py  test_pages_run.py
.github/workflows/docs_site.yml
```

---

### Task 1: Scaffold the app and prove it compiles

**Files:**
- Create: `docs_site/requirements.txt`, `docs_site/pytest.ini`, `docs_site/rxconfig.py`, `docs_site/docs_site/__init__.py`, `docs_site/docs_site/docs_site.py`, `docs_site/tests/conftest.py`, `docs_site/tests/test_layout_compile.py`
- Modify: `.gitignore` (add `.venv-docs/`, `docs_site/.web/`, `docs_site/.states/`)

**Interfaces:**
- Produces: `docs_site.docs_site.app` (the `rx.App`); fixture `compiled` in `conftest.py` returning `SimpleNamespace(app=..., pages=dict(app._pages))`. Later tasks add pages and assert on `compiled.pages`.

- [ ] **Step 1: Make the venv**

```bash
cd /home/syscall/Documents/lex/.claude/worktrees/lex_reflex_docs
printf 'reflex-docgen==0.9.5\n' > docs_site/requirements.txt   # after mkdir -p docs_site
uv venv .venv-docs --python 3.12
uv pip install --python .venv-docs -e . -r docs_site/requirements.txt pytest
.venv-docs/bin/python -c "import reflex_docgen, rfds, reflex; from lex import ui; print('ok')"
```
Expected: `ok`. If `pip install -e .` fails on `pycairo`, install `libcairo2-dev pkg-config` equivalents (`sudo pacman -S cairo pkgconf`) and ask the user before running sudo.

`docs_site/requirements.txt` gets a comment line above the pin:

```
# 0.10.0 needs reflex>=0.10; rfds 1.0.0 needs reflex<0.10 (lex-app pins reflex==0.9.13).
reflex-docgen==0.9.5
```

- [ ] **Step 2: Write the config files**

`docs_site/pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

`docs_site/rxconfig.py`:

```python
"""The docs site's Reflex config: lex-app's look (Radix with RFDS's theme, then RFDS)."""

import reflex as rx

from lex.lex_app.reflex.design import rfds_plugin, theme_plugin

config = rx.Config(
    app_name="docs_site",
    telemetry_enabled=False,
    show_built_with_reflex=False,
    default_color_mode="light",
    plugins=[theme_plugin(), rfds_plugin()],
)
```

`docs_site/docs_site/__init__.py`: one docstring line, `"""lex-app's Reflex docs, as a Reflex app."""`.

- [ ] **Step 3: Write the failing compile test**

`docs_site/tests/conftest.py`:

```python
"""Shared fixtures: the docs app, compiled once per test run, exactly as `reflex run` would."""

import contextlib
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SITE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def compiled():
    from reflex_base.registry import RegistrationContext

    with contextlib.chdir(SITE), RegistrationContext.ensure_context().fork():
        os.environ["CI"] = "true"                       # RFDS conformance: error, as in CI
        sys.modules.pop("docs_site.docs_site", None)
        app = importlib.import_module("docs_site.docs_site").app
        app._compile(dry_run=True)
        yield SimpleNamespace(app=app, pages=dict(app._pages))
```

`docs_site/tests/test_layout_compile.py`:

```python
"""The docs app compiles, with the routes the site promises."""


def test_the_app_compiles_with_a_home_page(compiled):
    assert "index" in compiled.pages
```

- [ ] **Step 4: Run it to see it fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_layout_compile.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'docs_site.docs_site'`.

- [ ] **Step 5: Write the app**

`docs_site/docs_site/docs_site.py`:

```python
"""The docs app: lex-app's look, no sign-in, every page from nav.py."""

import reflex as rx
import rfds
from rfds.platform import install_platform

from lex import ui
from lex.lex_app.reflex.design import lex_head_components, lex_security_headers


def _home() -> rx.Component:
    return rfds.page(rfds.page_header(title="lex-app dashboards", path="/"))


app = rx.App(**rfds.app_kwargs(html_lang="en", extra_head=lex_head_components()))
install_platform(app, headers=lex_security_headers())
rfds.install(app, tenant=None, auth=None)
import rfds.toast  # noqa: E402,F401 - defines states on first use; must exist before any event
app.add_page(_home, route="/", title="lex-app dashboards")
ui.install(app)
```

- [ ] **Step 6: Run the test**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_layout_compile.py -q`
Expected: PASS. If `rfds.install` refuses `auth=None` without a `PolicyMiddleware`, add `from rfds.auth import PolicyMiddleware` and `app.add_middleware(PolicyMiddleware(None, default="allow"))` before `rfds.install`, as `lex/lex_app/reflex/app.py:81` does, and re-run. If `ui.install` fails on `/ui-error`'s `LexAccessState.load`, record the error text in the task report and stop: that decides whether the docs app can call `ui.install` (needed for `@ui.job`'s clock).

- [ ] **Step 7: Commit**

```bash
git add .gitignore docs_site
git commit -m "feat(docs-site): a Reflex app for the dashboard docs, in lex-app's look

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Sample models, data and the per-tab store

**Files:**
- Create: `docs_site/docs_site/sandbox/__init__.py` (empty docstring for now), `sandbox/models.py`, `sandbox/data.py`, `sandbox/store.py`
- Test: `docs_site/tests/test_sandbox.py`

**Interfaces:**
- Produces: `models.Fund`, `models.Investor`, `models.Commitment`, `models.MODELS: dict[str, type]` (keyed by class name); each class has `_meta.app_label == "docs"`, `_meta.model_name`, `fields: tuple[str, ...]`, `search_field: str`.
- Produces: `data.seed() -> dict[str, list[dict]]` keyed by class name.
- Produces: `store.Store(clock=time.monotonic, idle_seconds=1800)`, `Store.tab(tab_id: str) -> TabData`, `Store.reset(tab_id)`, `TabData.rows: dict[str, list[dict]]`, `TabData.role: str` (default `"editor"`), module global `store.STORE`.

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_sandbox.py`:

```python
"""The sandbox: sample models, a copy of the data per tab, idle copies dropped."""

from docs_site.sandbox import data, models
from docs_site.sandbox.store import Store


def test_models_answer_what_lex_ui_reads():
    from lex.lex_app.reflex.auth import _resource_name
    from lex.lex_app.reflex.data import model_id

    assert _resource_name(models.Fund) == "docs.Fund"
    assert model_id(models.Commitment) == "commitment"
    assert set(models.MODELS) == {"Fund", "Investor", "Commitment"}


def test_seed_rows_have_ids_and_every_field():
    rows = data.seed()
    for name, model in models.MODELS.items():
        assert rows[name], name
        for row in rows[name]:
            assert set(row) == set(model.fields), (name, row)
        ids = [row["id"] for row in rows[name]]
        assert len(ids) == len(set(ids))


def test_each_tab_has_its_own_copy():
    store = Store()
    store.tab("a").rows["Fund"][0]["name"] = "Changed"
    assert store.tab("b").rows["Fund"][0]["name"] != "Changed"
    assert store.tab("a").rows["Fund"][0]["name"] == "Changed"


def test_reset_restores_the_seed():
    store = Store()
    store.tab("a").rows["Fund"].clear()
    store.reset("a")
    assert store.tab("a").rows["Fund"] == data.seed()["Fund"]


def test_an_idle_tab_is_dropped():
    now = [0.0]
    store = Store(clock=lambda: now[0], idle_seconds=10)
    store.tab("a").role = "viewer"
    now[0] = 11.0
    store.tab("b")                       # any access sweeps
    assert store.tab("a").role == "editor"
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_sandbox.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'docs_site.sandbox'`.

- [ ] **Step 3: Write the models**

`docs_site/docs_site/sandbox/models.py`:

```python
"""The docs' sample models.

Plain classes, not Django models: lex.ui reads only ``_meta.app_label`` and the class name (a
grant's resource, ``docs.Fund``) and ``_meta.model_name`` (an API id). ``fields`` and
``search_field`` are the sandbox's own: what a row holds, and what a search matches.
"""

from types import SimpleNamespace


class Fund:
    _meta = SimpleNamespace(app_label="docs", model_name="fund")
    fields = ("id", "name", "vintage", "status", "size", "currency")
    search_field = "name"


class Investor:
    _meta = SimpleNamespace(app_label="docs", model_name="investor")
    fields = ("id", "name", "country", "kind")
    search_field = "name"


class Commitment:
    _meta = SimpleNamespace(app_label="docs", model_name="commitment")
    fields = ("id", "fund", "investor", "amount", "signed_on")
    search_field = ""


MODELS: dict[str, type] = {model.__name__: model for model in (Fund, Investor, Commitment)}
```

- [ ] **Step 4: Write the seed**

`docs_site/docs_site/sandbox/data.py`:

```python
"""The sample rows every tab starts from. Invented names; amounts in the fund's currency."""

_FUNDS = [
    (1, "Northbridge Growth I", 2019, "closed", 250_000_000, "EUR"),
    (2, "Northbridge Growth II", 2022, "investing", 400_000_000, "EUR"),
    (3, "Harbour Infrastructure", 2020, "investing", 600_000_000, "USD"),
    (4, "Alder Credit Opportunities", 2021, "investing", 150_000_000, "EUR"),
    (5, "Cedar Venture III", 2023, "fundraising", 120_000_000, "USD"),
    (6, "Birch Secondaries", 2018, "harvesting", 300_000_000, "EUR"),
]
_INVESTORS = [
    (1, "Rhine Pension Fund", "DE", "pension"),
    (2, "Alpine Insurance", "CH", "insurer"),
    (3, "Lakeside Family Office", "AT", "family office"),
    (4, "Meridian Endowment", "US", "endowment"),
    (5, "Fjord Sovereign Fund", "NO", "sovereign"),
]
_COMMITMENTS = [
    (1, 1, 1, 40_000_000, "2019-03-14"),
    (2, 1, 2, 25_000_000, "2019-04-02"),
    (3, 2, 1, 60_000_000, "2022-01-20"),
    (4, 2, 3, 10_000_000, "2022-02-11"),
    (5, 3, 4, 75_000_000, "2020-06-30"),
    (6, 3, 5, 120_000_000, "2020-07-15"),
    (7, 4, 2, 20_000_000, "2021-09-01"),
    (8, 5, 3, 5_000_000, "2023-05-05"),
    (9, 6, 5, 50_000_000, "2018-11-23"),
    (10, 6, 4, 30_000_000, "2018-12-07"),
]


def seed() -> dict[str, list[dict]]:
    """A fresh copy of the sample rows, keyed by model name."""
    return {
        "Fund": [dict(zip(("id", "name", "vintage", "status", "size", "currency"), row)) for row in _FUNDS],
        "Investor": [dict(zip(("id", "name", "country", "kind"), row)) for row in _INVESTORS],
        "Commitment": [dict(zip(("id", "fund", "investor", "amount", "signed_on"), row)) for row in _COMMITMENTS],
    }
```

- [ ] **Step 5: Write the store**

`docs_site/docs_site/sandbox/store.py`:

```python
"""A copy of the sample data per browser tab, in this process's memory.

A reader's edits never reach another reader, a reload of the page (a new tab token) starts from
the seed, and a tab idle for ``idle_seconds`` is dropped. One backend process only: the copies
are not shared between processes.
"""

import threading
import time
from collections.abc import Callable

from docs_site.sandbox.data import seed


class TabData:
    def __init__(self, now: float):
        self.rows: dict[str, list[dict]] = seed()
        self.role: str = "editor"
        self.seen = now


class Store:
    def __init__(self, clock: Callable[[], float] = time.monotonic, idle_seconds: float = 1800):
        self._clock = clock
        self._idle = idle_seconds
        self._tabs: dict[str, TabData] = {}
        self._lock = threading.Lock()

    def tab(self, tab_id: str) -> TabData:
        with self._lock:
            now = self._clock()
            for key in [k for k, t in self._tabs.items() if now - t.seen > self._idle]:
                del self._tabs[key]
            data = self._tabs.get(tab_id)
            if data is None:
                data = self._tabs[tab_id] = TabData(now)
            data.seen = now
            return data

    def reset(self, tab_id: str) -> None:
        with self._lock:
            self._tabs.pop(tab_id, None)


STORE = Store()
```

- [ ] **Step 6: Run the tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_sandbox.py -q`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add docs_site/docs_site/sandbox docs_site/tests/test_sandbox.py
git commit -m "feat(docs-site): sample models and a copy of their data per tab

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `SandboxApi` — `self.api` over a tab's data

**Files:**
- Create: `docs_site/docs_site/sandbox/api.py`
- Test: `docs_site/tests/test_sandbox.py` (append)

**Interfaces:**
- Consumes: `TabData`, `models.MODELS`.
- Produces: `SandboxApi(tab: TabData)` with the methods and signatures of `lex/lex_app/reflex/ui/context.py:Api`:
  `list(model, *, filters=None, search=None, ordering=None, page=1, per_page=25, fields=None) -> Rows`,
  `get(model, pk) -> dict`, `create(model, data) -> dict`, `update(model, pk, data) -> dict`, `delete(model, pk) -> None`.
  Missing record → `LexApiError(404, ...)`; a model outside the sample → `LexApiError(404, ...)`.

- [ ] **Step 1: Write the failing tests** (append to `test_sandbox.py`)

```python
import pytest

from docs_site.sandbox.api import SandboxApi
from docs_site.sandbox.models import Commitment, Fund
from lex.lex_app.reflex.data import LexApiError


def _api():
    return SandboxApi(Store().tab("t"))


def test_list_filters_searches_orders_and_pages():
    api = _api()
    rows = api.list(Fund, filters={"status": "investing"}, ordering="-size", per_page=2)
    assert [r["name"] for r in rows] == ["Harbour Infrastructure", "Northbridge Growth II"]
    assert rows.count == 3
    assert [r["name"] for r in api.list(Fund, search="birch")] == ["Birch Secondaries"]
    assert [r["id"] for r in api.list(Commitment, filters={"amount__gte": 60_000_000})] == [3, 5, 6]
    assert [r["id"] for r in api.list(Fund, filters={"id__in": [1, 5]})] == [1, 5]
    assert set(api.list(Fund, fields=["id", "name"])[0]) == {"id", "name"}


def test_create_update_delete_change_only_this_tab():
    store = Store()
    api = SandboxApi(store.tab("t"))
    made = api.create(Fund, {"name": "New", "vintage": 2024, "status": "fundraising",
                             "size": 1, "currency": "EUR"})
    assert made["id"] == 7
    assert api.update(Fund, 7, {"status": "investing"})["status"] == "investing"
    api.delete(Fund, 7)
    with pytest.raises(LexApiError) as missing:
        api.get(Fund, 7)
    assert missing.value.status == 404
    assert len(SandboxApi(store.tab("other")).list(Fund)) == 6


def test_an_unknown_model_is_refused():
    class Elsewhere:
        pass

    with pytest.raises(LexApiError):
        _api().list(Elsewhere)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_sandbox.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'docs_site.sandbox.api'`.

- [ ] **Step 3: Write `SandboxApi`**

`docs_site/docs_site/sandbox/api.py`:

```python
"""``self.api`` in the docs: lex-app's API methods over a tab's copy of the sample data.

Filters take the forms a handler writes (``field``, ``field__icontains``, ``__gte``, ``__lte``,
``__in``); ``ordering`` is a field, ``-`` first for descending, comma-separated for more.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from docs_site.sandbox.models import MODELS
from docs_site.sandbox.store import TabData
from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui.context import Rows


def _matches(row: Mapping[str, Any], key: str, wanted: Any) -> bool:
    field, _, op = key.partition("__")
    value = row.get(field)
    if op == "":
        return value == wanted
    if op == "icontains":
        return str(wanted).lower() in str(value or "").lower()
    if op == "gte":
        return value is not None and value >= wanted
    if op == "lte":
        return value is not None and value <= wanted
    if op == "in":
        return value in list(wanted)
    raise LexApiError(400, f"the sandbox has no filter {key!r}")


class SandboxApi:
    def __init__(self, tab: TabData):
        self._tab = tab

    def _rows(self, model: Any) -> tuple[type, list[dict]]:
        name = getattr(model, "__name__", str(model))
        if MODELS.get(name) is not model:
            raise LexApiError(404, f"the docs sandbox has no model {name}")
        return model, self._tab.rows[name]

    def _find(self, model: Any, pk: Any) -> dict:
        _, rows = self._rows(model)
        for row in rows:
            if str(row["id"]) == str(pk):
                return row
        raise LexApiError(404, f"no {model.__name__} {pk}")

    def list(self, model: Any, *, filters: Mapping[str, Any] | None = None, search: str | None = None,
             ordering: str | None = None, page: int = 1, per_page: int = 25,
             fields: Sequence[str] | None = None) -> Rows:
        model, rows = self._rows(model)
        found = [r for r in rows if all(_matches(r, k, v) for k, v in (filters or {}).items())]
        if search:
            if not model.search_field:
                raise ValueError(f"{model.__name__} has no text field to search")
            found = [r for r in found if _matches(r, f"{model.search_field}__icontains", search)]
        for key in reversed([k for k in (ordering or "").split(",") if k]):
            name = key.lstrip("-")
            found.sort(key=lambda r: (r.get(name) is None, r.get(name)), reverse=key.startswith("-"))
        start = (max(page, 1) - 1) * per_page
        out = Rows({k: v for k, v in r.items() if fields is None or k in fields}
                   for r in found[start:start + per_page])
        out.count = len(found)
        return out

    def get(self, model: Any, pk: Any) -> dict:
        return dict(self._find(model, pk))

    def create(self, model: Any, data: Mapping[str, Any]) -> dict:
        model, rows = self._rows(model)
        row = {name: None for name in model.fields}
        row.update({k: v for k, v in data.items() if k in model.fields and k != "id"})
        row["id"] = max((r["id"] for r in rows), default=0) + 1
        rows.append(row)
        return dict(row)

    def update(self, model: Any, pk: Any, data: Mapping[str, Any]) -> dict:
        row = self._find(model, pk)
        row.update({k: v for k, v in data.items() if k in model.fields and k != "id"})
        return dict(row)

    def delete(self, model: Any, pk: Any) -> None:
        _, rows = self._rows(model)
        rows.remove(self._find(model, pk))
```

- [ ] **Step 4: Run the tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_sandbox.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add docs_site/docs_site/sandbox/api.py docs_site/tests/test_sandbox.py
git commit -m "feat(docs-site): self.api in the docs, over a tab's sample data

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The resolver and the "Viewing as" switch

**Files:**
- Modify: `docs_site/docs_site/sandbox/__init__.py`
- Create: `docs_site/docs_site/sandbox/role.py`
- Modify: `docs_site/docs_site/docs_site.py` (call `sandbox.install()` before `rx.App(...)`)
- Test: `docs_site/tests/test_sandbox.py` (append)

**Interfaces:**
- Consumes: `STORE`, `SandboxApi`, `lex.lex_app.reflex.ui.context.{Session, User, Params, set_resolver, _params_of}`, `workcopy.raw`.
- Produces: `sandbox.ROLES: dict[str, list[dict]]` (`"editor"`, `"viewer"`), `sandbox.resolve(state) -> Session` (async), `sandbox.install() -> None`, `role.RoleState` with var `role: str` and event `set_role(value: str)`, and `role.switch() -> rx.Component` (a segmented control for the demo card).

- [ ] **Step 1: Write the failing tests** (append)

```python
import asyncio
from types import SimpleNamespace

from docs_site import sandbox
from docs_site.sandbox.store import STORE


def _fake_state(tab: str):
    router = SimpleNamespace(session=SimpleNamespace(client_token=tab), url="/pages/record", page=SimpleNamespace(params={}))
    return SimpleNamespace(router=router)


def test_the_resolver_gives_the_tab_its_data_and_role():
    STORE.reset("tab-r")
    STORE.tab("tab-r").role = "viewer"
    session = asyncio.run(sandbox.resolve(_fake_state("tab-r")))
    grants = asyncio.run(session.permissions())
    assert session.tab == "tab-r"
    assert session.user.username == "demo"
    assert {"rsname": "docs.Fund", "scopes": ["read"]} in grants
    assert all(g["scopes"] == ["read"] for g in grants)
    assert session.api.list(Fund).count == 6


def test_a_lex_ui_handler_reads_the_sandbox():
    from lex import ui
    from lex.lex_app.reflex.ui import testing

    class OpenFunds(ui.State):
        names: list[str] = []

        @ui.load
        def open(self):
            self.names = [r["name"] for r in self.api.list(Fund, filters={"status": "investing"})]

    result = testing.run(OpenFunds, "open", api=SandboxApi(Store().tab("h")))
    assert result.fields["names"] == ["Northbridge Growth II", "Harbour Infrastructure",
                                      "Alder Credit Opportunities"]
```

Before writing the second test, read `lex/lex_app/reflex/ui/testing.py:296-330` for `run()`'s exact return value and adjust `result.fields[...]` to it (it records fields after the handler; the attribute name is in that function).

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_sandbox.py -q`
Expected: FAIL, `AttributeError: module 'docs_site.sandbox' has no attribute 'resolve'`.

- [ ] **Step 3: Write the resolver**

`docs_site/docs_site/sandbox/__init__.py`:

```python
"""The docs' sandbox: who is asking (a demo user), what they may do (the tab's role) and what
``self.api`` answers (a copy of the sample data for the tab). Installed in place of lex-app's
sign-in, so no demo needs Keycloak, a lex-app instance or a database.
"""

from typing import Any

from docs_site.sandbox.api import SandboxApi
from docs_site.sandbox.models import MODELS
from docs_site.sandbox.store import STORE

_ALL = ["read", "create", "edit", "delete"]
ROLES: dict[str, list[dict]] = {
    "editor": [{"rsname": f"docs.{name}", "scopes": list(_ALL)} for name in MODELS],
    "viewer": [{"rsname": f"docs.{name}", "scopes": ["read"]} for name in MODELS],
}


async def resolve(state: Any):
    from lex.lex_app.reflex.ui import context, workcopy

    target = workcopy.raw(state)
    try:
        tab = str(target.router.session.client_token or "")
    except Exception:  # noqa: BLE001 - a state without router data comes from no tab
        tab = ""
    data = STORE.tab(tab)
    grants = ROLES[data.role]

    async def token() -> str:
        return "docs-sandbox"

    async def permissions() -> list:
        return [dict(g, scopes=list(g["scopes"])) for g in grants]

    user = context.User(id="demo", name="Demo Consultant", username="demo", email="demo@example.test")
    return context.Session(user=user, params=context._params_of(target), token=token,
                           permissions=permissions, api=SandboxApi(data), tab=tab)


def install() -> None:
    from lex.lex_app.reflex.ui import context

    context.set_resolver(resolve)
```

If `context.User(...)` takes different fields, copy the call from `lex/lex_app/reflex/ui/testing.py:142` (it is the same constructor).

- [ ] **Step 4: Write the switch**

`docs_site/docs_site/sandbox/role.py` (no `from __future__ import annotations`):

```python
"""The "Viewing as" switch on a demo card: the tab's role, Editor or Viewer."""

import reflex as rx
import rfds

from docs_site.sandbox.store import STORE


class RoleState(rx.State):
    role: str = "editor"

    @rx.event
    def set_role(self, value: str):
        if value not in ("editor", "viewer"):
            return
        self.role = value
        STORE.tab(self.router.session.client_token).role = value


def switch() -> rx.Component:
    return rfds.segmented_control(
        options=[{"value": "editor", "label": "Editor"}, {"value": "viewer", "label": "Viewer"}],
        value=RoleState.role,
        on_change=RoleState.set_role,
        aria_label="Viewing as",
    )
```

If `rfds.segmented_control` has another name or signature in RFDS 1.0.0, find it with `../.venv-docs/bin/python -c "import rfds; print([n for n in dir(rfds) if 'segment' in n or 'toggle' in n])"` and use what is there; fall back to `rx.segmented_control.root` with two `rx.segmented_control.item`s.

- [ ] **Step 5: Install it in the app**

In `docs_site/docs_site/docs_site.py`, add `from docs_site import sandbox` with the imports, and `sandbox.install()` on the line before `app = rx.App(...)`.

- [ ] **Step 6: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass (10 in `test_sandbox.py`, 1 compile).

- [ ] **Step 7: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): demos run as a demo user, Editor or Viewer, on the tab's data

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Content pages and the navigation

**Files:**
- Create: `docs_site/docs_site/content.py`, `docs_site/docs_site/nav.py`, `docs_site/content/index.md`, one stub per page listed in `NAV` (Step 3)
- Test: `docs_site/tests/test_content_nav.py`

**Interfaces:**
- Produces: `content.CONTENT: Path`; `content.Page` (frozen dataclass: `slug: str` like `"pages/record"`, `title: str`, `description: str`, `source: str` (the Markdown, front matter included), `document: reflex_docgen.markdown.Document`, `headings: list[tuple[int, str, str]]` (level, text, anchor id)); `content.load(slug) -> Page`; `content.all_slugs() -> list[str]` (every `.md` under `content/`, without `.md`; `index` for the home page); `content.anchor(text) -> str`.
- Produces: `nav.Group(title: str, pages: tuple[str, ...])`, `nav.Section(slug: str, title: str, blurb: str, groups: tuple[Group, ...])`, `nav.NAV: tuple[Section, ...]`, `nav.ordered() -> list[str]` (every page slug in reading order), `nav.section_of(slug) -> Section`, `nav.neighbours(slug) -> tuple[str | None, str | None]`, `nav.problems() -> list[str]` (empty when nav and content agree).

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_content_nav.py`:

```python
"""Pages are read from content/; nav.py lists every page once and nothing else."""

from docs_site import content, nav


def test_nav_and_content_agree():
    assert nav.problems() == []


def test_a_page_has_a_title_and_anchored_headings():
    page = content.load("pages/record")
    assert page.title == "ui.record"
    assert all(anchor == content.anchor(text) for _, text, anchor in page.headings)


def test_anchor_is_stable_and_url_safe():
    assert content.anchor("State & actions: `@ui.job`") == "state-actions-ui-job"


def test_neighbours_follow_reading_order():
    order = nav.ordered()
    assert nav.neighbours(order[0])[0] is None
    assert nav.neighbours(order[1]) == (order[0], order[2])
    assert nav.neighbours(order[-1])[1] is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_content_nav.py -q`
Expected: FAIL, `ImportError: cannot import name 'content'`.

- [ ] **Step 3: Write `nav.py`**

```python
"""The site's navigation: sections, their groups, and the pages in reading order.

The landing cards, the sidebar, the breadcrumbs and previous/next all come from here. A page in
content/ that is not listed here, or a listed page with no file, fails the build (problems()).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    title: str
    pages: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    blurb: str
    groups: tuple[Group, ...]


NAV: tuple[Section, ...] = (
    Section("get-started", "Get started", "Your first dashboard on lex-app, from nothing to a page that works.", (
        Group("Get started", ("get-started/first-dashboard", "get-started/lex-reflex-add",
                              "get-started/running-locally")),
    )),
    Section("pages", "Pages", "Where a page lives, and the page kinds that decide how it looks.", (
        Group("Declaring pages", ("pages/ui-page",)),
        Group("Page kinds", ("pages/record", "pages/list")),
        Group("Parts", ("pages/tables",)),
    )),
    Section("state-and-actions", "State & actions", "Plain Python handlers; lex-app does the plumbing.", (
        Group("State", ("state-and-actions/state",)),
        Group("Handlers", ("state-and-actions/action", "state-and-actions/job", "state-and-actions/load")),
    )),
    Section("data-and-permissions", "Data & permissions", "Reading and writing records as the signed-in user.", (
        Group("Data", ("data-and-permissions/api",)),
        Group("Permissions", ("data-and-permissions/can", "data-and-permissions/grants")),
    )),
    Section("look-and-chrome", "Look & chrome", "lex-app's design, your own frame, and dashboards inside lex-app.", (
        Group("Look & chrome", ("look-and-chrome/look", "look-and-chrome/chrome", "look-and-chrome/embedding")),
    )),
    Section("ship-it", "Ship it", "Sign-in, checks and deployment.", (
        Group("Ship it", ("ship-it/sign-in", "ship-it/deploying", "ship-it/check")),
    )),
)


def ordered() -> list[str]:
    return [page for section in NAV for group in section.groups for page in group.pages]


def section_of(slug: str) -> Section:
    for section in NAV:
        if any(slug in group.pages for group in section.groups):
            return section
    raise KeyError(slug)


def neighbours(slug: str) -> tuple[str | None, str | None]:
    order = ordered()
    i = order.index(slug)
    return (order[i - 1] if i > 0 else None, order[i + 1] if i + 1 < len(order) else None)


def problems() -> list[str]:
    from docs_site import content

    listed = ordered()
    found = {slug for slug in content.all_slugs() if slug != "index" and not slug.startswith("reference/")}
    out = [f"listed twice: {s}" for s in sorted({s for s in listed if listed.count(s) > 1})]
    out += [f"listed in nav.py, no file: content/{s}.md" for s in listed if s not in found]
    out += [f"in content/, not in nav.py: {s}" for s in sorted(found - set(listed))]
    out += [f"{s} is not in its section's folder" for sec in NAV for g in sec.groups for s in g.pages
            if not s.startswith(sec.slug + "/")]
    return out
```

(The API reference pages are generated, Task 10, and are not files.)

- [ ] **Step 4: Write `content.py`**

```python
"""The Markdown pages under content/, parsed once by reflex-docgen."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from reflex_docgen.markdown import Document, HeadingBlock, parse_document

CONTENT = Path(__file__).resolve().parents[1] / "content"


def anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def plain(spans) -> str:
    """A heading's or paragraph's text, without its markup."""
    out = []
    for span in spans:
        if hasattr(span, "text"):
            out.append(span.text)
        elif hasattr(span, "code"):
            out.append(span.code)
        elif hasattr(span, "children"):
            out.append(plain(span.children))
    return "".join(out)


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    description: str
    source: str
    document: Document
    headings: list[tuple[int, str, str]]


def all_slugs() -> list[str]:
    return sorted(str(p.relative_to(CONTENT).with_suffix("")) for p in CONTENT.rglob("*.md"))


@cache
def load(slug: str) -> Page:
    source = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
    document = parse_document(source)
    meta = document.metadata or {}
    title = str(meta.get("title") or slug.rsplit("/", 1)[-1].replace("-", " ").capitalize())
    headings = [(b.level, plain(b.children), anchor(plain(b.children)))
                for b in document.blocks if isinstance(b, HeadingBlock) and b.level in (2, 3)]
    return Page(slug=slug, title=title, description=str(meta.get("description") or ""),
                source=source, document=document, headings=headings)
```

- [ ] **Step 5: Write `content/index.md` and one stub per page**

`content/index.md`:

```markdown
---
title: lex-app dashboards
description: Build dashboards on lex-app in plain Python, with live examples on every page.
---

Every example on this site runs. Click its buttons: it works on sample data that belongs to
your browser tab, and a reload starts it fresh.
```

For every slug in `nav.ordered()`, create `content/<slug>.md` with this stub, filling in a title and description that match the page (titles: `Your first dashboard`, `lex reflex add`, `Running it locally`, `@ui.page`, `ui.record`, `ui.list`, `Tables`, `ui.State`, `@ui.action`, `@ui.job`, `@ui.load`, `self.api`, `self.can`, `Grants from Keycloak`, `lex-app's look`, `Your own chrome`, `Inside lex-app`, `Sign-in`, `Deploying`, `lex reflex check`):

```markdown
---
title: ui.record
description: One thing on a page - its title, facts, sections and actions.
---

This page is being written. Until then, the code is the reference:
`lex/lex_app/reflex/ui/pages/kinds.py`.
```

Point each stub at the module that holds the feature (find it with `grep -rn "def <name>" lex/lex_app/reflex`).

- [ ] **Step 6: Run the tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_content_nav.py -q`
Expected: 4 passed. If `parse_document` keeps front matter only for known keys, read `title` from `document.frontmatter.title` instead (see `reflex_docgen/markdown/_types.py:FrontMatter`).

- [ ] **Step 7: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): pages from content/, navigation from nav.py, checked both ways

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Rendering Markdown in lex-app's look

**Files:**
- Create: `docs_site/docs_site/render/__init__.py`, `docs_site/docs_site/render/blocks.py`
- Test: `docs_site/tests/test_render.py`

**Interfaces:**
- Consumes: `content.Page`, `content.anchor`, `content.plain`.
- Produces: `blocks.LexDocTransformer(page: Page)` (a `DocumentTransformer[rx.Component]`); its `code_block(block)` calls `self.live_block(block)` for `python` fences with flags (Task 7 fills `live_block` in; here it raises `NotImplementedError`). `render.render_page(page) -> rx.Component`, `render.toc(page) -> rx.Component`.

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_render.py`:

```python
"""Markdown becomes lex-look components: headings with anchors, code, callouts, tabs, tables."""

from reflex_docgen.markdown import parse_document

from docs_site.content import Page
from docs_site.render import render_page, toc


def _page(source: str) -> Page:
    doc = parse_document(source)
    return Page(slug="t/x", title="X", description="", source=source, document=doc, headings=[(2, "Setup steps", "setup-steps")])


def test_headings_text_code_and_links():
    built = str(render_page(_page("## Setup steps\n\nRun **this** and see [the record](/pages/record).\n\n"
                                  "```python\nprint(1)\n```\n")))
    assert 'id="setup-steps"' in built or '"setup-steps"' in built
    assert "Setup steps" in built and "this" in built and "/pages/record" in built
    assert "print(1)" in built


def test_alert_and_tabs():
    built = str(render_page(_page("```md alert warning\nCareful.\n```\n\n"
                                  "```md tabs\n## One\nFirst.\n## Two\nSecond.\n```\n")))
    assert "Careful." in built
    assert "One" in built and "Second." in built


def test_a_table():
    built = str(render_page(_page("| a | b |\n|---|---|\n| 1 | 2 |\n")))
    assert "rfds-table" in built or "<table" in built.lower() or "table" in built


def test_toc_lists_the_headings():
    assert "#setup-steps" in str(toc(_page("## Setup steps\n")))
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_render.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'docs_site.render'`.

- [ ] **Step 3: Write the transformer**

`docs_site/docs_site/render/blocks.py` (no `from __future__ import annotations`):

```python
"""reflex-docgen's blocks as components in lex-app's look (RFDS components and tokens)."""

import reflex as rx
import rfds
from reflex_docgen.markdown import (
    CodeBlock, DirectiveBlock, Document, HeadingBlock, ListBlock, QuoteBlock, TableBlock, TextBlock,
    ThematicBreakBlock,
)
from reflex_docgen.markdown.transformer import DocumentTransformer

from docs_site.content import Page, anchor, plain

_HEADING_SIZE = {1: "7", 2: "6", 3: "5", 4: "4"}
_TONE = {"info": "info", "success": "success", "warning": "warning", "danger": "danger", "error": "danger"}


class LexDocTransformer(DocumentTransformer[rx.Component]):
    def __init__(self, page: Page):
        self.page = page

    # -- the page ---------------------------------------------------------------
    def transform(self, document: Document) -> rx.Component:
        return rx.vstack(*self.transform_blocks(document.blocks), spacing="4", align="stretch",
                         class_name="docs-prose")

    # -- blocks -----------------------------------------------------------------
    def frontmatter(self, block) -> rx.Component:
        return rx.fragment()

    def heading(self, block: HeadingBlock) -> rx.Component:
        text = plain(block.children)
        return rx.heading(*self.transform_spans(block.children), as_=f"h{block.level}",
                          size=_HEADING_SIZE.get(block.level, "3"), id=anchor(text))

    def text_block(self, block: TextBlock) -> rx.Component:
        return rx.text(*self.transform_spans(block.children), as_="p")

    def list_block(self, block: ListBlock) -> rx.Component:
        items = [rx.list.item(*self.transform_blocks(item.children)) for item in block.items]
        return rx.list.ordered(*items) if block.ordered else rx.list.unordered(*items)

    def transform_list_item(self, item) -> rx.Component:
        return rx.list.item(*self.transform_blocks(item.children))

    def quote(self, block: QuoteBlock) -> rx.Component:
        return rx.blockquote(*self.transform_blocks(block.children))

    def table(self, block: TableBlock) -> rx.Component:
        head = rx.table.row(*[rx.table.column_header_cell(*self.transform_spans(c.children))
                              for c in block.header.cells])
        body = [rx.table.row(*[rx.table.cell(*self.transform_spans(c.children)) for c in row.cells])
                for row in block.rows]
        return rx.table.root(rx.table.header(head), rx.table.body(*body), variant="surface")

    def thematic_break(self, block: ThematicBreakBlock) -> rx.Component:
        return rx.separator()

    def code_block(self, block: CodeBlock) -> rx.Component:
        if block.language == "python" and block.flags:
            return self.live_block(block)
        return rx.code_block(block.content.rstrip("\n"), language=block.language or "text",
                             show_line_numbers=False, can_copy=True)

    def live_block(self, block: CodeBlock) -> rx.Component:
        raise NotImplementedError("python demo/exec fences come with render/live.py")

    def directive(self, block: DirectiveBlock) -> rx.Component:
        if block.name == "alert":
            tone = _TONE.get(block.args[0] if block.args else "info", "info")
            return rfds.callout(rx.vstack(*self.transform_blocks(block.children), spacing="2"), tone=tone)
        if block.name == "tabs":
            return self._tabs(block)
        raise ValueError(f"{self.page.slug}: no ```md {block.name}``` directive on this site "
                         "(there are: alert, tabs)")

    def _tabs(self, block: DirectiveBlock) -> rx.Component:
        panes: list[tuple[str, list]] = []
        for child in block.children:
            if isinstance(child, HeadingBlock) and child.level == 2:
                panes.append((plain(child.children), []))
            elif panes:
                panes[-1][1].append(child)
        if not panes:
            raise ValueError(f"{self.page.slug}: a ```md tabs``` block needs a '## Title' per tab")
        return rx.tabs.root(
            rx.tabs.list(*[rx.tabs.trigger(title, value=str(i)) for i, (title, _) in enumerate(panes)]),
            *[rx.tabs.content(*self.transform_blocks(tuple(body)), value=str(i)) for i, (_, body) in enumerate(panes)],
            default_value="0",
        )

    # -- spans ------------------------------------------------------------------
    def text_span(self, span) -> rx.Component:
        return rx.fragment(span.text)

    def bold(self, span) -> rx.Component:
        return rx.text(*self.transform_spans(span.children), as_="strong", weight="bold")

    def italic(self, span) -> rx.Component:
        return rx.text(*self.transform_spans(span.children), as_="em")

    def strikethrough(self, span) -> rx.Component:
        return rx.text(*self.transform_spans(span.children), as_="s")

    def code_span(self, span) -> rx.Component:
        return rx.code(span.code)

    def link(self, span) -> rx.Component:
        return rx.link(*self.transform_spans(span.children), href=span.target,
                       is_external=span.target.startswith("http"))

    def image(self, span) -> rx.Component:
        return rx.image(src=span.src, alt=plain(span.children))

    def line_break(self, span) -> rx.Component:
        return rx.fragment(" ") if span.soft else rx.el.br()
```

`rfds.callout(..., tone=)`: confirm the name and keyword with `../.venv-docs/bin/python -c "import rfds, inspect; print(inspect.signature(rfds.callout))"`; if RFDS names it differently, use `rx.callout.root(..., color_scheme=...)` with `info→blue, success→green, warning→amber, danger→red`.

`docs_site/docs_site/render/__init__.py`:

```python
"""A content page as components, and its "On this page" list."""

import reflex as rx

from docs_site.content import Page
from docs_site.render.blocks import LexDocTransformer


def render_page(page: Page) -> rx.Component:
    return LexDocTransformer(page).transform(page.document)


def toc(page: Page) -> rx.Component:
    if not page.headings:
        return rx.fragment()
    return rx.vstack(
        rx.text("On this page", weight="bold", size="2"),
        *[rx.link(text, href=f"#{anchor_id}", size="2", padding_left="12px" if level == 3 else "0")
          for level, text, anchor_id in page.headings],
        spacing="1", align="start", class_name="docs-toc",
    )
```

- [ ] **Step 4: Run the tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_render.py -q`
Expected: 4 passed. If an assertion's string differs because RFDS/Radix render a different class name, print `str(component)` for that case and assert on the text it carries instead of the class; keep the assertion about the *content* (heading text, anchor, link target, code), never weaken it to `True`.

- [ ] **Step 5: Commit**

```bash
git add docs_site/docs_site/render docs_site/tests/test_render.py
git commit -m "feat(docs-site): Markdown in lex-app's look: anchors, code, callouts, tabs, tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Live fences — `exec`, `demo`, `demo-only`

**Files:**
- Create: `docs_site/docs_site/render/live.py`
- Modify: `docs_site/docs_site/render/blocks.py` (`live_block`)
- Test: `docs_site/tests/test_live.py`

**Interfaces:**
- Consumes: `LexDocTransformer`, `role.switch()`, `lex.lex_app.reflex.ui.pages.kinds.Kind`, `lex.lex_app.reflex.ui.pages.parts.Part`, `ui.dashboard`, `ui.embed`.
- Produces: `live.PageModule(slug: str)` with `.run(code: str) -> None` (exec) and `.value(code: str) -> Any` (exec all but the last expression, return the last expression's value); `live.to_component(value, name: str) -> rx.Component`; `live.demo_card(code: str | None, component: rx.Component) -> rx.Component`.

Rules this task implements:
- One module per page, named `docs_site.pages.<slug with / and - as _>`, registered in `sys.modules`, so states a page defines are unique to it and picklable.
- `exec` blocks run in order, in that module; `demo` and `demo-only` blocks also run there, and their **last statement must be an expression** whose value is the demo.
- The value may be an `rx.Component`, a lex.ui page kind (`Kind` → `ui.dashboard` over a function named after the page), or a lex.ui part (`Part` → `ui.embed(part, name=...)`). Anything else is an error naming the page and the block.

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_live.py`:

```python
"""Live fences: setup code, demos of components, page kinds and parts, one module per page."""

import sys

import pytest
import reflex as rx

from docs_site.render.live import PageModule, to_component

_STATE = '''
from lex import ui
from docs_site.sandbox.models import Fund

class Funds(ui.State):
    @ui.rows
    def rows(self):
        return self.api.list(Fund, filters={"status": "investing"})
'''


def test_states_belong_to_their_page_module():
    a, b = PageModule("t/a"), PageModule("t/b")
    a.run(_STATE)
    b.run(_STATE)
    assert a.namespace["Funds"] is not b.namespace["Funds"]
    assert sys.modules["docs_site.pages.t_a"].Funds is a.namespace["Funds"]


def test_a_demo_value_is_its_last_expression():
    page = PageModule("t/c")
    assert page.value("x = 2\nx * 21") == 42
    with pytest.raises(ValueError, match="last line"):
        page.value("y = 1")


def test_parts_and_kinds_become_components():
    page = PageModule("t/d")
    page.run(_STATE)
    table = page.value('ui.table(Funds.rows, columns=["name", "vintage"])')
    assert "rfds-data-table" in str(to_component(table, "funds"))
    record = page.value('ui.record(title="Northbridge Growth II", facts=[ui.fact("Vintage", "2022")])')
    assert "Northbridge Growth II" in str(to_component(record, "funds"))
    assert isinstance(to_component(rx.text("hi"), "x"), rx.Component)
    with pytest.raises(TypeError, match="t/d"):
        to_component(object(), "t/d")


def test_a_demo_fence_shows_code_and_result():
    from reflex_docgen.markdown import parse_document

    from docs_site.content import Page
    from docs_site.render import render_page

    src = "```python exec\n" + _STATE + "```\n\n```python demo\nui.table(Funds.rows, columns=[\"name\"])\n```\n"
    page = Page(slug="t/e", title="E", description="", source=src, document=parse_document(src), headings=[])
    built = str(render_page(page))
    assert "ui.table(Funds.rows" in built and "rfds-data-table" in built
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_live.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'docs_site.render.live'`.

- [ ] **Step 3: Write `live.py`** (no `from __future__ import annotations`)

```python
"""Running a page's python fences: setup (exec) and demos (demo, demo-only), in a module per page."""

import ast
import sys
import types
from typing import Any

import reflex as rx

from lex import ui
from lex.lex_app.reflex.ui.pages.kinds import Kind
from lex.lex_app.reflex.ui.pages.parts import Part


class PageModule:
    def __init__(self, slug: str):
        self.slug = slug
        name = "docs_site.pages." + slug.replace("/", "_").replace("-", "_")
        module = types.ModuleType(name)
        module.__dict__["__builtins__"] = __builtins__
        sys.modules[name] = module
        self.module = module
        self.namespace = module.__dict__

    def run(self, code: str) -> None:
        exec(compile(code, f"<{self.slug}>", "exec"), self.namespace)

    def value(self, code: str) -> Any:
        tree = ast.parse(code, f"<{self.slug}>")
        if not tree.body or not isinstance(tree.body[-1], ast.Expr):
            raise ValueError(f"{self.slug}: a demo's last line must be an expression - the thing to show")
        last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, f"<{self.slug}>", "exec"), self.namespace)
        return eval(compile(last, f"<{self.slug}>", "eval"), self.namespace)


def to_component(value: Any, name: str) -> rx.Component:
    if isinstance(value, rx.Component):
        return value
    if isinstance(value, Kind):
        def page() -> Any:
            return value
        page.__name__ = page.__qualname__ = name.replace("/", "_").replace("-", "_")
        return ui.dashboard(page)()
    if isinstance(value, Part):
        return ui.embed(value, name=name)
    raise TypeError(f"{name}: a demo shows a component, a lex.ui page kind (ui.record, ui.list) or a part "
                    f"(ui.table, ui.section); got {type(value).__name__}")


def demo_card(code: str | None, component: rx.Component) -> rx.Component:
    from docs_site.sandbox.role import switch

    shown = [rx.hstack(rx.text("Viewing as", size="1", color_scheme="gray"), switch(),
                       justify="end", width="100%"),
             rx.box(component, class_name="docs-demo-frame", width="100%")]
    if code is not None:
        shown.insert(0, rx.code_block(code.rstrip("\n"), language="python", can_copy=True))
    return rx.vstack(*shown, spacing="3", align="stretch", class_name="docs-demo")
```

If `ui.dashboard(page)()` refuses a page kind with no load (read `declare.py:dashboard` — `_loads` should accept none), use `ui.embed` on each of the kind's parts instead only after confirming the error; do not change `lex/`.

- [ ] **Step 4: Wire the transformer**

In `render/blocks.py`, give `LexDocTransformer.__init__` a page module and replace `live_block`:

```python
    def __init__(self, page: Page):
        from docs_site.render.live import PageModule

        self.page = page
        self.module = PageModule(page.slug)
        self._demos = 0

    def live_block(self, block: CodeBlock) -> rx.Component:
        from docs_site.render.live import demo_card, to_component

        kind = block.flags[0]
        if kind == "exec":
            self.module.run(block.content)
            return rx.fragment()
        if kind in ("demo", "demo-only"):
            self._demos += 1
            shown = to_component(self.module.value(block.content), f"{self.page.slug}#{self._demos}")
            return demo_card(None if kind == "demo-only" else block.content, shown)
        raise ValueError(f"{self.page.slug}: ```python {kind}``` is not a fence this site knows "
                         "(exec, demo, demo-only)")
```

- [ ] **Step 5: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): live demos - setup and demo fences in a module per page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The layout — landing, sidebar, contents, prev/next — and every page as a route

**Files:**
- Create: `docs_site/docs_site/layout.py`
- Modify: `docs_site/docs_site/docs_site.py` (replace `_home` and its `add_page` with `layout.add_pages(app)`)
- Test: `docs_site/tests/test_layout_compile.py` (append)

**Interfaces:**
- Consumes: `nav.*`, `content.load`, `render.render_page`, `render.toc`.
- Produces: `layout.route_of(slug) -> str` (`"index"` → `"/"`, else `"/" + slug`), `layout.shell(slug, body, page=None) -> rx.Component`, `layout.add_pages(app) -> None` (adds `/` and every `nav.ordered()` page). Task 9 adds the header's search box, Task 10 the reference pages, through `layout.add_pages`.

- [ ] **Step 1: Write the failing test** (append)

```python
from docs_site import nav


def test_every_page_in_nav_is_a_route(compiled):
    routes = set(compiled.pages)
    for slug in nav.ordered():
        assert slug in routes, slug
```

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_layout_compile.py -q`
Expected: FAIL on `get-started/first-dashboard`. (`app._pages` keys are routes without the leading `/`; if they carry it, compare with `"/" + slug`.)

- [ ] **Step 2: Write `layout.py`** (no `from __future__ import annotations`)

```python
"""The site's frame: header, sidebar, the page, "On this page", previous and next."""

import reflex as rx
import rfds

from docs_site import nav
from docs_site.content import load
from docs_site.render import render_page, toc


def route_of(slug: str) -> str:
    return "/" if slug == "index" else "/" + slug


def _title(slug: str) -> str:
    return load(slug).title


def _header() -> rx.Component:
    return rx.hstack(
        rx.link(rx.heading("lex-app dashboards", size="4"), href="/", underline="none"),
        rx.spacer(),
        rx.box(id="docs-search-slot"),
        padding="12px 24px", border_bottom="1px solid var(--gray-a5)", width="100%", align="center",
    )


def _sidebar(current: str) -> rx.Component:
    items = []
    for section in nav.NAV:
        items.append(rx.text(section.title, weight="bold", size="2", margin_top="12px"))
        for group in section.groups:
            if len(section.groups) > 1:
                items.append(rx.text(group.title, size="1", color_scheme="gray", margin_top="4px"))
            for slug in group.pages:
                items.append(rx.link(_title(slug), href=route_of(slug), size="2",
                                     weight="medium" if slug == current else "regular",
                                     color_scheme="teal" if slug == current else "gray",
                                     aria_current="page" if slug == current else None))
    return rx.vstack(*items, spacing="1", align="start", width="240px", flex_shrink="0",
                     padding="16px", class_name="docs-sidebar")


def _prev_next(slug: str) -> rx.Component:
    before, after = nav.neighbours(slug)
    return rx.hstack(
        rx.link(f"← {_title(before)}", href=route_of(before)) if before else rx.box(),
        rx.link(f"{_title(after)} →", href=route_of(after)) if after else rx.box(),
        justify="between", width="100%", margin_top="32px",
    )


def shell(slug: str, body: rx.Component, *, aside: rx.Component | None = None) -> rx.Component:
    return rx.vstack(
        _header(),
        rx.hstack(
            _sidebar(slug),
            rx.box(body, flex="1", min_width="0", max_width="760px", padding="24px"),
            rx.box(aside or rx.fragment(), width="200px", flex_shrink="0", padding="24px",
                   display=["none", "none", "block"]),
            align="start", width="100%",
        ),
        spacing="0", width="100%",
    )


def _content_page(slug: str):
    def page() -> rx.Component:
        doc = load(slug)
        section = nav.section_of(slug)
        body = rx.vstack(
            rx.text(section.title, size="2", color_scheme="gray"),
            rx.heading(doc.title, size="8", as_="h1"),
            rx.text(doc.description, size="4", color_scheme="gray") if doc.description else rx.fragment(),
            render_page(doc),
            _prev_next(slug),
            spacing="4", align="stretch",
        )
        return shell(slug, body, aside=toc(doc))
    page.__name__ = page.__qualname__ = "page_" + slug.replace("/", "_").replace("-", "_")
    return page


def _landing() -> rx.Component:
    doc = load("index")
    cards = [rx.link(rx.card(rx.heading(s.title, size="4"), rx.text(s.blurb, size="2")),
                     href=route_of(s.groups[0].pages[0]), underline="none")
             for s in nav.NAV]
    body = rx.vstack(rx.heading(doc.title, size="8", as_="h1"), render_page(doc),
                     rx.grid(*cards, columns=rx.breakpoints(initial="1", sm="2", lg="3"), spacing="4"),
                     spacing="5", align="stretch")
    return shell("index", body)


def add_pages(app: rx.App) -> None:
    app.add_page(_landing, route="/", title=load("index").title, description=load("index").description)
    for slug in nav.ordered():
        doc = load(slug)
        app.add_page(_content_page(slug), route=route_of(slug), title=f"{doc.title} · lex-app dashboards",
                     description=doc.description)
```

The header and sidebar use only Radix/RFDS tokens (`var(--gray-a5)`, `color_scheme`), so lex-app's theme carries the look. Collapsible groups are later work; flat groups ship first.

- [ ] **Step 3: Use it in the app**

In `docs_site/docs_site/docs_site.py`, delete `_home` and its `app.add_page(...)`, import `from docs_site import layout`, and call `layout.add_pages(app)` before `ui.install(app)`.

- [ ] **Step 4: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass. If the compile fails with RFDS conformance errors (CI mode) about raw `rx.*` layout, read the message: fix the component it names (RFDS will name the allowed replacement); do not set `LEX_RFDS_CONFORMANCE` to silence it.

- [ ] **Step 5: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): landing cards, sidebar, contents and previous/next on every page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Search

**Files:**
- Create: `docs_site/docs_site/search.py`
- Modify: `docs_site/docs_site/layout.py` (`_header` uses `search.box()` instead of the slot)
- Test: `docs_site/tests/test_search.py`

**Interfaces:**
- Consumes: `content.all_slugs`, `content.load`, `content.plain`, `layout.route_of`.
- Produces: `search.Entry(slug, title, heading, anchor, text)`; `search.build_index() -> list[Entry]` (one entry per page and per `##`/`###` section, with that section's paragraph text); `search.find(index, query, limit=8) -> list[Entry]` (all words must match, title hits first, then heading, then text); `search.SearchState` (`query: str`, computed `results: list[dict]` with `title`, `heading`, `href`); `search.box() -> rx.Component`.

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_search.py`:

```python
"""Search finds pages and sections by every word typed, titles first."""

from docs_site.search import build_index, find


def test_every_word_must_match_and_titles_come_first():
    index = build_index()
    hits = find(index, "ui.record")
    assert hits and hits[0].slug == "pages/record"
    assert find(index, "zzzz-nothing") == []


def test_a_section_hit_links_to_its_anchor():
    index = build_index()
    section_hits = [e for e in index if e.anchor]
    assert all(e.slug and e.heading for e in section_hits)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_search.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `search.py`** (no `from __future__ import annotations`)

```python
"""Search over every page and section, in the app's backend, as the reader types."""

from dataclasses import dataclass
from functools import cache

import reflex as rx
from reflex_docgen.markdown import HeadingBlock, TextBlock

from docs_site.content import all_slugs, anchor, load, plain


@dataclass(frozen=True)
class Entry:
    slug: str
    title: str
    heading: str
    anchor: str
    text: str


@cache
def build_index() -> list[Entry]:
    out: list[Entry] = []
    for slug in all_slugs():
        page = load(slug)
        heading, words = "", []
        for block in page.document.blocks:
            if isinstance(block, HeadingBlock) and block.level in (2, 3):
                out.append(Entry(slug, page.title, heading, anchor(heading) if heading else "", " ".join(words)))
                heading, words = plain(block.children), []
            elif isinstance(block, TextBlock):
                words.append(plain(block.children))
        out.append(Entry(slug, page.title, heading, anchor(heading) if heading else "", " ".join(words)))
    return out


def find(index: list[Entry], query: str, limit: int = 8) -> list[Entry]:
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []

    def rank(e: Entry) -> int | None:
        hay = f"{e.title} {e.heading} {e.text}".lower()
        if not all(t in hay for t in terms):
            return None
        if all(t in e.title.lower() for t in terms):
            return 0 if not e.heading else 1
        return 2 if all(t in e.heading.lower() for t in terms) else 3

    ranked = sorted((r, i, e) for i, e in enumerate(index) if (r := rank(e)) is not None)
    return [e for _, _, e in ranked[:limit]]


class SearchState(rx.State):
    query: str = ""

    @rx.event
    def set_query(self, value: str):
        self.query = value

    @rx.var
    def results(self) -> list[dict]:
        from docs_site.layout import route_of

        return [{"title": e.title, "heading": e.heading,
                 "href": route_of(e.slug) + (f"#{e.anchor}" if e.anchor else "")}
                for e in find(build_index(), self.query)]


def box() -> rx.Component:
    return rx.popover.root(
        rx.popover.trigger(rx.input(placeholder="Search the docs", value=SearchState.query,
                                    on_change=SearchState.set_query, width="260px", aria_label="Search")),
        rx.popover.content(
            rx.foreach(SearchState.results, lambda r: rx.link(
                rx.vstack(rx.text(r["title"], weight="medium", size="2"),
                          rx.text(r["heading"], size="1", color_scheme="gray"), spacing="0"),
                href=r["href"], underline="none")),
            width="320px",
        ),
        open=SearchState.results.length() > 0,
    )
```

In `layout.py`, `_header`: replace `rx.box(id="docs-search-slot")` with `search.box()` (import inside the function to avoid an import cycle: `from docs_site import search`).

- [ ] **Step 4: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass. (`test_every_word_must_match...` needs `pages/record.md` to have title `ui.record`, from Task 5.)

- [ ] **Step 5: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): search every page and section from the header

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Every page as Markdown — `.md`, `llms.txt`, `llms-full.txt`, Copy page

**Files:**
- Create: `docs_site/docs_site/llms.py`
- Modify: `docs_site/docs_site/docs_site.py` (`rfds.app_kwargs(..., extra_routes=llms.routes())`), `docs_site/docs_site/layout.py` (a "Copy page" and "View Markdown" pair in each content page's title row)
- Test: `docs_site/tests/test_llms.py`

**Interfaces:**
- Consumes: `content.*`, `nav.*`.
- Produces: `llms.page_markdown(slug) -> str` (the source without front matter, with `# Title` and the description on top), `llms.llms_txt() -> str`, `llms.llms_full() -> str`, `llms.routes() -> list[starlette.routing.Route]` serving `GET /md/{slug:path}.md`, `GET /llms.txt`, `GET /llms-full.txt` as `text/markdown; charset=utf-8` / `text/plain; charset=utf-8`. Routes live on the backend; the page links use `rx.config.get_config().api_url`.

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_llms.py`:

```python
"""Each page as Markdown, and the indexes an AI tool reads."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from docs_site import llms, nav


def test_a_page_as_markdown_has_title_and_no_front_matter():
    text = llms.page_markdown("pages/record")
    assert text.startswith("# ui.record\n")
    assert "title:" not in text.split("\n\n", 2)[1]


def test_llms_txt_lists_every_section_and_page():
    text = llms.llms_txt()
    for section in nav.NAV:
        assert f"## {section.title}" in text
    for slug in nav.ordered():
        assert f"/md/{slug}.md)" in text


def test_routes_serve_markdown():
    client = TestClient(Starlette(routes=llms.routes()))
    page = client.get("/md/pages/record.md")
    assert page.status_code == 200 and page.text.startswith("# ui.record")
    assert client.get("/md/nope.md").status_code == 404
    assert client.get("/md/../../etc/passwd.md").status_code == 404
    assert client.get("/llms.txt").status_code == 200
    assert "# ui.record" in client.get("/llms-full.txt").text
```

`starlette.testclient` needs `httpx` (already installed with lex-app).

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_llms.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Write `llms.py`**

```python
"""The site as Markdown, for readers working with an AI tool.

``/md/<page>.md`` is one page; ``/llms.txt`` lists every page by section; ``/llms-full.txt`` is
every page in one file. Served by the backend, from the same files the pages are drawn from.
"""

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from docs_site import nav
from docs_site.content import all_slugs, load

_FRONT = "---"


def _body(source: str) -> str:
    if source.startswith(_FRONT):
        end = source.find("\n" + _FRONT, len(_FRONT))
        if end != -1:
            return source[end + len(_FRONT) + 1:].lstrip("\n")
    return source


def page_markdown(slug: str) -> str:
    page = load(slug)
    head = f"# {page.title}\n\n" + (f"{page.description}\n\n" if page.description else "")
    return head + _body(page.source)


def llms_txt() -> str:
    index = load("index")
    lines = [f"# {index.title}", "", f"> {index.description}", ""]
    for section in nav.NAV:
        lines += [f"## {section.title}", ""]
        for slug in (s for g in section.groups for s in g.pages):
            page = load(slug)
            lines.append(f"- [{page.title}](/md/{slug}.md)" + (f": {page.description}" if page.description else ""))
        lines.append("")
    lines.append("Every page in one file: [/llms-full.txt](/llms-full.txt)")
    return "\n".join(lines) + "\n"


def llms_full() -> str:
    return "\n\n---\n\n".join(page_markdown(slug) for slug in ["index", *nav.ordered()])


async def _page(request: Request) -> Response:
    slug = request.path_params["slug"]
    if slug not in set(all_slugs()):
        return PlainTextResponse("No such page.", status_code=404)
    return Response(page_markdown(slug), media_type="text/markdown; charset=utf-8")


async def _llms(request: Request) -> Response:
    return PlainTextResponse(llms_txt())


async def _full(request: Request) -> Response:
    return PlainTextResponse(llms_full())


def routes() -> list[Route]:
    return [Route("/md/{slug:path}.md", _page), Route("/llms.txt", _llms), Route("/llms-full.txt", _full)]
```

The 404 check against `all_slugs()` is also the path-traversal guard: only a known slug is ever read.

- [ ] **Step 4: Mount the routes and add the buttons**

In `docs_site.py`: `app = rx.App(**rfds.app_kwargs(html_lang="en", extra_head=lex_head_components(), extra_routes=llms.routes()))` (import `from docs_site import llms`). Confirm `extra_routes` takes Starlette `Route`s by reading `rfds/platform` `rfds_api` (`grep -n "extra_routes" -r ../.venv-docs/lib/python3.12/site-packages/rfds/platform*`); if it wants another shape, wrap them as it says.

In `layout._content_page`, put this row right under the `h1`:

```python
            rx.hstack(
                rx.button("Copy page", variant="soft", size="1",
                          on_click=rx.set_clipboard(llms.page_markdown(slug))),
                rx.link("View Markdown", href=f"{rx.config.get_config().api_url}/md/{slug}.md",
                        is_external=True, size="1"),
                spacing="3",
            ),
```

(with `from docs_site import llms` at the top of `layout.py`).

- [ ] **Step 5: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): every page as Markdown, llms.txt and llms-full.txt, Copy page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: API reference from `lex.ui`

**Files:**
- Create: `docs_site/docs_site/reference.py`
- Modify: `docs_site/docs_site/nav.py` (an "API reference" section built from `reference.GROUPS`), `docs_site/docs_site/layout.py` (`add_pages` adds the reference pages; `_sidebar` shows them), `docs_site/docs_site/content.py` (`load` answers `reference/<group>` from `reference.page_source`)
- Test: `docs_site/tests/test_reference.py`

**Interfaces:**
- Consumes: `lex.ui.__all__`.
- Produces: `reference.GROUPS: dict[str, tuple[str, ...]]` mapping a group slug to the `lex.ui` names it documents; `reference.page_source(group) -> str` (generated Markdown: `title:` front matter, then per name an `## ui.<name>` heading, a ` ```python ` block with the signature, and the docstring); `reference.undocumented() -> list[str]` (names in `ui.__all__` not in any group).

Groups (every name in `ui.__all__` exactly once; `testing` is a module and goes in `testing`):

```python
GROUPS = {
    "pages": ("page", "dashboard", "reflex_page", "chrome", "install", "embed", "record", "list"),
    "parts": ("section", "table", "column", "fact", "text", "each", "status", "custom", "custom_block",
              "button", "link", "switch", "address", "message", "progress", "progress_of"),
    "state": ("State", "action", "job", "load", "rows", "running", "failed", "loaded", "cancel", "refresh"),
    "results": ("toast", "goto", "download", "Refuse", "ApiError", "PageError"),
    "permissions": ("read", "create", "edit", "delete", "signed_in", "permission"),
    "testing": ("testing",),
}
```

- [ ] **Step 1: Write the failing tests**

`docs_site/tests/test_reference.py`:

```python
"""The API reference covers every name lex.ui exports, from its own docstrings."""

from docs_site import reference


def test_every_export_is_in_one_group():
    assert reference.undocumented() == []
    names = [n for group in reference.GROUPS.values() for n in group]
    assert len(names) == len(set(names))


def test_a_reference_page_has_signatures_and_docstrings():
    text = reference.page_source("pages")
    assert text.startswith("---\ntitle: Pages and page kinds")
    assert "## ui.record" in text
    assert "ui.record(*, title" in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_reference.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Write `reference.py`**

```python
"""API reference pages, generated from lex.ui's exports and their docstrings."""

import inspect

from lex import ui

GROUPS: dict[str, tuple[str, ...]] = {
    "pages": ("page", "dashboard", "reflex_page", "chrome", "install", "embed", "record", "list"),
    "parts": ("section", "table", "column", "fact", "text", "each", "status", "custom", "custom_block",
              "button", "link", "switch", "address", "message", "progress", "progress_of"),
    "state": ("State", "action", "job", "load", "rows", "running", "failed", "loaded", "cancel", "refresh"),
    "results": ("toast", "goto", "download", "Refuse", "ApiError", "PageError"),
    "permissions": ("read", "create", "edit", "delete", "signed_in", "permission"),
    "testing": ("testing",),
}
TITLES = {"pages": "Pages and page kinds", "parts": "Parts", "state": "State and handlers",
          "results": "Results and errors", "permissions": "Permissions", "testing": "Testing"}


def undocumented() -> list[str]:
    listed = {n for group in GROUPS.values() for n in group}
    return sorted(set(ui.__all__) - listed)


def _signature(name: str, obj) -> str:
    if inspect.ismodule(obj):
        return f"from lex.ui import {name}"
    try:
        return f"ui.{name}{inspect.signature(obj)}".replace("'", "")
    except (TypeError, ValueError):
        return f"ui.{name}"


def page_source(group: str) -> str:
    parts = [f"---\ntitle: {TITLES[group]}\ndescription: lex.ui's {TITLES[group].lower()}, from the code.\n---\n"]
    for name in GROUPS[group]:
        obj = getattr(ui, name)
        doc = inspect.cleandoc(inspect.getdoc(obj) or "No docstring yet.")
        parts.append(f"## ui.{name}\n\n```python\n{_signature(name, obj)}\n```\n\n{doc}\n")
    return "\n".join(parts)
```

Docstrings are shown as Markdown. If one breaks rendering (an unbalanced fence, say), fix the rendering by wrapping that docstring in a ` ```text ` block, never by editing `lex/`.

- [ ] **Step 4: Wire it into content, nav and layout**

- `content.load(slug)`: if `slug.startswith("reference/")`, use `reference.page_source(slug.split("/", 1)[1])` as `source` instead of reading a file. `content.all_slugs()` appends `f"reference/{g}"` for each group.
- `nav.NAV`: append `Section("reference", "API reference", "Every name lex.ui exports, with its signature.", (Group("API reference", tuple(f"reference/{g}" for g in reference.GROUPS)),))`. `nav.problems()` already skips `reference/` slugs when comparing with files.
- `layout.add_pages`: no change needed; it adds every `nav.ordered()` slug.

- [ ] **Step 5: Run all tests**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass (the compile test now also covers the reference routes).

- [ ] **Step 6: Commit**

```bash
git add docs_site
git commit -m "feat(docs-site): API reference generated from lex.ui's exports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The first real pages, and a test that runs every page

**Files:**
- Modify: `content/get-started/first-dashboard.md`, `content/get-started/lex-reflex-add.md`, `content/pages/ui-page.md`, `content/pages/record.md`, `content/pages/list.md`, `content/pages/tables.md`, `content/state-and-actions/state.md`, `content/state-and-actions/action.md`, `content/state-and-actions/job.md`
- Create: `docs_site/tests/test_pages_run.py`

**Interfaces:**
- Consumes: everything above. `@ui.page` only in plain ` ```python ` blocks; demos return kinds or parts.

- [ ] **Step 1: Write the test that runs every page**

`docs_site/tests/test_pages_run.py`:

```python
"""Every page renders: its setup runs, every demo builds, every internal link resolves."""

import re

import pytest

from docs_site import content, nav
from docs_site.layout import route_of
from docs_site.render import render_page

SLUGS = ["index", *nav.ordered()]
ROUTES = {route_of(s) for s in SLUGS}
ANCHORS = {route_of(s): {a for _, _, a in content.load(s).headings} for s in SLUGS}


@pytest.mark.parametrize("slug", SLUGS)
def test_the_page_renders(slug):
    render_page(content.load(slug))


@pytest.mark.parametrize("slug", SLUGS)
def test_internal_links_resolve(slug):
    for target in re.findall(r"\]\((/[^)\s]*)\)", content.load(slug).source):
        path, _, frag = target.partition("#")
        if path.startswith("/md/") or path in ("/llms.txt", "/llms-full.txt"):
            continue
        assert path in ROUTES, f"{slug}: link to {target}"
        if frag:
            assert frag in ANCHORS[path], f"{slug}: no #{frag} on {path}"
```

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest tests/test_pages_run.py -q`
Expected: PASS on the stubs (nothing live in them yet).

- [ ] **Step 2: Write "Your first dashboard"**

`content/get-started/first-dashboard.md`, in full:

````markdown
---
title: Your first dashboard
description: A page that lists your open funds and lets you close one, in about twenty lines.
---

A lex-app dashboard is a page written in Python. You describe what is on it - a title, a table,
a button - and lex-app decides how it looks, loads its data as the signed-in user, and checks
their permissions. You write no layout code and no event plumbing.

## The state

A page's data lives on a state: a class with fields, and handlers that fill them. A handler is a
plain function. `self.api` reads and writes records as the user who is looking.

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class Funds(ui.State):
    @ui.rows
    def open_funds(self):
        return self.api.list(Fund, filters={"status": "investing"}, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

```python
from lex import ui
from myproject.models import Fund


class Funds(ui.State):
    @ui.rows
    def open_funds(self):
        return self.api.list(Fund, filters={"status": "investing"}, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

## The page

The page says what is on it. `ui.list` is the kind of page for finding things: a title and a table.

```python demo
ui.list(
    title="Open funds",
    table=ui.table(
        Funds.open_funds,
        columns=["name", "vintage", ui.column("size", kind="money", currency="currency")],
        row_actions=lambda row: [ui.button("Close", Funds.close, row["id"])],
    ),
)
```

Click **Close** on a fund: it leaves the list. Switch **Viewing as** to Viewer and the action is
refused, because a viewer may read funds but not edit them.

## Giving it an address

In your project, the same page gets a route with `@ui.page`:

```python
@ui.page("/funds", nav="Funds", requires=ui.read(Fund))
def funds():
    return ui.list(title="Open funds", table=...)
```

Next: [let `lex reflex add` write a page like this for you](/get-started/lex-reflex-add).
````

Before committing it, check every call against the source on this branch: `ui.action(requires=...)` (`lex/lex_app/reflex/ui/handlers.py`), `ui.column(..., kind="money", currency=...)` (`ui/pages/tables.py:39`), `row_actions=` returning buttons with arguments (`tables.py:_row_actions`), `ui.list(table=)` (`kinds.py`). Where the code differs, change the page, not `lex/`. If a viewer's refused action does not show a refusal in the demo, say what it does show.

- [ ] **Step 3: Write the other eight pages**

Each page: front matter (`title`, `description`), an opening paragraph saying what the feature is for, then sections with at least one live demo, and a "Next" link. Content per page, all claims checked against the file named:

| Page | Covers (source) | Live demos |
| --- | --- | --- |
| `get-started/lex-reflex-add` | `lex reflex add <block> --model X`, blocks `list_report`/`object_page`/`overview`, `--route --title --nav --force --no-register`, where the file goes (`lex/lex_app/reflex/scaffold.py`, PR #905) | none: show the command and the generated file as plain code |
| `pages/ui-page` | `@ui.page(route, nav=, icon=, requires=, params=)`, `@ui.dashboard`, one route per page (`ui/pages/declare.py`) | a `ui.record` shown through `ui.dashboard`, to show what a page body is |
| `pages/record` | `ui.record(title, load, status, facts, sections, actions, tabs)`, `ui.fact`, `ui.section(load=)` (`ui/pages/kinds.py`, `parts.py`) | a fund record with facts, a commitments section from `@ui.rows`, an Export-style button calling a `@ui.action` |
| `pages/list` | `ui.list(title, load, table, actions)` (`kinds.py`) | open funds, with a "New fund" action that `self.api.create`s one |
| `pages/tables` | `ui.table` over `@ui.rows`: `columns`, `ui.column(kind=, currency=)`, `search=`, `filters=`, `open=`, `row_actions=`, `export=`, `totals=`; `ui.table(Model, where=)` as plain code with an `md alert info` that it reads lex-app's API directly and is not live here (`tables.py`) | commitments with totals and search; funds filtered by status |
| `state-and-actions/state` | `ui.State`; `self.user`, `self.api`, `self.params`, `self.can`, `self.state(Other)`, `self.tab_id` (`ui/context.py`) | a card showing `self.user.name` and `self.can(Fund, "edit")`, changing with **Viewing as** |
| `state-and-actions/action` | `@ui.action(requires=)`: runs in a worker thread, failed changes discarded, returns `ui.toast`/`ui.goto` (`ui/handlers.py`, `results.py`) | an action that raises `ui.Refuse("...")` and one that succeeds |
| `state-and-actions/job` | `@ui.job`: working copy merged at each `yield`, progress, cancel, one run per tab and arguments (`ui/runs.py`) | a job that walks the commitments with `yield ui.progress(...)` and `time.sleep(0.3)` per step, capped at 10 steps, with a Cancel button |

Each page's states are defined in a ` ```python exec ` block. Where a reader's project would import its own models, show that in a plain ` ```python ` block next to it, as "Your first dashboard" does.

- [ ] **Step 4: Run every page**

Run: `cd docs_site && ../.venv-docs/bin/python -m pytest -q`
Expected: all pass. A demo that fails to build fails here with the page's slug in the message.

- [ ] **Step 5: Check one demo's handlers really run**

Append to `tests/test_pages_run.py`:

```python
def test_the_first_dashboard_closes_a_fund():
    from docs_site.render.live import PageModule
    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.store import Store
    from lex.lex_app.reflex.ui import testing

    render_page(content.load("get-started/first-dashboard"))
    funds = __import__("sys").modules["docs_site.pages.get_started_first_dashboard"].Funds
    api = SandboxApi(Store().tab("x"))
    testing.run(funds, "close", grants=[("docs.Fund", "edit")], api=api, pk=2)
    assert api.get(__import__("docs_site.sandbox.models", fromlist=["Fund"]).Fund, 2)["status"] == "closed"
```

Check `testing.run`'s keyword for handler arguments in `lex/lex_app/reflex/ui/testing.py:296` and match it. Run the suite again; expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add docs_site
git commit -m "docs(docs-site): get started, pages, state and actions - with live demos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: CI

**Files:**
- Create: `.github/workflows/docs_site.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Docs site

on:
  pull_request:
    paths:
      - "lex/ui.py"
      - "lex/lex_app/reflex/**"
      - "docs_site/**"
      - "requirements.txt"
      - ".github/workflows/docs_site.yml"
  workflow_dispatch:

jobs:
  docs-site:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: System libraries (pycairo)
        run: sudo apt-get update && sudo apt-get install -y libcairo2-dev pkg-config
      - name: Install lex-app and the docs site
        run: pip install -e . -r docs_site/requirements.txt pytest
      - name: Build and check every page
        working-directory: docs_site
        env:
          CI: "true"
        run: python -m pytest -q
```

- [ ] **Step 2: Check it locally as far as possible**

Run: `cd docs_site && CI=true ../.venv-docs/bin/python -m pytest -q`
Expected: all pass. Validate the YAML: `../.venv-docs/bin/python -c "import yaml,sys; yaml.safe_load(open('../.github/workflows/docs_site.yml'))"`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs_site.yml
git commit -m "ci: build the docs site and run every page on PRs that touch lex.ui

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Push only when the user asks, and over SSH (`git push git@github.com:ExcellenceCloudGmbH/lex-app.git feat/reflex-docs-site`): the workflow file cannot go over HTTPS with the current token.

---

## Not in this plan (from the spec)

Deployment and domain; versioned docs; moving the Quartz content; replacing the mirrored Reflex pages upstream; live `ui.table(Model)` (needs a seam in `lex/lex_app/reflex/data.py:lex_api`, with cluster tests); collapsible sidebar groups and the desktop/phone width toggle; screenshots.
