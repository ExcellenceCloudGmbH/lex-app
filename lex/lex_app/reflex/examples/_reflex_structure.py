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
  lex-app comes from ``lex.lex_app.reflex``.
* A model's records -- read or written -- go through lex-app's own API as the
  user (``lex_table``, ``lex_grid``, ``LexRecordState``, ``lex_api``), so the
  dashboard shows and changes exactly what the grid would. ``run_orm`` is for
  what the API has no endpoint for -- here, counting rows -- behind an
  ``auth=`` check.

=====================  ================================================  ============================
Page                   What it shows                                     Features
=====================  ================================================  ============================
``/``                  KPIs and a chart over every model, an export,     ``main()``, ``run_orm``,
                       and what the user may do                          ``has_permission``, RFDS
                                                                         charts, ``ExportMixin``,
                                                                         ``can``, ``permission``
``/records``           the featured model's records, paged on the        ``lex_page``, ``lex_table``
                       server
``/grid``              the same records in AG Grid                       ``lex_grid``
``/in-lex-app``        lex-app's own table and create form in the        ``lex_view``, ``Flow``,
                       dashboard, reacting to selection and creation     event handlers
``/calculations``      a selected record's calculation and its log       ``lex_widgets``,
                                                                         ``calculation``,
                                                                         ``calculation_log``
``/edit``              change one field of a record through lex-app      ``lex_api``
(record dashboard)     one record, for the model's ``reflex_main``       ``LexRecordState``
=====================  ================================================  ============================
"""

from __future__ import annotations

from typing import Any, ClassVar

import reflex as rx
import reflex_enterprise as rxe
import rfds
from rfds.auth import can
from rfds.patterns.exports import ExportMixin, export_menu

from lex.lex_app.reflex import (
    Flow,
    LexApiError,
    LexRecordState,
    LexUser,
    calculation,
    calculation_log,
    current_permissions,
    has_permission,
    lex_api,
    lex_grid,
    lex_page,
    lex_table,
    lex_view,
    lex_widgets,
    permission,
    run_orm,
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
    """Every model and how many rows it holds -- plain Django, run through ``run_orm``."""
    return [
        {"model": str(model._meta.verbose_name_plural).capitalize(), "rows": model._default_manager.count()}
        for model in _project_models()
    ]


async def _may_read_featured(ctx: Any) -> bool:
    """An ``auth=`` check: Keycloak grants the user read access to the featured model.

    Resolved when an event arrives, not on import: the featured model is looked
    up once Django knows the project's models.
    """
    return await has_permission(featured(), "read")(ctx)


class OverviewState(ExportMixin, rx.State):
    """The overview's figures, per viewer; the counts are also its CSV export."""

    export_entity: ClassVar[str] = "row-counts"
    counts: list[dict] = []
    total: int = 0
    resources: list[str] = []

    # Counting rows reads the database directly, so the handler checks a grant
    # first: here, read access to the featured model. Without it, Reflex
    # Enterprise refuses the event before it runs.
    @rxe.event(auth=_may_read_featured)
    async def load(self):
        """Read the counts and the viewer's Keycloak resources -- on mount, per viewer."""
        self.counts = await run_orm(_row_counts)
        self.total = sum(int(row["rows"]) for row in self.counts)
        grants = await current_permissions(self)
        self.resources = sorted({grant.get("rsname", "") for grant in grants} - {""})

    async def _export_rows(self, kind: str):
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
                OverviewState.resources,
                rx.hstack(rx.foreach(OverviewState.resources, rx.badge), wrap="wrap"),
                rx.text("None yet.", color_scheme="gray"),
            ),
            # Shown only to users Keycloak lets edit the featured model -- what
            # they see, not access control: the edit page's handler checks too.
            can(permission(model, "edit"), rfds.link("Change a record", href="/edit")),
            title="Keycloak resources you hold a permission on",
        ),
        on_mount=OverviewState.load,
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


class InLexAppState(rx.State):
    """What the framed lex-app pages told the dashboard."""

    selected: list[str] = []
    created: list[str] = []

    @rx.event
    def selection(self, payload: dict):
        """The framed table's selection changed: ``{resource, ids}``."""
        self.selected = [str(i) for i in payload.get("ids") or []]

    @rx.event
    def record_created(self, payload: dict):
        """The framed form saved a record: ``{resource, id, data}``."""
        self.created.append(str(payload.get("id")))
        return rx.toast(f"Created record {payload.get('id')}")


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


class CalculationState(rx.State):
    """The record whose calculation is shown, and what it last reported."""

    pk: str = ""
    status: str = ""

    @rx.event
    def pick(self, payload: dict):
        ids = payload.get("ids") or []
        self.pk = str(ids[0]) if ids else ""

    @rx.event
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


class EditState(rx.State):
    """One field of one record, changed as the user -- lex-app validates, audits and recalculates."""

    pk: str = ""
    field_name: str = ""
    value: str = ""
    outcome: str = ""

    @rx.event
    def set_pk(self, value: str):
        self.pk = value

    @rx.event
    def set_field_name(self, value: str):
        self.field_name = value

    @rx.event
    def set_value(self, value: str):
        self.value = value

    @rx.event
    async def save(self):
        if not (self.pk and self.field_name):
            self.outcome = "Name a record and a field."
            return
        try:
            await (await lex_api(self)).update(featured(), self.pk, {self.field_name: self.value})
        except LexApiError as exc:
            self.outcome = "Not allowed." if exc.denied else f"lex-app refused it: {exc.detail}"
            return
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


class RecordState(LexRecordState):
    """The record ``?model=&pk=`` names -- only if lex-app's API would show it to this user."""

    @rx.event
    async def load(self):
        await self.record()


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
    return rx.box(
        rx.match(
            RecordState.record_state,
            ("ok", rx.vstack(
                rx.heading(RecordState.record_title, size="5"),
                rx.text("Read through lex-app's API as you; the instance is in the handler too."),
            )),
            ("denied", rfds.inline_message("You may not see this record.", tone="warning")),
            ("missing", rfds.inline_message("This record does not exist.", tone="neutral")),
            rfds.spinner(label="Loading"),
        ),
        on_mount=RecordState.load,
    )
