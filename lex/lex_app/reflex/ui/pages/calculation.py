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
from lex.lex_app.reflex.ui.pages import forms, kinds, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError, Part, called, say, shown

#: The fields of every lex-app model and of every calculation, which a form does not set.
_MANAGED = ("is_calculated", "created_at", "created_by", "edited_at", "edited_by")


def _settable(model: Any) -> dict:
    """The model's fields a record is created with: concrete, editable, not the key, not lex-app's own."""
    return {field.name: field for field in model._meta.concrete_fields
            if field.editable and not field.primary_key and not field.auto_created and field.name not in _MANAGED
            and not field.name.startswith("_")}


#: A run's status as its badge says it: the words, and RFDS's tone.
_BADGES = {
    runtime.NOT_CALCULATED: ("Not started", "neutral"), runtime.IN_PROGRESS: ("Running", "info"),
    runtime.SUCCESS: ("Done", "success"), runtime.ERROR: ("Failed", "danger"),
    runtime.CANCELLED: ("Stopped", "neutral"), runtime.ABORTED: ("Interrupted", "warning"),
}


def _status_column() -> Any:
    """The past runs' status column: the run's status in the badge's words and tones."""
    from lex.lex_app.reflex.ui.pages import tables

    found = tables.column("is_calculated", label="Status", kind="status",
                          tones={word: tone for word, (_, tone) in _BADGES.items()})
    return dataclasses.replace(found, words=tuple((word, text) for word, (text, _) in _BADGES.items()))


def _field_of(value: Any) -> Any:
    """The Django field ``value`` names when it is a model's field as the class holds it (``Model.name``)."""
    from django.db import models

    if isinstance(value, (type, rx.Var, Part)) or not hasattr(value, "__get__"):
        return None
    field = getattr(value, "field", None)
    return field if isinstance(field, models.Field) and getattr(field, "model", None) is not None else None


def _whole(field: Any) -> bool:
    from django.db import models

    return isinstance(field, (models.IntegerField, models.BigIntegerField, models.SmallIntegerField,
                              models.PositiveIntegerField, models.PositiveSmallIntegerField, models.AutoField))


def _kind(field: Any) -> str:
    """How a fact shows the model's ``field``: the kind its Django field implies."""
    from django.db import models

    if isinstance(field, models.BooleanField):
        return "bool"
    if _whole(field) or isinstance(field, (models.DecimalField, models.FloatField)):
        return "number"
    if isinstance(field, models.DateTimeField):
        return "datetime"
    if isinstance(field, models.DateField):
        return "date"
    return "text"


def _shown_kind(one: Any, field: Any) -> str:
    """How the run's view shows an input: as its form field's kind, a choice as the value chosen."""
    kind = one.is_a
    return "text" if kind in ("choice", "choices") else kind


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

    #: The page's state and its load: lex-app's, made when the page is built (:meth:`_bind`).
    state: Any = dataclasses.field(default=None, init=False)
    load: Any = dataclasses.field(default=None, init=False)

    def _bind(self, ctx: Build) -> None:
        """The page's state, whose handlers require what the page does: ``ctx.requires``, the ``requires=``
        of the ``@ui.page`` being built. Outside a ``@ui.page`` no page's requirements are known, and the
        handlers would guard nothing: refused."""
        if ctx.requires is None or ctx.route is None:
            raise ctx.fail("`ui.calculation` is built outside a `@ui.page`, so no page's `requires=` is known, and "
                           "its handlers -- lex-app's, which create records and start runs -- would guard nothing.",
                           "Return it from a page function: `@ui.page(\"/reports/regions\", "
                           "requires=ui.read(RegionReport))`.")
        every = [*self.fields, *(one for step in self.steps for one in getattr(step, "fields", ()))]
        keys = tuple(getattr(one, "key", "") for one in every)
        requires = tuple(ctx.requires)
        identity = (str(self.title), tuple((getattr(one, "key", ""), getattr(one, "is_a", ""),
                                            bool(getattr(one, "required", False))) for one in every), requires)
        self.state = runtime.state_for(runtime.Plan(self.model, keys, requires, ctx.route), identity)
        self.load = self.state.open

    def loads(self) -> tuple[list, list]:
        if self.load is None:
            raise PageError(f"ui.calculation({self.model.__name__}): its load is known once the page is built, "
                            "and it is built by a @ui.page: return it from a page function.")
        return [self.load], []

    def build(self, ctx: Build) -> rx.Component:
        self._bind(ctx)
        return super().build(ctx)

    def view_crumb(self) -> tuple:
        """The run's view as one more breadcrumb under the page: shown while a run is open, its name."""
        S = self.state
        return S.run_id != "", rx.cond(S.run_name != "", S.run_name, S.run_id)

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
    def _history(self, ctx: Build) -> Any:
        """The past runs: the author's table over the model, with the status, a link to each run, newest first."""
        from lex.lex_app.reflex.ui.pages import tables

        name = self.model.__name__
        at = ctx.at("history")
        example = f"`history=ui.table({name}, columns=[...])`"
        if not isinstance(self.history, tables.Table) or self.history.source is not self.model:
            got = (f"a table of {getattr(self.history.source, '__name__', self.history.source)}"
                   if isinstance(self.history, tables.Table) else called(self.history))
            raise at.fail(f"got {got}; the past runs are {name}'s.", f"Write {example}.")
        if self.history.open is not None:
            raise at.fail("the table has `open=`; each past run opens its own view on this page.", "Leave `open=` out.")
        if self.history.where is not None:
            raise at.fail("the table has `where=`; the past runs are the model's, as the user may see them.",
                          "Leave `where=` out: the model's permission_list and permission_read decide.")
        columns = list(self.history.columns)
        if "is_calculated" not in [getattr(one, "key", one) for one in columns]:
            columns.append(_status_column())
        key = self.model._meta.pk.name
        return dataclasses.replace(self.history, columns=tuple(columns),
                                   row_link=f"{ctx.route or '/'}?{runtime.RUN}={{{key}}}", ordering=f"-{key}")

    def _new(self, ctx: Build) -> rx.Component:
        """The form for a new run, to users who may create one; under it, the past runs."""
        import rfds

        form = forms.Form(self.title, None, self.fields, self.steps, self.state.calculate, (), self.actions)
        whole = [shown(ctx, form._page(ctx), guard.create(self.model))]
        if self.history is not None:
            history = self._history(ctx)
            self.built_history = history
            at = dataclasses.replace(ctx.at("history"), caption="Past runs", headed=True, under_load=True)
            whole.append(rfds.page(rfds.section(history.build(at), title=say("Past runs"))))
        return rx.fragment(*whole)

    def _value(self, field: Any) -> Any:
        """This run's value of the model's ``field``, typed for how it is shown; a foreign key's related record's name."""
        from lex.lex_app.reflex.data import shown_field

        if shown_field(field) != field.name:
            return self.state.run[shown_field(field)].to(str)
        value = self.state.run[field.name]
        kind = _kind(field)
        if kind == "number":
            return value.to(int) if _whole(field) else value.to(float)
        return value.to(bool) if kind == "bool" else value.to(str)

    def _swap(self, ctx: Build, value: Any, top: bool = False) -> Any:
        """``value`` with each of the model's fields (``RegionReport.orders``) made this run's value of it.

        ``top``: a slot's own item, where a field alone is a fact with the model's label.
        """
        from django.db import models

        field = _field_of(value)
        if field is not None:
            if field.model is not self.model and not issubclass(self.model, field.model):
                raise ctx.fail(f"`{field.model.__name__}.{field.name}` is a field of {field.model.__name__}; this page is "
                               f"about {self.model.__name__}.",
                               f"A run's value is written as the model's field: `{self.model.__name__}.<field>`. For "
                               f"records of another model, give the section a `ui.table({field.model.__name__}, "
                               f"where={{...: {self.model.__name__}.id}})`.")
            label = str(field.verbose_name).capitalize()
            if top and isinstance(field, models.FileField):      # the run's file: a button that downloads it
                return vocab.button(label, self.state.download, field.name,
                                    visible=self.state.run[field.name].to(str) != "")
            if top:
                return vocab.Fact(label, self._value(field), _kind(field))
            return self._value(field)
        if isinstance(value, Part) and dataclasses.is_dataclass(value):
            changed = {one.name: self._swap(ctx, getattr(value, one.name)) for one in dataclasses.fields(value)
                       if one.init}
            return dataclasses.replace(value, **changed)
        if isinstance(value, (list, tuple)):
            return type(value)(self._swap(ctx, one, top) for one in value)
        if isinstance(value, dict):
            return {key: self._swap(ctx, one) for key, one in value.items()}
        return value

    def _result(self, ctx: Build) -> list:
        """What a run that succeeded shows: ``result=``, with the run's values in place of the model's fields."""
        at = dataclasses.replace(ctx.at("result"), under_load=True, caption=self.title, headed=True)
        given = self._swap(at, list(self.result), top=True)
        return forms.Form(self.title, None, (), (), None, tuple(given))._result(at)

    def _inputs_shown(self, ctx: Build) -> rx.Component:
        """The run's inputs, as facts labelled as the form's fields."""
        by_name = {field.name: field for field in self.model._meta.concrete_fields}
        every = [*self.fields, *(one for step in self.steps for one in step.fields)]
        facts = [vocab.Fact(one.label if one.label is not None else one.key.replace("_", " ").capitalize(),
                            self._value(by_name[one.key])
                            if one.is_a not in ("choice", "choices", "text") or by_name[one.key].is_relation
                            else self.state.run[one.key].to(str),
                            _shown_kind(one, by_name[one.key]))
                 for one in every]
        return vocab.facts(ctx.at("fields"), facts)

    def _run(self, ctx: Build) -> rx.Component:
        """One run, as it stands: its status, its inputs; its progress, its result or its error; its log."""
        import rfds

        from lex.lex_app.reflex.ui import runs
        from lex.lex_app.reflex.ui.state import State

        S = self.state
        status = S.run_status
        badge = rx.match(status, *((word, rfds.lozenge(say(text), tone=tone)) for word, (text, tone) in _BADGES.items()),
                         rfds.lozenge(say("Not started"), tone="neutral"))
        header = kinds._rfds(ctx, lambda: rfds.page_header(title=self._title(ctx), path=ctx.route or "/",
                                                           subtitle=S.run_name, status=badge,
                                                           back_href=ctx.route or "/"))
        note = State.ui_progress_message.get(runs.key_of(S.follow), "")
        # The progress part follows the run while it shows: it starts the job that reads the record.
        stop = vocab.button("Stop", S.stop, confirm="Stop this run?", visible=guard.edit(self.model))
        progress = rx.box(rfds.stack(rfds.progress_bar(None, label=rx.cond(note != "", note, say("Running")),
                                                       indeterminate=True), stop.build(ctx.at("stop"))),
                          on_mount=S.follow, width="100%")
        problem = rx.match(
            status,
            (runtime.ERROR, rfds.inline_message(rx.cond(S.run_error != "", S.run_error,
                                                        say("The calculation failed. Its log says what happened.")),
                                                title=say("The calculation failed"), tone="danger")),
            (runtime.CANCELLED, rfds.inline_message(say("The calculation was stopped before it finished."),
                                                    tone="warning")),
            (runtime.ABORTED, rfds.inline_message(say("The calculation was interrupted: lex-app found it unfinished "
                                                      "after a restart. Start it again."), tone="warning")),
            rx.fragment())
        log = rx.cond(S.run_log.length() > 0, rfds.section(
            rfds.stack(rx.foreach(S.run_log, lambda entry: rfds.safe_markdown(entry))),
            title=say("Log"), collapsible=True, default_open=False))
        return rfds.page(header, self._inputs_shown(ctx), rx.cond(status == runtime.IN_PROGRESS, progress), problem,
                         rx.cond(status == runtime.SUCCESS, rfds.stack(*self._result(ctx))), log)

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
    The form is shown to users who may create the model. The page's handlers are lex-app's, so the
    page's ``requires=`` -- its permissions and a project's ``@ui.rule`` -- is theirs too: a user the
    page refuses cannot send its events either. It is a page of its own: built anywhere but a
    ``@ui.page`` (a ``@ui.dashboard``) it is refused, for its handlers would guard nothing there.
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
