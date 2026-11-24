"""``ui.calculation``: the page of a lex-app calculation model -- its inputs, its runs, their results.

One page, two views: the form for a new run with the past runs under it, and -- the same page with
``?run=<key>`` -- one run as it stands: running, done with its result, or failed with its error. The
page's state and handlers are lex-app's (:mod:`lex.lex_app.reflex.ui.calculations`); every read and
write is the signed-in user's, through lex-app's API.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui import calculations as runtime
from lex.lex_app.reflex.ui import guard
from lex.lex_app.reflex.ui.pages import forms, kinds
from lex.lex_app.reflex.ui.pages.parts import Build, called, say, shown

#: The fields of every lex-app model and of every calculation, which a form does not set.
_MANAGED = ("is_calculated", "created_at", "created_by", "edited_at", "edited_by")


def _settable(model: Any) -> dict:
    """The model's fields a record is created with: concrete, editable, not the key, not lex-app's own."""
    return {field.name: field for field in model._meta.concrete_fields
            if field.editable and not field.primary_key and not field.auto_created and field.name not in _MANAGED
            and not field.name.startswith("_")}


def _needed(field: Any) -> bool:
    """Whether lex-app refuses a create that leaves ``field`` out: no default, and neither null nor blank."""
    return not (field.has_default() or field.null or field.blank)


@dataclasses.dataclass(eq=False)
class Calculation(kinds.Kind):
    model: Any
    title: Any
    fields: tuple = ()
    steps: tuple = ()
    result: tuple = ()
    history: Any = None
    actions: tuple = ()

    def __post_init__(self) -> None:
        every = [*self.fields, *(one for step in self.steps for one in getattr(step, "fields", ()))]
        keys = tuple(getattr(one, "key", "") for one in every)
        identity = (str(self.title), tuple((getattr(one, "key", ""), getattr(one, "is_a", ""),
                                            bool(getattr(one, "required", False))) for one in every))
        self.state = runtime.state_for(runtime.Plan(self.model, keys), identity)
        self.load = self.state.open

    def loads(self) -> tuple[list, list]:
        return [self.load], []

    # -- the checks ---------------------------------------------------------------------------------
    def _inputs(self, ctx: Build) -> None:
        """Each field names a field of the model a record is created with; every field the model needs is there."""
        settable = _settable(self.model)
        name = self.model.__name__
        every = [*self.fields, *(one for step in self.steps for one in getattr(step, "fields", ()))]
        for one in every:
            key = getattr(one, "key", None)
            if key is not None and key not in settable:
                raise ctx.at("fields").fail(
                    f"field `{key}` is not a field of {name} that a new record is given.",
                    f"A calculation's fields are its model's: {', '.join(sorted(settable)) or 'none'}.")
        given = {getattr(one, "key", None) for one in every}
        missing = [key for key, field in settable.items() if _needed(field) and key not in given]
        if missing:
            raise ctx.at("fields").fail(
                f"{name} needs `{missing[0]}` when a record is created, and the form has no field for it.",
                f"Add `ui.field(\"{missing[0]}\")`, or give the model's field a default.")

    # -- the views ----------------------------------------------------------------------------------
    def _new(self, ctx: Build) -> rx.Component:
        """The form for a new run, to users who may create one."""
        form = forms.Form(self.title, None, self.fields, self.steps, self.state.calculate, (), self.actions)
        return shown(ctx, form._page(ctx), guard.create(self.model))

    def _run(self, ctx: Build) -> rx.Component:
        """One run, as it stands."""
        import rfds

        header = kinds._rfds(ctx, lambda: rfds.page_header(title=self._title(ctx), path=ctx.route or "/",
                                                           subtitle=self.state.run_name, back_href=ctx.route or "/"))
        return rfds.page(header, rfds.lozenge(self.state.run_status))

    def _page(self, ctx: Build) -> rx.Component:
        from lex.lex_app.reflex.ui.pages.vocab import Button, Link

        self._inputs(ctx)
        for index, part in enumerate(self.actions):
            if not isinstance(part, (Button, Link)):
                raise ctx.at(f"actions[{index}]").fail(f"got {called(part)}.", "`actions=` holds `ui.button(...)`.")
        content = rx.cond(self.state.run_id != "", self._run(ctx), self._new(ctx))
        return kinds.states(ctx, self.load, content, "rect")


def calculation(model: Any, *, title: Any, fields: Any = (), steps: Any = (), result: Any = (), history: Any = None,
                actions: Any = ()) -> Calculation:
    """The page of a lex-app calculation model: a form for a run's inputs, the run while it goes, its result.

    ::

        @ui.page("/reports/regions", nav="Reports", requires=ui.read(RegionReport))
        def region_reports():
            return ui.calculation(
                RegionReport,
                title="Region report",
                fields=[ui.field("region", options=["north", "south"], required=True),
                        ui.field("year", kind="year", required=True)],
                result=[RegionReport.orders, ui.fact("Revenue", RegionReport.revenue, kind="money")],
                history=ui.table(RegionReport, columns=["region", "year", "created_by"]),
            )

    ``fields=`` (or ``steps=`` for a wizard) are written as a form's; each key names a field of the
    model, which the value is written to. The button reads **Calculate**: it creates the record
    through lex-app's API as the user -- the model's ``permission_create``, its validation, its audit
    entry -- and starts its run as lex-app's Calculate button does. The browser then goes to the run:
    the same page with ``?run=<key>``, which follows the run until it ends, whether lex-app runs it
    in its own process or on a Celery worker. A refusal of the API is the form's, above the button.
    The form is shown to users who may create the model.
    """
    from django.db import models

    from lex.core.models.CalculationModel import CalculationModel

    if not (isinstance(model, type) and issubclass(model, models.Model)):
        raise TypeError(f"ui.calculation() takes the calculation model first: ui.calculation(RegionReport, title=...); "
                        f"got {called(model)}")
    if not issubclass(model, CalculationModel):
        raise TypeError(f"ui.calculation({model.__name__}): {model.__name__} is not a calculation model. A calculation's "
                        "page is over a model that inherits from CalculationModel; a record of another model is "
                        "entered with ui.form(...).")
    if bool(kinds._many(fields)) == bool(kinds._many(steps)):
        raise TypeError(f"ui.calculation({model.__name__}) takes `fields=[ui.field(...)]`, or `steps=[ui.step(...)]` for a "
                        "wizard: one of them.")
    return Calculation(model, title, kinds._many(fields), kinds._many(steps), kinds._many(result), history,
                       kinds._many(actions))
