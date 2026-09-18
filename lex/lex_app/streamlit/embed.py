"""
Built-in Streamlit helpers for embedding React application views.

Provides ``lex_view()`` — a single function that embeds any page from the
React frontend inside a Streamlit dashboard via an iframe, with full control
over which UI elements are shown.

Usage
-----
::

    import streamlit as st
    from lex.lex_app.streamlit.embed import lex_view

    st.set_page_config(layout="wide")
    st.title("My Dashboard")

    # Embed a table view
    lex_view("quarter")

    # Embed a create form, toolbar-free
    lex_view("quarter/create", hide_toolbar=True)

    # Side-by-side layout
    col1, col2 = st.columns(2)
    with col1:
        lex_view("fund", height=600)
    with col2:
        lex_view("investor", height=600)

    # Chain forms together: each step opens on the record the last one saved
    lex_view("investor/create", flow=(
        Flow().create("investor").create("vehicle").update("investor").table("investor")
    ))

    # Enter records one after another
    lex_view("investor/create", flow=Flow().create("investor").loop_last())
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Any, Dict, List, NamedTuple, Optional, Union

import streamlit.components.v1 as components

from lex.lex_app.streamlit._lex_view_component import render_lex_view_component

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_HEIGHT: int = 800
_DEFAULT_WIDTH: Union[int, str] = "100%"
_DEFAULT_SCROLLING: bool = True


# ---------------------------------------------------------------------------
# Flow builder — ergonomic multi-step redirect definition
# ---------------------------------------------------------------------------


class FlowError(ValueError):
    """A flow rule that could never fire."""


#: Target meaning "do not navigate -- stay on this form and clear it".
#: The React side matches the literal string; this constant exists so a typo is
#: a NameError at author time rather than a redirect that silently never happens.
STAY = "self"

#: The operations the React app actually resolves a redirect for.
#: ``delete`` is deliberately absent: the app EMITS ``lex:record_deleted`` but
#: has no delete-redirect resolver, so a ``"<resource>/delete"`` rule would be
#: accepted, serialised, shipped, and then ignored. Rejecting it here turns a
#: silent no-op into a message at the call that wrote it.
_OPERATIONS = ("create", "update")


class Ref(NamedTuple):
    """A reference to the id produced by an earlier step.

    Built with :func:`ref`; never constructed directly by callers.
    """

    name: str


def ref(name: str) -> Ref:
    """The id produced by the step that declared ``as_=name``.

    For reaching further back than the previous step::

        Flow().create("investor", as_="inv").create("vehicle").update("investor", id=ref("inv"))

    A step that only needs the id the step BEFORE it produced does not need
    this -- leave ``id`` off and it is implied.
    """
    return Ref(name)


#: Wire-format version for a flow built as a SEQUENCE of steps. Absent means
#: the v1 mapping, which is still what a `Flow({...})` or an `after_create`
#: chain emits, byte for byte.
#:
#: The frontend branches on this and degrades to its default redirect when it
#: sees a version it does not know. That matters because lex-app and the React
#: bundle ship as separate artefacts and can be a version apart: a v2 flow
#: reaching an older frontend has to do nothing rather than something wrong.
FLOW_WIRE_VERSION = 2

#: Steps that end with a saved record, and can therefore hand an id to the step
#: after them. `goto` is deliberately not one: it opens a route, it does not
#: write anything, so an implicit id across it would resolve to nothing.
_ID_PRODUCING = ("create", "update")


def _check_resource(resource: object, where: str) -> str:
    if not isinstance(resource, str) or not resource.strip():
        raise FlowError(f"{where}: resource must be a non-empty string, got {resource!r}")
    resource = resource.strip()
    if "/" in resource:
        raise FlowError(
            f"{where}: {resource!r} looks like a mapping key, not a resource. "
            f"A step takes the resource alone and gets its operation from which "
            f"builder you called; '<resource>/<operation>' belongs to the mapping "
            f"form -- Flow({{...}}) or after_create()."
        )
    return resource


class Flow(dict):
    """Where to go after a record is created or updated.

    Two ways to write one, and they produce different wire formats because they
    mean different things.

    **A sequence**, when the steps have an order::

        Flow().create("investor").create("vehicle").update("investor").table("investor")

    Each call appends a step; exactly one terminal ends it. A step takes the id
    of the record the step before it produced, so the common case needs no id at
    all. Name a step with ``as_=`` and :func:`ref` reaches it from further down.

    **A mapping**, when they do not::

        Flow({"investor/create": "/cashflow/{id}/edit"})
        Flow().after_create("investor", "/cashflow/{id}/edit")

    Each key is ``"<resource>/<operation>"`` and each value the target path,
    resolved independently every time that operation happens. Targets may
    contain ``{id}`` and ``{resource}`` (``{model}`` is an accepted alias), and
    a target of :data:`STAY` keeps the user on the form.

    The mapping form is the escape hatch: it addresses operations directly and
    can express things the sequence deliberately cannot. **The two cannot be
    mixed in one flow** -- see :meth:`create`.

    **Rules are validated when written, not when they fail to fire.** A flow
    rule that never matches produces no error and no redirect; it simply does
    nothing, in a browser, later. Every way of building a ``Flow`` -- the
    constructor, the builders, ``update()``, plain item assignment -- goes
    through the same check, so a typo cannot reach the URL by taking a
    different door in.
    """

    def __init__(self, mapping=None, **kwargs):  # noqa: D107
        super().__init__()
        # Before the mapping is processed: __setitem__ consults both.
        self._steps: List[Dict[str, Any]] = []
        self._end: Optional[Dict[str, Any]] = None
        self._names: List[str] = []
        if mapping:
            for key, target in dict(mapping).items():
                self[key] = target
        for key, target in kwargs.items():
            self[key] = target

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _validate(key: object, target: object) -> None:
        if not isinstance(key, str) or "/" not in key:
            raise FlowError(
                f"flow key {key!r} must be '<resource>/<operation>', "
                f"e.g. 'investor/create'"
            )
        resource, _, operation = key.partition("/")
        if not resource:
            raise FlowError(f"flow key {key!r} has no resource before the '/'")
        if operation not in _OPERATIONS:
            if operation == "delete":
                raise FlowError(
                    "flow key {!r}: there is no delete redirect. The app emits a "
                    "delete event (lex_view(on_delete=True)) but never navigates "
                    "on one, so this rule would be silently ignored.".format(key)
                )
            raise FlowError(
                f"flow key {key!r}: unknown operation {operation!r}; "
                f"expected one of {', '.join(_OPERATIONS)}"
            )
        if not isinstance(target, str) or not target:
            raise FlowError(f"flow target for {key!r} must be a non-empty string")

    def __setitem__(self, key, target) -> None:  # noqa: D105
        if self._steps:
            raise FlowError(
                f"cannot add the rule {key!r} to a flow built as a sequence. "
                f"A mapping rule fires whenever its operation happens; a step "
                f"fires at its position. Pick one -- they answer different "
                f"questions and a flow holding both has no single answer."
            )
        self._validate(key, target)
        super().__setitem__(key, target)

    def setdefault(self, key, target=None):  # noqa: D102
        if self._steps:
            raise FlowError(
                f"cannot add the rule {key!r} to a flow built as a sequence."
            )
        self._validate(key, target)
        return super().setdefault(key, target)

    # -- builders: the mapping form -----------------------------------------

    def after_create(self, resource: str, target: str) -> "Flow":
        """After creating a record of ``resource``, go to ``target``."""
        self[f"{resource}/create"] = target
        return self

    def after_update(self, resource: str, target: str) -> "Flow":
        """After updating a record of ``resource``, go to ``target``."""
        self[f"{resource}/update"] = target
        return self

    def after_save(self, resource: str, target: str) -> "Flow":
        """After creating **or** updating a record of ``resource``, go to ``target``.

        The common case, and writing both rules by hand is where they drift.
        """
        return self.after_create(resource, target).after_update(resource, target)

    # -- builders: the sequence form ----------------------------------------

    def _open(self, what: str) -> None:
        """Guard every append: no mixing, and nothing after the ending."""
        if dict.__len__(self):
            raise FlowError(
                f"cannot add the step {what} to a flow built as a mapping. "
                f"A step fires at its position; a mapping rule fires whenever "
                f"its operation happens. Pick one -- they answer different "
                f"questions and a flow holding both has no single answer."
            )
        if self._end is not None:
            raise FlowError(
                f"cannot add {what}: this flow already ends with "
                f"{self._end['kind']!r}. A flow has exactly one ending, and "
                f"anything written after it could never be reached."
            )

    def _bind(self, step: Dict[str, Any], as_: Optional[str]) -> None:
        if as_ is None:
            return
        if not isinstance(as_, str) or not as_.strip():
            raise FlowError(f"as_ must be a non-empty string, got {as_!r}")
        name = as_.strip()
        if name in self._names:
            raise FlowError(
                f"as_={name!r} is already used by an earlier step. Two steps "
                f"under one name means ref({name!r}) has two answers and "
                f"silently takes the later one."
            )
        self._names.append(name)
        step["as"] = name

    def _resolve_id(self, id_: object, where: str, *, implicit_ok: bool = True):
        """Normalise an id to its wire form, or ``None`` for 'the previous step'."""
        if id_ is None:
            if not implicit_ok:
                raise FlowError(f"{where}: an id is required here")
            if not self._steps:
                raise FlowError(
                    f"{where}: no id given and no step before it. An implicit id "
                    f"means 'the record the previous step made', and this is the "
                    f"first step -- pass id=... or put a create before it."
                )
            previous = self._steps[-1]["op"]
            if previous not in _ID_PRODUCING:
                raise FlowError(
                    f"{where}: no id given, but the step before it is "
                    f"{previous!r}, which saves no record. Pass id=... or "
                    f"ref(...) naming a step that does."
                )
            return None
        if isinstance(id_, Ref):
            if id_.name not in self._names:
                raise FlowError(
                    f"{where}: ref({id_.name!r}) names a step that has not been "
                    f"written yet. References point backwards -- declare it with "
                    f"as_={id_.name!r} on an earlier step."
                )
            return {"ref": id_.name}
        if isinstance(id_, bool) or not isinstance(id_, (str, int)):
            raise FlowError(f"{where}: id must be a string, an int or ref(...), got {id_!r}")
        if isinstance(id_, str) and not id_.strip():
            raise FlowError(f"{where}: id must not be blank")
        return id_

    def create(self, resource: str, *, as_: Optional[str] = None) -> "Flow":
        """Open ``resource``'s create form.

        ``as_`` names the record this step will produce, so a later step can
        reach it with :func:`ref`. The step immediately after does not need a
        name -- it takes this id by default.
        """
        self._open(f"create({resource!r})")
        step: Dict[str, Any] = {"op": "create", "res": _check_resource(resource, "create()")}
        self._bind(step, as_)
        self._steps.append(step)
        return self

    def update(self, resource=None, id=None, *, as_: Optional[str] = None, **kwargs) -> "Flow":  # type: ignore[override]
        """Open ``resource``'s edit form for one record.

        ``id`` takes three forms, and which one you mean is the whole point of
        the step: leave it off for the record the PREVIOUS step produced, pass a
        literal you knew when writing the flow, or pass ``ref("name")`` to reach
        a step further back.

        This shadows ``dict.update`` for a flow built as a sequence, which is
        the trade for keeping one class. The mapping form is unaffected: a flow
        with mapping rules in it refuses to take steps at all, so
        ``Flow({...}).update({...})`` still means the dict method -- the two
        signatures can never be live at the same time.
        """
        if not isinstance(resource, str):
            # ``dict.update`` semantics. Routed through ``self[key] = target``
            # rather than ``dict.update``, which would put rules in without
            # passing ``__setitem__`` -- the door scenario 1.325 exists to keep
            # shut, and the one this dispatch reopened.
            for key, target in dict(resource or {}, **kwargs).items():
                self[key] = target
            return None  # type: ignore[return-value]
        self._open(f"update({resource!r})")
        step: Dict[str, Any] = {"op": "update", "res": _check_resource(resource, "update()")}
        resolved = self._resolve_id(id, f"update({resource!r})")
        if resolved is not None:
            step["id"] = resolved
        self._bind(step, as_)
        self._steps.append(step)
        return self

    def goto(self, path: str) -> "Flow":
        """Open any route, as written.

        The escape hatch inside the sequence form: ``{id}`` and ``{resource}``
        interpolate exactly as they do in a mapping target. A ``goto`` saves
        nothing, so the step after it cannot take an implicit id.
        """
        self._open(f"goto({path!r})")
        if not isinstance(path, str) or not path.strip():
            raise FlowError(f"goto(): path must be a non-empty string, got {path!r}")
        self._steps.append({"op": "goto", "path": path.strip()})
        return self

    # -- terminals ----------------------------------------------------------

    def _last_resource(self, where: str) -> str:
        for step in reversed(self._steps):
            if "res" in step:
                return str(step["res"])
        raise FlowError(
            f"{where}: no resource to default to. Name one explicitly -- the "
            f"steps before it open routes rather than records."
        )

    def _end_with(self, payload: Dict[str, Any]) -> "Flow":
        self._open(f"{payload['kind']}()")
        if not self._steps:
            raise FlowError(
                f"{payload['kind']}(): a flow that is only an ending has nothing "
                f"to end. Add at least one step before it."
            )
        self._end = payload
        return self

    def table(self, resource: Optional[str] = None) -> "Flow":
        """End on ``resource``'s table. Defaults to the last step's resource."""
        res = _check_resource(resource, "table()") if resource is not None else None
        return self._end_with({"kind": "table", "res": res or self._last_resource("table()")})

    def show(self, resource: Optional[str] = None, id=None) -> "Flow":
        """End on one record's detail page.

        Defaults to the last step's resource and to the record it produced.
        """
        res = _check_resource(resource, "show()") if resource is not None else None
        payload: Dict[str, Any] = {
            "kind": "show",
            "res": res or self._last_resource("show()"),
        }
        resolved = self._resolve_id(id, "show()")
        if resolved is not None:
            payload["id"] = resolved
        return self._end_with(payload)

    def end_goto(self, path: str) -> "Flow":
        """End on any route, as written."""
        if not isinstance(path, str) or not path.strip():
            raise FlowError(f"end_goto(): path must be a non-empty string, got {path!r}")
        return self._end_with({"kind": "goto", "path": path.strip()})

    def loop(self) -> "Flow":
        """Never end: on the last step's save, start again from the first.

        Bindings are cleared on each pass -- a new iteration is a fresh run, and
        a ``ref`` resolving to the previous pass's record would be a bug that
        only shows up on the second lap.
        """
        return self._end_with({"kind": "loop"})

    def loop_last(self) -> "Flow":
        """Never end: repeat the final step.

        The shape for entering records one after another. Mechanically this is
        :data:`STAY` with a cursor that does not move, so the form clears in
        place rather than navigating.
        """
        return self._end_with({"kind": "loop_last"})

    # -- serialisation ------------------------------------------------------

    @property
    def is_program(self) -> bool:
        """Whether this flow is a sequence of steps rather than a mapping."""
        return bool(self._steps)

    def entry_path(self) -> Optional[str]:
        """The route this flow's first step opens, or ``None`` if it has none.

        A sequence already says where it begins, so the caller does not have to
        say it again::

            lex_view(flow=Flow().create("investor").create("vehicle"))

        Two statements of one route can disagree; one cannot. ``lex_view`` uses
        this when no path is given.

        ``None`` for a mapping, which has no first step -- its rules fire
        whenever their operation happens, from wherever the embed already is --
        and for anything else that cannot name a route on its own.
        """
        if not self._steps:
            return None
        step = self._steps[0]
        operation = step["op"]
        if operation == "goto":
            return str(step["path"])
        if operation == "create":
            return f"/{step['res']}/create"
        if operation == "update":
            # A first step's id can only be a literal. An implicit one has no
            # previous step to take from, and a ref can only point backwards --
            # both are already refused by `_resolve_id` at author time, so the
            # unresolvable forms cannot reach here. The check stays anyway: a
            # guess at a route is worse than leaving the path alone.
            identifier = step.get("id")
            if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
                return f"/{step['res']}/{identifier}"
        return None

    def __bool__(self) -> bool:
        """Truthy when the flow says anything at all.

        Overridden because ``dict.__bool__`` asks only about the mapping, and a
        flow built entirely from steps has an empty mapping. Left as inherited,
        ``lex_view(flow=Flow().create("x"))`` would drop the flow on the floor
        at ``if flow:`` -- silently, which is the failure mode this module
        exists to keep out of the browser.
        """
        return bool(self._steps) or dict.__len__(self) > 0

    def to_wire(self) -> Dict[str, Any]:
        """The JSON payload for the ``lex_flow`` query parameter.

        A mapping-form flow serialises to the bare mapping, exactly as it always
        has. Only a sequence carries a version, so an existing flow's URL does
        not change by a byte.
        """
        if not self._steps:
            return dict(self)
        if self._end is None:
            end = {"kind": "table", "res": self._last_resource("this flow's ending")}
        else:
            end = self._end
        return {"v": FLOW_WIRE_VERSION, "steps": list(self._steps), "end": end}


def _resolve_base_url() -> str:
    """
    Resolve the base URL of the React application.

    Priority:
    1. ``REACT_APP_URL`` environment variable (explicit override)
    2. ``LEX_FRONTEND_URL`` environment variable (framework convention)
    3. Falls back to ``http://localhost:8000``
    """
    return (
        os.getenv("REACT_APP_URL")
        or os.getenv("LEX_FRONTEND_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def lex_view(
    path: str = "",
    *,
    height: int = _DEFAULT_HEIGHT,
    width: Union[int, str] = _DEFAULT_WIDTH,
    scrolling: bool = _DEFAULT_SCROLLING,
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    redirect_after: Optional[str] = None,
    redirect_after_create: Optional[str] = None,
    redirect_after_update: Optional[str] = None,
    flow: Optional[Dict[str, str]] = None,
    serializer: Optional[str] = None,
    theme: str = "light",
    on_create: bool = False,
    on_update: bool = False,
    on_delete: bool = False,
    on_select: bool = False,
    on_navigate: bool = False,
    on_flow_step: bool = False,
    extra_params: Optional[Dict[str, str]] = None,
    base_url: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[dict]:
    """
    Embed a page from the React application inside the current Streamlit page.

    The React frontend detects embed mode via the ``embed=true`` query
    parameter **and** the ``#embed`` URL fragment, and renders the content
    without its own sidebar/appbar chrome.

    Two modes
    ---------
    * **Plain iframe (legacy).** No ``on_*`` flag and no ``serializer``
      requesting event-bearing behaviour → renders ``components.iframe``
      and returns ``None``. Existing call sites are unchanged.
    * **Bidirectional component.** At least one of ``on_create``,
      ``on_update``, ``on_select``, ``on_navigate``, ``on_flow_step`` is
      ``True`` → renders the lex_view custom component which forwards
      ``postMessage`` events from the React app back to Python.
      ``lex_view(...)`` then returns the latest event envelope (or
      ``None`` until the first event arrives).

    See ``docs/features/access-and-ui/lex_view callbacks.md`` for the
    event envelope schema and the per-type payload contracts.

    Parameters
    ----------
    path : str
        The React route to embed, e.g. ``"quarter"``, ``"fund/42"``,
        ``"investor/create"``.  A leading ``/`` is added automatically
        if missing.

        Optional when ``flow`` is a **sequence**: it already names the route
        its first step opens, and repeating it here is a second statement of
        one route that can disagree with the first::

            lex_view(flow=Flow().create("investor").create("vehicle"))

        A path given explicitly always wins, and a mapping flow names no
        starting point, so leaving both out still embeds the application root.
    height : int
        Iframe height in pixels.  Default ``800``.
    width : int | str
        Iframe width — either an integer (pixels) or a CSS string like
        ``"100%"`` or ``"50vw"``.  Default ``"100%"``.  Ignored in
        bidirectional mode (the custom component is always 100% wide).
    scrolling : bool
        Whether the iframe should be scrollable.  Default ``True``.
        Ignored in bidirectional mode.
    hide_toolbar : bool
        If ``True``, hides the local toolbar row (History, Analytics,
        Density, Views, Sidebar toggle, etc.).  Maps to the
        ``?hide_toolbar=true`` query parameter read by ``CustomList``.
    hide_actions : bool
        If ``True``, hides the top actions bar (Create button, Refresh,
        Export).  Maps to the ``?hide_actions=true`` query parameter
        read by ``CustomListActions``.
    redirect_after : str, optional
        React route to navigate to after *any* successful create or update.
        Supports ``{resource}`` and ``{id}`` template tokens.
    redirect_after_create : str, optional
        Override ``redirect_after`` for create operations only.
    redirect_after_update : str, optional
        Override ``redirect_after`` for update operations only.
    flow : dict, optional
        Multi-step redirect routing table — see ``Flow``.
    serializer : str, optional
        Name of a registered DRF serializer on the model. Forwarded as
        ``?serializer=<name>`` so the embedded list/detail uses that
        serializer to shape its response. An unknown name surfaces as
        HTTP 400 (see cluster 12h). Resolves through
        ``ModelEntryProviderMixin.get_serializer_class``.
    on_create, on_update, on_delete, on_select, on_navigate, on_flow_step : bool
        Opt-in flags for the bidirectional event channel. Setting any
        of them switches ``lex_view`` to the custom-component path and
        causes the return value to carry event dicts.

        ``on_select`` additionally forwards as ``?emit_select=true`` so
        the React side wires the AG Grid ``onSelectionChanged`` callback
        only when explicitly requested (it is opt-in because driving
        Streamlit re-runs on every grid click is expensive).
    theme : str
        Host colour scheme forwarded to the embedded app — ``"light"``
        (default) or ``"dark"``.  Sent as the ``?theme=`` query parameter
        (boot fallback) and re-announced over the host→iframe ``theme``
        postMessage in bidirectional mode.  See the *Theme handshake*
        section of ``lex_view callbacks.md``.
    extra_params : dict, optional
        Arbitrary extra query parameters forwarded to the React app.
    base_url : str, optional
        Override the React app base URL for this call only.
        By default uses ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` env vars.
    key : str, optional
        Streamlit component key — only used in bidirectional mode.
        Defaults to the resolved URL so different embeds in the same
        script get distinct component slots automatically.

    Returns
    -------
    None | dict
        ``None`` in plain-iframe mode (no callbacks requested), or in
        bidirectional mode before the first event has arrived.
        Otherwise the latest event envelope dict.

    Examples
    --------
    Basic table embed (plain iframe, no callbacks)::

        lex_view("quarter")

    React to AG Grid selection changes::

        event = lex_view("investor", on_select=True)
        if event and event["type"] == "select":
            st.write(event["payload"]["ids"])

    Request a specific serializer for the embedded list::

        lex_view("investor", serializer="InvestorWithFundSerializer")

    Multi-step workflow with creation hooks::

        event = lex_view(
            "investor",
            on_create=True,
            flow=Flow().after_create("investor", "/cashflow/{id}/edit"),
        )
        if event and event["type"] == "create":
            st.toast(f"Created investor #{event['payload']['id']}")
    """
    resolved_base = base_url.rstrip("/") if base_url else _resolve_base_url()

    # Normalise path. A sequence flow names its own first step, so an omitted
    # path is taken from it rather than defaulting to the application's root --
    # which would drop the user somewhere the flow does not begin.
    if not path and isinstance(flow, Flow):
        path = flow.entry_path() or ""
    if path and not path.startswith("/"):
        path = f"/{path}"

    raw_url = f"{resolved_base}{path}"

    # ── Parse & build query params ──
    parsed = urllib.parse.urlparse(raw_url)
    params: Dict[str, Any] = urllib.parse.parse_qs(parsed.query)

    # Core embed flag
    params["embed"] = ["true"]

    # Visibility toggles
    if hide_toolbar:
        params["hide_toolbar"] = ["true"]
    if hide_actions:
        params["hide_actions"] = ["true"]

    # Post-operation redirect overrides
    if redirect_after:
        params["redirect_after"] = [redirect_after]
    if redirect_after_create:
        params["redirect_after_create"] = [redirect_after_create]
    if redirect_after_update:
        params["redirect_after_update"] = [redirect_after_update]

    # Flow routing table — JSON-encoded, takes priority over flat params.
    #
    # `to_wire` decides the format from the flow's own content: a mapping
    # serialises to the bare mapping it always did, a sequence carries a
    # version and its steps. A plain dict passed here is a mapping by
    # definition and is sent as-is.
    if flow:
        wire = flow.to_wire() if isinstance(flow, Flow) else dict(flow)
        params["lex_flow"] = [json.dumps(wire, separators=(",", ":"))]
        # The cursor starts the sequence, and its PRESENCE is what tells the
        # frontend a sequence is live. Emitting 0 here rather than letting the
        # frontend default to it is what lets an absent cursor mean "the user
        # left the flow" instead of "start again from the top".
        if isinstance(flow, Flow) and flow.is_program:
            params["lex_step"] = ["0"]

    # Serializer override (per docs/features/access-and-ui/lex_view callbacks.md)
    if serializer:
        params["serializer"] = [serializer]

    # Theme handshake (host → iframe): boot fallback via query param; the
    # custom-component shim additionally posts a `theme` message on load.
    if theme not in ("light", "dark"):
        raise ValueError(f"lex_view(theme=...) must be 'light' or 'dark', got {theme!r}")
    params["theme"] = [theme]

    # Event opt-in flags forwarded to the React bridge. The React side
    # only attaches handlers / emits events for the flags it sees here,
    # so off-by-default cost stays zero for plain iframe embeds.
    if on_create:
        params["emit_create"] = ["true"]
    if on_update:
        params["emit_update"] = ["true"]
    if on_delete:
        params["emit_delete"] = ["true"]
    if on_select:
        params["emit_select"] = ["true"]
    if on_navigate:
        params["emit_navigate"] = ["true"]
    if on_flow_step:
        params["emit_flow_step"] = ["true"]

    # Extra user-supplied params
    if extra_params:
        for key_, value in extra_params.items():
            params[key_] = [str(value)]

    new_query = urllib.parse.urlencode(params, doseq=True)

    # ── Ensure #embed fragment ──
    fragment = parsed.fragment
    if "embed" not in (fragment or ""):
        fragment = f"{fragment}#embed" if fragment else "embed"

    final_url = urllib.parse.urlunparse(
        parsed._replace(query=new_query, fragment=fragment)
    )

    logger.debug("lex_view → %s", final_url)
    # ── Render ──
    callbacks_requested = any(
        (on_create, on_update, on_delete, on_select, on_navigate, on_flow_step)
    )

    if callbacks_requested:
        # Bidirectional path: custom component returns the latest event.
        # Origin used to gate inbound postMessage events to the resolved
        # frontend base. ``urlparse(resolved_base)`` keeps scheme+netloc
        # only — the React app's window.location.origin must match.
        parsed_base = urllib.parse.urlparse(resolved_base)
        expected_origin = (
            f"{parsed_base.scheme}://{parsed_base.netloc}"
            if parsed_base.scheme and parsed_base.netloc
            else None
        )
        return render_lex_view_component(
            url=final_url,
            height=height,
            expected_origin=expected_origin,
            key=key,
        )

    # Legacy iframe path. Streamlit's components.iframe accepts width as
    # int (pixels) or str (CSS value); pass it through directly.
    width_arg: Any = width
    if isinstance(width, str) and width.isdigit():
        width_arg = int(width)

    components.iframe(final_url, height=height, width=width_arg, scrolling=scrolling)
    return None
