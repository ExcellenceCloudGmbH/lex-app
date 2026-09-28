"""Which dashboard ``/`` shows -- decided by the query string, as in Streamlit.

The contract is the one ``lex/streamlit_app.py`` answers, so a frontend that
knows how to open a Streamlit dashboard knows how to open this one:

========================  ====================================================
``?model=fund&pk=42``     the record's dashboard: ``Fund.reflex_main()``
``?model=fund``           the table's dashboard: ``Fund.reflex_class_main()``
neither                   the project's own: ``<repo>/_reflex_structure.py``
                          ``main()``
========================  ====================================================

Streamlit renders by running Python per request, so it can hand
``streamlit_main`` the record itself. Reflex compiles every page to the browser
ahead of time, so the hooks here are classmethods that *return a component*,
and the record is read by the component's own state, in an event handler, with
:func:`current_record`.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from types import ModuleType
from typing import Any, Callable

import reflex as rx
import reflex_enterprise as rxe
from reflex_enterprise.auth import User

from lex.lex_app.reflex.auth import LexUser
from lex.lex_app.reflex.django_orm import run_orm

logger = logging.getLogger(__name__)

MODEL_PARAM = "model"
PK_PARAM = "pk"

#: The query parameter lex-app adds when it frames a dashboard in its own
#: chrome, and the one that hides the sign-out control. Both are the contract
#: lex-app already uses for Streamlit pages (``lex/lex_app/streamlit/sidebar.py``
#: and ``lex/streamlit_app.py``), so a framed page behaves the same whichever
#: framework drew it.
EMBED_PARAM = "lex_embed"
LOGOUT_PARAM = "is_logout_enabled"

RECORD_HOOK = "reflex_main"
TABLE_HOOK = "reflex_class_main"

#: What ``/`` resolves to. ``""`` while the page is still deciding.
VIEW_STRUCTURE = "structure"
VIEW_TABLE = "table"
VIEW_RECORD = "record"
VIEW_MISSING_MODEL = "missing_model"
VIEW_MISSING_RECORD = "missing_record"
VIEW_UNSUPPORTED_RECORD = "unsupported_record"
VIEW_UNSUPPORTED_TABLE = "unsupported_table"

#: The same words ``lex/streamlit_app.py`` uses, so a reader of either dashboard
#: meets one set of messages.
NO_RECORD_DASHBOARD = "No instance-level visualization available for this model."
NO_TABLE_DASHBOARD = "No class-level visualization available for this model."

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "n", "off"}


def _project_app_label() -> str:
    """The Django app the project's models live in -- its repo name."""
    from lex.lex_app.settings import repo_name

    return repo_name


def _lookup_model(name: str, app_label: str) -> type | None:
    """The project model ``name`` names (case-insensitively), or ``None``."""
    from django.apps import apps

    try:
        return apps.get_model(app_label, name)
    except (LookupError, ValueError):
        return None


def defines_hook(model: type, hook: str) -> bool:
    """Whether ``model`` supplies its own ``hook``, rather than LexModel's default.

    A model with no such attribute at all -- a plain Django model -- does not.
    A LexModel that never overrode it does not either: the default renders the
    "no visualization" notice, which the page draws once for every such model
    instead of compiling a copy per model.
    """
    implementation = getattr(model, hook, None)
    if implementation is None:
        return False
    from lex.core.models.LexModel import LexModel

    default = getattr(LexModel, hook, None)
    own = getattr(implementation, "__func__", implementation)
    return default is None or own is not getattr(default, "__func__", default)


def dashboard_models(app_label: str | None = None) -> dict[str, type]:
    """The project's models, keyed by the lowercase name ``?model=`` carries."""
    from django.apps import apps

    try:
        config = apps.get_app_config(app_label or _project_app_label())
    except LookupError:
        return {}
    return {model._meta.model_name: model for model in config.get_models()}


def _record_exists(model: type, pk: str) -> bool:
    """Whether ``model`` has a row with primary key ``pk``; a malformed pk has none."""
    from django.core.exceptions import ValidationError

    try:
        return model._default_manager.filter(pk=pk).exists()
    except (ValueError, TypeError, ValidationError):
        return False


async def resolve_view(
    model_param: str | None,
    pk_param: str | None,
    *,
    app_label: str | None = None,
) -> tuple[str, str, str]:
    """Decide what ``/`` shows for a query string: ``(view, model, pk)``.

    ``model`` comes back as the model's own lowercase name, which is what the
    page matches on. The order of the checks is ``lex/streamlit_app.py``'s: an
    unknown model first, then a missing record, then a model that cannot draw
    a dashboard at all.
    """
    model_name = (model_param or "").strip()
    pk = (pk_param or "").strip()
    if not model_name:
        return VIEW_STRUCTURE, "", ""

    model = _lookup_model(model_name, app_label or _project_app_label())
    if model is None:
        return VIEW_MISSING_MODEL, model_name, pk
    model_name = model._meta.model_name

    if pk:
        if not await run_orm(_record_exists, model, pk):
            return VIEW_MISSING_RECORD, model_name, pk
        if getattr(model, RECORD_HOOK, None) is None:
            return VIEW_UNSUPPORTED_RECORD, model_name, pk
        return VIEW_RECORD, model_name, pk

    if getattr(model, TABLE_HOOK, None) is None:
        return VIEW_UNSUPPORTED_TABLE, model_name, ""
    return VIEW_TABLE, model_name, ""


async def current_record(state: rx.State, model: type | None = None) -> Any | None:
    """The record the page's ``?model=&pk=`` names, read through the ORM.

    The Reflex counterpart of the ``self`` a ``streamlit_main`` receives. Call
    it from an event handler of the dashboard's own state -- usually the one
    its component runs on mount::

        class FundDashboard(rx.State):
            name: str = ""

            @rx.event
            async def load(self):
                fund = await current_record(self)
                self.name = fund.name if fund else ""

    ``model`` pins the class, for a dashboard shared by several models' pages;
    left out, it is the model the URL names. ``None`` when the URL names no
    record or the record does not exist.
    """
    params = state.router.url.query_parameters
    pk = (params.get(PK_PARAM) or "").strip()
    if not pk:
        return None
    if model is None:
        model = _lookup_model((params.get(MODEL_PARAM) or "").strip(), _project_app_label())
        if model is None:
            return None

    def fetch():
        from django.core.exceptions import ValidationError

        try:
            return model._default_manager.filter(pk=pk).first()
        except (ValueError, TypeError, ValidationError):
            return None

    return await run_orm(fetch)


def _query_flag(params: Any, name: str) -> str | None:
    value = params.get(name)
    return None if value is None else str(value).strip().lower()


class LexDashboardState(rx.State):
    """What the dashboard route is showing, resolved once the viewer is known.

    ``view``, ``model`` and ``pk`` are protected like any other state, and are
    delivered after sign-in -- :meth:`resolve` runs behind the page guard. The
    two chrome flags are public: they only restate the URL.
    """

    view: str = ""
    model: str = ""
    pk: str = ""

    @rxe.var(auth=False)
    def embedded(self) -> bool:
        """True when lex-app frames the page in its own chrome (``?lex_embed=1``)."""
        return _query_flag(self.router.url.query_parameters, EMBED_PARAM) in _TRUTHY

    @rxe.var(auth=False)
    def logout_enabled(self) -> bool:
        """False only when the URL turns the sign-out control off."""
        return _query_flag(self.router.url.query_parameters, LOGOUT_PARAM) not in _FALSY

    @rxe.event
    async def resolve(self):
        """Resolve the query string into the view the page shows."""
        params = self.router.url.query_parameters
        self.view, self.model, self.pk = await resolve_view(
            params.get(MODEL_PARAM), params.get(PK_PARAM)
        )


def _notice(message: Any) -> rx.Component:
    return rx.callout(message, icon="info", width="100%")


def _problem(message: Any) -> rx.Component:
    return rx.callout(message, icon="triangle_alert", color_scheme="red", width="100%")


def _resolving() -> rx.Component:
    return rx.center(rx.spinner(size="3"), width="100%", min_height="40vh")


def topbar() -> rx.Component:
    """The signed-in user and the way out, for a dashboard lex-app is not framing."""
    return rx.hstack(
        rx.spacer(),
        rx.vstack(
            rx.text(LexUser.display_name, weight="medium", size="2"),
            rx.cond(
                User.email != LexUser.display_name,
                rx.text(User.email, size="1", color_scheme="gray"),
            ),
            spacing="0",
            align="end",
        ),
        rx.cond(
            LexDashboardState.logout_enabled,
            rx.button("Sign out", on_click=User.logout, variant="soft", size="2"),
        ),
        align="center",
        spacing="3",
        width="100%",
        padding_y="0.5em",
    )


def structure_main(structure: ModuleType | None) -> rx.Component:
    """The project's own dashboard: ``_reflex_structure.main()``, or a pointer to it."""
    main = getattr(structure, "main", None) if structure is not None else None
    if main is None:
        return _notice(
            "This project has no Reflex dashboard yet. Add `_reflex_structure.py` "
            "with a `main()` that returns a component to show one here."
        )
    if not callable(main):
        raise TypeError(
            f"{structure.__name__}.main must be a function returning a Reflex component."
        )
    component = main()
    if not isinstance(component, rx.Component):
        raise TypeError(
            f"{structure.__name__}.main() must return a Reflex component, got "
            f"{type(component).__name__}. Build the page and return it -- the "
            "module's main() is called once, when the app compiles."
        )
    return component


def _hook_component(model: type, hook: str) -> rx.Component:
    # The page is compiled once, with no record in hand, so an instance method
    # -- the shape `streamlit_main` has -- cannot be called here at all. Say so,
    # rather than letting it fail as a missing `self`.
    if not isinstance(inspect.getattr_static(model, hook), (classmethod, staticmethod)):
        raise TypeError(
            f"{model.__name__}.{hook} must be a @classmethod returning a Reflex "
            "component. Reflex compiles the page ahead of time, so there is no "
            "record to call it on; read the record in the component's state with "
            "current_record()."
        )
    component = getattr(model, hook)()
    if not isinstance(component, rx.Component):
        raise TypeError(
            f"{model.__name__}.{hook}() must return a Reflex component, got "
            f"{type(component).__name__}."
        )
    return component


def _dispatch(cases: list[tuple[str, rx.Component]], fallback: rx.Component) -> rx.Component:
    if not cases:
        return fallback
    return rx.match(LexDashboardState.model, *cases, fallback)


def dashboard_page(
    structure: ModuleType | None,
    models: Callable[[], dict[str, type]] | None = None,
) -> Callable[[], rx.Component]:
    """The component function for ``/``: every dashboard, chosen at run time.

    Evaluated once, when the app compiles. Each model that supplies a hook
    contributes its component; which one is on screen follows ``view`` and
    ``model``, which :meth:`LexDashboardState.resolve` sets from the URL.
    ``models`` defaults to :func:`dashboard_models`.
    """

    def index() -> rx.Component:
        project_models = (models or dashboard_models)()
        record_cases = [
            (name, _hook_component(model, RECORD_HOOK))
            for name, model in sorted(project_models.items())
            if defines_hook(model, RECORD_HOOK)
        ]
        table_cases = [
            (name, _hook_component(model, TABLE_HOOK))
            for name, model in sorted(project_models.items())
            if defines_hook(model, TABLE_HOOK)
        ]
        return rx.vstack(
            rx.cond(LexDashboardState.embedded, rx.fragment(), topbar()),
            rx.match(
                LexDashboardState.view,
                (VIEW_RECORD, _dispatch(record_cases, _notice(NO_RECORD_DASHBOARD))),
                (VIEW_TABLE, _dispatch(table_cases, _notice(NO_TABLE_DASHBOARD))),
                (VIEW_STRUCTURE, structure_main(structure)),
                (VIEW_MISSING_MODEL, _problem(f"Model '{LexDashboardState.model}' not found")),
                (VIEW_MISSING_RECORD, _problem(f"Object with ID {LexDashboardState.pk} not found")),
                (VIEW_UNSUPPORTED_RECORD, _problem("This model doesn't support visualization")),
                (
                    VIEW_UNSUPPORTED_TABLE,
                    _problem("This model doesn't support class-level visualization"),
                ),
                _resolving(),
            ),
            width="100%",
            spacing="4",
            padding="1em",
        )

    return index


def load_structure(app_label: str | None = None) -> ModuleType | None:
    """``<repo>/_reflex_structure.py``, imported; ``None`` when the project has none.

    Only absence is quiet. A structure module that exists and fails to import
    raises, because a dashboard that silently turns into the placeholder is
    much harder to debug than the traceback.
    """
    module_name = f"{app_label or _project_app_label()}._reflex_structure"
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        logger.debug("No %s module; / shows the placeholder.", module_name)
        return None
    return importlib.import_module(module_name)
