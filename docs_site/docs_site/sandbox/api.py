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
