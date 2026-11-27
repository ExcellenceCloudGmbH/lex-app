---
title: "Start from one of your models"
description: "Write a dashboard page bound to one of your models."
api:
  - "lex reflex add"
---

`lex reflex add` writes a starting page for one of your models into your project. It is a file
you own: change it as you like, or use it as a model for pages of your own. Run it inside your
project:

```bash
lex reflex add list_report --model Fund
```

## The blocks

The first argument says which page to write:

| Block | What it writes |
| --- | --- |
| `list_report` | the model's records in a table that pages, sorts and filters on the server |
| `object_page` | one record's dashboard: its title and lex-app's own record page for it, for the model's `reflex_main` hook |
| `overview` | a tile with the number of records, then the model's table |

`--model` is the model's class name, such as `Fund`.

## Where the file goes

The page is written to `_dashboards/` in your project's root folder, named after the model and the
block: `lex reflex add list_report --model Fund` writes `_dashboards/fund_list_report.py`. The
folder's name starts with `_` so that Django's model discovery never imports a dashboard.

`list_report` and `overview` pages are then imported from `_reflex_structure.py`, so the page is
part of the app the next time it compiles. If the project has no `_reflex_structure.py` yet, one is
created with a plain home page (`main()`). `object_page` is not imported there: its file shows the
`reflex_main` hook to add to your model, which returns the page.

The command prints each file it wrote or changed.

## The options

| Option | What it does |
| --- | --- |
| `--route /funds` | the page's address; by default `/fund` for a list report, `/fund-overview` for an overview |
| `--title "Funds"` | the page's title; by default `Fund list`, `Fund record` or `Fund overview` |
| `--nav "Reports"` | the navigation heading the page is listed under; by default `Dashboards` |
| `--force` | replace the file if it exists; without it, an existing file is left alone and the command fails |
| `--no-register` | do not add the import to `_reflex_structure.py` |

`object_page` has no address of its own, so `--route` and `--nav` change nothing in it.

## What it writes

`lex reflex add list_report --model Fund --route /funds --title Funds` writes this:

```python
"""Funds -- written by `lex reflex add list_report --model Fund`; yours to change.

Imported by _reflex_structure.py, which registers its page when the app compiles.
"""

import rfds
from django.apps import apps

from lex.lex_app.reflex import lex_page, lex_table
from lex.lex_app.settings import repo_name

ROUTE = "/funds"


def _model():
    return apps.get_model(repo_name, "Fund")


@lex_page(ROUTE, title="Funds", nav="Dashboards", requires=f"{repo_name}.Fund:read")
def fund_list_page():
    """Every Fund the user may see, paged, sorted and filtered on the server."""
    return rfds.page(
        rfds.page_header(title="Funds", path=ROUTE),
        lex_table(_model()),
    )
```

and adds this line to `_reflex_structure.py`:

```python
from ._dashboards import fund_list_report  # noqa: F401  (registers its page)
```

```md alert info
The pages it writes use lex-app's lower-level page helpers (`lex_page`, `lex_table`) and RFDS
components directly; `object_page` and `overview` add a `ui.State` with a `@ui.load`. They are not
written with `@ui.page` and `ui.list`, the way [Your first dashboard](/start-here/first-dashboard)
is: rewrite one that way when you want lex-app to lay it out for you.
```

The `overview` block counts the records in the database directly, so its load declares what it
requires: `@ui.load(requires=ui.permission(...))`. A lex.ui handler that does not declare it may
reach the database only through `self.api`.

To check your dashboards as CI does, run `lex reflex check`.
