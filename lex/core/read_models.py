"""Read models: lex models that only show data.

A read model is a model with ``lex_read_model = True`` that has no table of
its own to write — either a database **view** (``Meta.managed = False``,
created by a migration with :class:`CreateView`) or a **projection** of an
existing table (``Meta.proxy = True``) with its own serializer, filters and
computed fields. It is served by the generic list and record endpoints (with
filtering, sorting, paging and export) and may carry actions; everything that
would write to it answers 405, and it has no history. Use it for overviews,
dashboards and lists whose rows are joins, unions or the answer of a
database function — with ``security_invoker`` views, row-level security of
the underlying tables still decides who sees what.

Two hooks for any model, read model or not:

* ``lex_list_queryset(cls, queryset, request)`` — the server's default
  selection of the list endpoint only (for example "without archived rows");
  records stay reachable by id.
* a serializer's ``lex_prepare_page(cls, instances, context)`` classmethod —
  called once per list page (and once for a single record) before the rows
  are serialized, so computed fields can be loaded for all of them at once.
"""
from __future__ import annotations

import re

from django.core.exceptions import ImproperlyConfigured
from django.db.migrations.operations.base import Operation


def is_read_model(model) -> bool:
    model = model if isinstance(model, type) else type(model)
    return bool(getattr(model, "lex_read_model", False))


def validate(model) -> None:
    """A read model must be a view (managed = False) or a projection (proxy = True)."""
    if is_read_model(model) and model._meta.managed and not model._meta.proxy:
        raise ImproperlyConfigured(
            f"{model.__name__} sets lex_read_model but has a table of its own: "
            "use Meta.managed = False (a view) or Meta.proxy = True (a projection)"
        )


def list_queryset(model, queryset, request):
    """``queryset`` with the model's default list selection (``lex_list_queryset``)."""
    hook = getattr(model, "lex_list_queryset", None)
    return queryset if hook is None else hook(queryset, request)


_TABLE = re.compile(r"\{table:([A-Za-z_][A-Za-z0-9_]*)\}")


class CreateView(Operation):
    """Create (and drop on reverse) the view behind an unmanaged read model.

    ``sql`` is the view's SELECT; ``{table:Model}`` placeholders become the
    real table names of models of the same app (lex assigns them), and the
    view gets the read model's own table name. On PostgreSQL the view is
    ``security_invoker`` by default, so the caller's row-level security
    applies to every table it reads.
    """

    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name, sql, security_invoker=True):
        self.model_name = model_name
        self.sql = sql
        self.security_invoker = security_invoker

    def deconstruct(self):
        return (self.__class__.__name__, [self.model_name, self.sql], {"security_invoker": self.security_invoker})

    def state_forwards(self, app_label, state):
        pass

    def _resolve(self, app_label, apps):
        def table(match):
            return apps.get_model(app_label, match.group(1))._meta.db_table

        return _TABLE.sub(table, self.sql)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        apps = to_state.apps
        name = apps.get_model(app_label, self.model_name)._meta.db_table
        sql = self._resolve(app_label, apps)
        options = ""
        if self.security_invoker and schema_editor.connection.vendor == "postgresql":
            options = " WITH (security_invoker = true)"
        schema_editor.execute(f'DROP VIEW IF EXISTS "{name}"')
        schema_editor.execute(f'CREATE VIEW "{name}"{options} AS {sql}')

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        name = from_state.apps.get_model(app_label, self.model_name)._meta.db_table
        schema_editor.execute(f'DROP VIEW IF EXISTS "{name}"')

    def describe(self):
        return f"Create view for read model {self.model_name}"
