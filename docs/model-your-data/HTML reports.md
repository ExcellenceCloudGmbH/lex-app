---
title: "HTML Reports"
---

An HTML report is a sidebar entry that shows HTML your code builds when someone opens it, instead of a table of records. It has no database table, no migration and no history — just a class with one method.

The sidebar's own **Streamlit** and **Reflex** entries are HTML reports: each returns an `<iframe>` that frames its dashboard.

## Writing One

Subclass `HTMLReport` and return a string from `get_html`:

```python title="Reports/TeamOverview.py"
from django.utils.html import escape

from lex.core.models.HTMLReport import HTMLReport

from Input.Team import Team


class TeamOverview(HTMLReport):
    def get_html(self, user):
        rows = "".join(
            f"<tr><td>{escape(team.name)}</td><td>{team.budget}</td></tr>"
            for team in Team.objects.order_by("name")
        )
        return f"<h2>Teams</h2><table>{rows}</table>"
```

`user` is the user who opened it — the request's `request.user`. Escape anything that comes from your data, as above: the string is HTML.

Declare a second parameter and the report gets the request as well. The built-in Streamlit report uses it to hand its frame the viewer's access token:

```python
class TeamOverview(HTMLReport):
    def get_html(self, user, request=None):
        ...
```

Import it from `lex.core.models.HTMLReport`, as above. `from lex.core.models import HTMLReport` gives you the module of that name, not the class.

## How It Is Found

The framework finds a report the way it finds a model: at startup, every module of your project that discovery reads is imported, and each subclass of `HTMLReport` **defined in it** is registered. A report a module only imports is not registered a second time. A module named `test_…` or `_…`, or ending in `_structure.py`, is not scanned at all — see [[model-your-data/model structure#Structure Modules|Structure Modules]].

The report's name is its class name in lower case: `TeamOverview` is `teamoverview`.

## Where It Shows

In the sidebar, like a model:

- **Without a `model_structure`**, it is placed by the folder its file is in — `Reports/TeamOverview.py` lands in the `Reports` group.
- **With one**, list it by name, or it has no entry:

```yaml title="model_structure.yaml"
model_structure:
  Reports:
    teamoverview: null
```

The entry is labelled with the class name, `TeamOverview`, unless `model_styling` gives it another — see [[model-your-data/model structure#Group and Model Names|Group and Model Names]].

Opening it fetches the HTML and renders it inside the application's page. The HTML is turned into page elements rather than loaded as a document of its own: `<style>` is kept, `<script>` is dropped. To show anything that needs scripts, frame it in an `<iframe>`, as the built-in entries do.

> [!warning] A report has no permission check of its own
> Every signed-in user sees the entry and can open it. Decide inside `get_html` what `user` may see, and return less — or nothing — for the others.

## Over the API

`GET api/htmlreport/<name>` answers with the report's HTML as a JSON string, and `Cache-Control: no-store`, since the HTML is built per user. It takes a signed-in user or an API key; an unknown name answers 404. See [[reference/REST API]].
