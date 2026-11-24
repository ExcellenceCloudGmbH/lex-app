---
title: "lex reflex check"
description: "Find mistakes in your dashboards' handlers before you ship, without running them."
---

`lex reflex check` reads your dashboards and reports the mistakes that would otherwise show up only
when someone clicks: a method the browser could call that was never meant to be a handler, a
handler that calls another, a query that will be refused. It reads the code without importing or
running it, so it needs no database, no server and no sign-in. Run it before you ship, and in CI:

```bash
lex reflex check
```

## An example

This state has three mistakes:

```python
from lex import ui
from myproject.models import Fund


class Funds(ui.State):
    rows: list[dict] = []

    @ui.load
    def open(self):
        self.rows = list(Fund.objects.values("id", "name"))

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        self.open()

    def total(self):
        return len(self.rows)
```

Saved as `_dashboards/funds.py`, it gets this report (the path is printed in full):

```text
[warning] /work/myproject/_dashboards/funds.py:9  Funds.open: names a model manager without requires=. The query will be refused at run time; use self.api or declare requires=.
[error  ] /work/myproject/_dashboards/funds.py:15  Funds.close: calls the handler self.open(), which does not run from a handler's body. Move the shared work into a method whose name starts with '_' (self._open()) and call it from both handlers.
[error  ] /work/myproject/_dashboards/funds.py:17  Funds.total: a public method with no lex.ui decorator. Reflex makes every public method a handler the browser can call; decorate it with @ui.action, @ui.job, @ui.load or @ui.custom, or, if it is a helper, rename it with a leading underscore.
lex.ui check: 2 error(s), 1 warning(s), 0 listed for review
```

Each line says how serious it is, where, and what to write instead. The fixed state reads its
rows through `self.api`, keeps the shared work in a method whose name starts with `_`, and makes
the helper private:

```python
class Funds(ui.State):
    rows: list[dict] = []

    @ui.load
    def open(self):
        self._read()

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        self._read()

    def _read(self):
        self.rows = self.api.list(Fund)

    def _total(self):
        return len(self.rows)
```

## What it reports

It checks every class that is a `ui.State`, directly or through a base class in the files it
reads.

| Finding | Level | Why it matters |
| --- | --- | --- |
| a public method (no leading `_`) without a lex.ui decorator | error | Reflex makes every public method of a state something the browser can call |
| a raw `@rx.event` on a `ui.State` | error | it skips what lex.ui does for a handler; use `@ui.action`, `@ui.job` or `@ui.load`, or mark it `@ui.custom` |
| a field, computed var or method named `user`, `api`, `params`, `can`, `state` or `tab_id` | error | it would hide the helper of that name in every handler |
| `self.<handler>(...)` of the state's own handler, or one it inherits | error | a handler called from another handler's body does not run |
| a handler that names `.objects` or `._default_manager` without `requires=` | warning | the query is refused when it runs: read through `self.api`, or declare `requires=` |
| a `@ui.custom` handler | note | listed for review |
| a handler with `requires=ui.signed_in` | note | listed for review: any signed-in user may run its queries |
| a file it cannot read as Python | error | its handlers were not checked |

Properties, static and class methods, `cached_property` and computed vars (`@rx.var`) are not
handlers, so they are not reported. The check finds imports under any name: `from lex import ui
as u` or `from lex.ui import State` are understood like the plain spellings.

The manager warning is a best effort. lex-app refuses such a query when it runs whether the check
saw it or not.

## What it does not see

- A public attribute assigned a function (`refresh = lambda self: ...`) is not seen as a method.
- The methods a mixin brings in - a base class that is not a `ui.State` - are not checked, and a
  state calling one of the mixin's handlers is not reported.

A base class is matched by its name across the files checked, so a class that shares the name of
a `ui.State` may be reported when it should not be. It is never missed.

## What it reads, and the rest of the run

`lex reflex check` reads `_reflex_structure.py` and the `_dashboards/` folder at the project's root,
whichever exist. After lex.ui's check it runs two checks of RFDS, the design system lex-app's
dashboards are drawn with:

1. `rfds lint --strict` over the same files: RFDS's rules for components and styling. With
   `--strict`, its warnings fail too.
2. `rfds doctor` over the project: the versions and pins of what is installed, and the checks of
   `rxconfig.py`, including the order of its plugins.

When the project has neither `_reflex_structure.py` nor `_dashboards/`, only `rfds doctor` runs.

The command exits with a non-zero status when any of the three fails, so a CI step fails with it.
For lex.ui's check, errors fail it; warnings and notes are printed and do not.

Next: [Deploying](/ship-it/deploying).
