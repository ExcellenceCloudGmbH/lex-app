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
``/``                  KPIs and a chart over the models the viewer may   ``main()``, ``@ui.load``,
                       read, an export, and what the user may do         ``self.can``, RFDS
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
``/edit``              change one field of a record through lex-app,     ``rfds.form``, ``self.api``,
                       a form submitted to one action                    ``ui.Refuse``
(record dashboard)     the record ``?model=&pk=`` names, for the         ``ui.State``,
                       model's ``reflex_main``                           ``ui.failed``,
                                                                         ``record_model``
=====================  ================================================  ============================
"""

from typing import Any, ClassVar

import reflex as rx
import rfds
import rfds.types
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
    record_model,
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


def _row_counts(readable: list) -> list[rfds.types.WireRow]:
    """What one viewer may see: how many rows each model in ``readable`` holds -- plain Django.

    The screen and the export both call it, so they always agree. A viewer who may not read the
    featured model is refused.
    """
    if featured() not in readable:
        raise ui.Refuse("You may not read the featured model, so no figures are shown.")
    return [
        {"model": str(model._meta.verbose_name_plural).capitalize(), "rows": model._default_manager.count()}
        for model in readable
    ]


class OverviewState(ExportMixin, ui.State):
    """The overview's figures, per viewer; the counts are also its CSV export."""

    export_entity: ClassVar[str] = "row-counts"
    counts: list[rfds.types.WireRow] = []
    total: int = 0
    readable: list[str] = []

    # Counting rows reads the database directly, so the load says what it requires. The featured
    # model is known only once Django has loaded the project, so the read access that matters is
    # checked in the body. main() starts it on mount, so a refusal shows in place (ui.failed) and
    # the rest of the page stays: a dashboard's load never sends the browser to the error page.
    @ui.load(requires=ui.signed_in)
    def open(self):
        """Count the rows of the models the viewer may read -- per viewer, when the page opens."""
        readable = [model for model in _project_models() if self.can(model, "read")]
        self.counts = _row_counts(readable)
        self.total = sum(int(row["rows"]) for row in self.counts)
        self.readable = sorted(model.__name__ for model in readable)

    async def _export_rows(self, kind: str):
        # RFDS's ExportMixin awaits this hook outside lex.ui, so it asks has_permission, not self.can.
        # The backstop: main() hides the menu from a viewer the load refused; this refuses the event.
        from lex.lex_app.reflex import has_permission, run_orm

        readable = [model for model in _project_models() if await has_permission(model, "read")(self)]
        rows = await run_orm(_row_counts, readable)
        return ["model", "rows"], rows, {"user": "", "entity": "row counts"}


def main() -> rx.Component:
    """The dashboard ``/`` shows when the URL names no model."""
    model = featured()
    problem = ui.failed(OverviewState.open)
    return rfds.page(
        rfds.page_header(title="Overview", path="/", subtitle=LexUser.display_name,
                         # Offered only while the figures are shown; _export_rows refuses on the server too.
                         secondary_actions=[rx.cond(problem == "",
                                                    export_menu(on_export=OverviewState.export, kinds=("csv",)))]),
        rx.cond(problem != "", rfds.inline_message(problem, tone="warning")),
        rfds.grid(
            rfds.kpi_tile(label="Rows you may read", value=OverviewState.total, kind="count"),
            rfds.kpi_tile(label="Models", value=OverviewState.counts.length(), kind="count"),
            columns=3,
        ),
        rfds.card(
            rfds.bar_chart(
                data=OverviewState.counts,
                x="model",
                series=[{"key": "rows", "label": "Rows"}],
                title="Rows per model",
                description="How many rows each model you may read holds.",
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

    @ui.action
    def save(self, form: dict):
        """The submitted form: ``{pk, field_name, value}``."""
        pk = str(form.get("pk") or "").strip()
        field_name = str(form.get("field_name") or "").strip()
        if not (pk and field_name):
            raise ui.Refuse("Name a record and a field.")
        # A refusal by lex-app (not allowed, or a validation error) is shown to the user as it is.
        self.api.update(featured(), pk, {field_name: form.get("value", "")})
        return ui.toast("Saved.", tone="success")


@lex_page("/edit", title="Edit a record", nav=NAV)
def edit() -> rx.Component:
    """A form: the browser keeps what is typed, and Save sends it to one action."""
    return rfds.page(
        rfds.page_header(title="Edit a record", path="/edit"),
        rfds.card(
            rfds.form(
                rfds.text_input(name="pk", label="Record id", required=True),
                rfds.text_input(name="field_name", label="Field", required=True),
                rfds.text_input(name="value", label="New value"),
                rfds.form_actions(rfds.button("Save", intent="primary", type="submit",
                                              loading=ui.running(EditState.save))),
                id="edit-record",
                on_submit=EditState.save,
            ),
            title="Change one field",
        ),
    )


# -- a record dashboard, for the model's reflex_main ----------------------------------------


def _facts(record: dict[str, rfds.types.WireValue], limit: int = 6) -> list[dict[str, str]]:
    """The first few of a record's own fields, as label and text."""
    own = [(name, value) for name, value in record.items()
           if name not in ("id_field", "short_description") and not name.startswith("lex_reserved")
           and "__" not in name]
    return [{"label": name.replace("_", " ").capitalize(), "value": str(value)} for name, value in own[:limit]]


class RecordState(ui.State):
    """The record ``?model=&pk=`` names, read through lex-app's API as the user."""

    record: dict[str, rfds.types.WireValue] = {}
    facts: list[dict[str, str]] = []

    # Started on mount by record_dashboard(): a failure shows in place, through ui.failed.
    @ui.load
    def open(self):
        model = record_model(self.params.get("model"))
        pk = str(self.params.get("pk") or "")
        if model is None or not pk:
            raise ui.Refuse("This link does not name a record of this project.")
        self.record = self.api.get(model, pk)
        self.facts = _facts(self.record)


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
                RecordState.record,
                rx.vstack(
                    rx.heading(RecordState.record["short_description"], size="5"),
                    rfds.key_value(
                        rx.foreach(RecordState.facts, lambda fact: rfds.kv_item(fact["label"], fact["value"])),
                    ),
                ),
                rfds.spinner(label="Loading"),
            ),
        ),
        on_mount=RecordState.open,
    )
