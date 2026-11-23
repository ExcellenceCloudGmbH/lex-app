"""Reference Reflex dashboard -- copy this to ``<your_repo>/_reflex_structure.py``.

Every feature lex-app gives a Reflex dashboard, on one set of pages, each in
the place a project would use it. It runs on any project: the pages are about
:data:`FEATURED_MODEL` -- set it to one of yours (``"Fund"``); left ``None``, it
is the project's first model.

HOW IT IS LOADED. ``lex reflex`` builds the app (``lex.reflex_app``): Django
first, then RFDS installed on lex-app's sign-in, then this module is imported --
which registers every ``@lex_page`` below -- and ``main()`` is called, once, for
the page at ``/``. Then the whole app compiles.

* ``main()`` and every page function run **once, when the app compiles** --
  not per request, not per user. They return the component tree; what varies at
  run time (the data, the user, the record) lives in a state and reaches the
  page as vars.
* Every page, event handler and var requires sign-in unless it says otherwise.
* UI comes from ``rfds`` (and plain ``reflex``); anything that knows about
  lex-app comes from ``lex.lex_app.reflex``; states and their handlers are
  written with ``from lex import ui``.
* A model's records -- read or written -- go through lex-app's own API as the
  user (``lex_table``, ``lex_grid``, ``self.api`` in a handler), so the
  dashboard shows and changes exactly what the grid would. A handler reads the
  database directly only for what the API has no endpoint for -- here,
  counting rows -- and then declares what it ``requires=``.

=====================  ================================================  ============================
Page                   What it shows                                     Features
=====================  ================================================  ============================
``/``                  KPIs and a chart over every model, an export,     ``main()``, ``@ui.load``,
                       and what the user may do                          ``self.can``, RFDS
                                                                         charts, ``ExportMixin``,
                                                                         ``can``, ``permission``
``/records``           the featured model's records, paged on the        ``lex_page``, ``lex_table``
                       server
``/grid``              the same records in AG Grid                       ``lex_grid``
``/in-lex-app``        lex-app's own table and create form in the        ``lex_view``, ``Flow``,
                       dashboard, reacting to selection and creation     ``@ui.action``
``/calculations``      a selected record's calculation and its log       ``lex_widgets``,
                                                                         ``calculation``,
                                                                         ``calculation_log``
``/edit``              change one field of a record through lex-app      ``self.api``, ``ui.Refuse``
(record dashboard)     one record, for the model's ``reflex_main``       ``ui.State``, ``ui.failed``
=====================  ================================================  ============================
"""

from __future__ import annotations

from typing import Any, ClassVar

import reflex as rx
import rfds
from rfds.auth import can
from rfds.patterns.exports import ExportMixin, export_menu

from lex import ui
from lex.lex_app.reflex import (
    Flow,
    LexUser,
    calculation,
    calculation_log,
    lex_grid,
    lex_page,
    lex_table,
    lex_view,
    lex_widgets,
    permission,
)

#: The model the pages are about: one of your models' class names, e.g. "Fund".
FEATURED_MODEL: str | None = None

#: The navigation heading these pages are listed under.
NAV = "Example"


# -- the project's models ------------------------------------------------------


def _project_models() -> list:
    from lex.lex_app.reflex.dashboards import dashboard_models

    return sorted(dashboard_models().values(), key=lambda model: model._meta.verbose_name.lower())


def featured() -> Any:
    """The model the pages are about (:data:`FEATURED_MODEL`, else the project's first)."""
    models = _project_models()
    for model in models:
        if FEATURED_MODEL and model.__name__ == FEATURED_MODEL:
            return model
    if FEATURED_MODEL:
        raise LookupError(f"FEATURED_MODEL = {FEATURED_MODEL!r} is not a model of this project")
    if not models:
        raise LookupError("this project has no models for the example to show")
    return models[0]


def _resource(model: Any) -> str:
    return model._meta.model_name


# -- / : the overview ------------------------------------------------------------


def _row_counts() -> list[dict]:
    """Every model and how many rows it holds -- plain Django, called from a load that declares ``requires=``."""
    return [
        {"model": str(model._meta.verbose_name_plural).capitalize(), "rows": model._default_manager.count()}
        for model in _project_models()
    ]


class OverviewState(ExportMixin, ui.State):
    """The overview's figures, per viewer; the counts are also its CSV export."""

    export_entity: ClassVar[str] = "row-counts"
    counts: list[dict] = []
    total: int = 0
    readable: list[str] = []

    # Counting rows reads the database directly, so the load says what it requires. The featured
    # model is known only once Django has loaded the project, so the load checks read access to it
    # itself, before it counts anything.
    @ui.load(requires=ui.signed_in)
    def open(self):
        """Read the counts and which models the viewer may read -- per viewer, when the page opens."""
        if not self.can(featured(), "read"):
            raise ui.Refuse("You may not read the featured model, so no figures are shown.")
        self.counts = _row_counts()
        self.total = sum(int(row["rows"]) for row in self.counts)
        self.readable = sorted(model.__name__ for model in _project_models() if self.can(model, "read"))

    async def _export_rows(self, kind: str):
        # RFDS's ExportMixin calls this hook and awaits it: it is RFDS's contract, not a lex.ui handler.
        from lex.lex_app.reflex import run_orm

        rows = await run_orm(_row_counts)
        return ["model", "rows"], rows, {"user": "", "entity": "row counts"}


def main() -> rx.Component:
    """The dashboard ``/`` shows when the URL names no model."""
    model = featured()
    return rfds.page(
        rfds.page_header(title="Overview", path="/", subtitle=LexUser.display_name,
                         secondary_actions=[export_menu(on_export=OverviewState.export, kinds=("csv",))]),
        rfds.grid(
            rfds.kpi_tile(label="Rows, all models", value=OverviewState.total, kind="count"),
            rfds.kpi_tile(label="Models", value=OverviewState.counts.length(), kind="count"),
            columns=3,
        ),
        rfds.card(
            rfds.bar_chart(
                data=OverviewState.counts,
                x="model",
                series=[{"key": "rows", "label": "Rows"}],
                title="Rows per model",
                description="How many rows each of the project's models holds.",
                x_kind="category",
            ),
            title="Rows per model",
        ),
        rfds.card(
            rx.cond(
                OverviewState.readable,
                rx.hstack(rx.foreach(OverviewState.readable, rx.badge), wrap="wrap"),
                rx.text("None yet.", color_scheme="gray"),
            ),
            # Shown only to users Keycloak lets edit the featured model -- what
            # they see, not access control: the edit page's handler checks too.
            can(permission(model, "edit"), rfds.link("Change a record", href="/edit")),
            title="Models you may read",
        ),
        on_mount=OverviewState.open,
    )


# -- /records and /grid : the featured model's records -------------------------------


@lex_page("/records", title="Records", nav=NAV)
def records() -> rx.Component:
    """Server-paged: only the visible page travels, through lex-app's list API."""
    model = featured()
    return rfds.page(
        rfds.page_header(title=str(model._meta.verbose_name_plural).capitalize(), path="/records"),
        lex_table(model, page_size=25),
    )


@lex_page("/grid", title="Grid", nav=NAV)
def grid() -> rx.Component:
    """AG Grid over up to 2,000 rows: instant sorting, filtering, copying and sums."""
    model = featured()
    return rfds.page(
        rfds.page_header(title=f"{str(model._meta.verbose_name_plural).capitalize()} (grid)", path="/grid"),
        lex_grid(model, status_bar=True),
    )


# -- /in-lex-app : lex-app's own screens, framed ----------------------------------------


class InLexAppState(ui.State):
    """What the framed lex-app pages told the dashboard."""

    selected: list[str] = []
    created: list[str] = []

    @ui.action
    def selection(self, payload: dict):
        """The framed table's selection changed: ``{resource, ids}``."""
        self.selected = [str(i) for i in payload.get("ids") or []]

    @ui.action
    def record_created(self, payload: dict):
        """The framed form saved a record: ``{resource, id, data}``."""
        self.created.append(str(payload.get("id")))
        return ui.toast(f"Created record {payload.get('id')}")


@lex_page("/in-lex-app", title="lex-app inside", nav=NAV)
def in_lex_app() -> rx.Component:
    """lex-app's real table and form -- their validation, permissions and hooks -- in the dashboard."""
    resource = _resource(featured())
    return rfds.page(
        rfds.page_header(title="lex-app inside the dashboard", path="/in-lex-app"),
        rfds.card(
            lex_view(resource, height=420, hide_toolbar=True, on_select=InLexAppState.selection),
            rx.text("Selected: ", rx.text.strong(InLexAppState.selected.join(", "))),
            title="lex-app's table",
        ),
        rfds.card(
            # Save, then the table opens on the new record's list: a chained flow.
            lex_view(f"{resource}/create", height=520,
                     flow=Flow().create(resource).table(resource),
                     on_create=InLexAppState.record_created),
            title="lex-app's create form",
        ),
    )


# -- /calculations : lex-app's controls, framed -------------------------------------------


class CalculationState(ui.State):
    """The record whose calculation is shown, and what it last reported."""

    pk: str = ""
    status: str = ""

    @ui.action
    def pick(self, payload: dict):
        ids = payload.get("ids") or []
        self.pk = str(ids[0]) if ids else ""

    @ui.action
    def calculation_status(self, payload: dict):
        """A calculation widget reported: ``{widget_id, model, pk, status}``."""
        self.status = str(payload.get("status") or "")


@lex_page("/calculations", title="Calculations", nav=NAV)
def calculations() -> rx.Component:
    """Pick a record; its calculation and log run in one lex-app widget frame."""
    resource = _resource(featured())
    return rfds.page(
        rfds.page_header(title="Calculations", path="/calculations"),
        lex_view(resource, height=320, on_select=CalculationState.pick),
        rx.cond(
            CalculationState.pk,
            rfds.card(
                lex_widgets(
                    calculation(resource, CalculationState.pk, show_log=True, on_status=True),
                    calculation_log(resource, CalculationState.pk, tree=True),
                    on_status=CalculationState.calculation_status,
                ),
                rx.text("Last status: ", CalculationState.status),
                title="Calculation",
            ),
            rfds.inline_message("Select a record above.", tone="info"),
        ),
    )


# -- /edit : a write through lex-app's API ------------------------------------------------


class EditState(ui.State):
    """One field of one record, changed as the user -- lex-app validates, audits and recalculates."""

    pk: str = ""
    field_name: str = ""
    value: str = ""
    outcome: str = ""

    @ui.action
    def set_pk(self, value: str):
        self.pk = value

    @ui.action
    def set_field_name(self, value: str):
        self.field_name = value

    @ui.action
    def set_value(self, value: str):
        self.value = value

    @ui.action
    def save(self):
        if not (self.pk and self.field_name):
            raise ui.Refuse("Name a record and a field.")
        # A refusal by lex-app (not allowed, or a validation error) is shown to the user as it is.
        self.api.update(featured(), self.pk, {self.field_name: self.value})
        self.outcome = "Saved."


@lex_page("/edit", title="Edit a record", nav=NAV)
def edit() -> rx.Component:
    """The write goes through lex-app's API, so a user without the grant is refused there."""
    return rfds.page(
        rfds.page_header(title="Edit a record", path="/edit"),
        rfds.card(
            rx.vstack(
                rx.input(placeholder="Record id", value=EditState.pk, on_change=EditState.set_pk),
                rx.input(placeholder="Field", value=EditState.field_name, on_change=EditState.set_field_name),
                rx.input(placeholder="New value", value=EditState.value, on_change=EditState.set_value),
                rfds.button("Save", intent="primary", on_click=EditState.save),
                rx.text(EditState.outcome),
                spacing="3",
            ),
            title="Change one field",
        ),
    )


# -- a record dashboard, for the model's reflex_main ----------------------------------------


class RecordState(ui.State):
    """The record ``?model=&pk=`` names, read through lex-app's API as the user."""

    pk: str = ""
    record: dict = {}

    @ui.load(optional=True)
    def open(self):
        self.pk = str(self.params.get("pk", ""))
        if not self.pk:
            raise ui.Refuse("This record does not exist.")
        self.record = self.api.get(featured(), self.pk)


def record_dashboard() -> rx.Component:
    """What a model's ``reflex_main`` returns -- lex-app's Analytics tab frames it::

        class Fund(LexModel):
            @classmethod
            def reflex_main(cls):
                # Your project's package by name: a relative import from a model
                # in a subfolder (Input/Fund.py) would look inside that subfolder.
                from crm._reflex_structure import record_dashboard
                return record_dashboard()
    """
    problem = ui.failed(RecordState.open)
    return rx.box(
        rx.cond(
            problem != "",
            rfds.inline_message(problem, tone="warning"),
            rx.cond(
                ui.running(RecordState.open),
                rfds.spinner(label="Loading"),
                rx.vstack(
                    rx.heading("Record " + RecordState.pk, size="5"),
                    rx.text("Read through lex-app's API as you."),
                ),
            ),
        ),
        on_mount=RecordState.open,
    )
