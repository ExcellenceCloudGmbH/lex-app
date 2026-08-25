"""Widget specs and the manifest that carries them.

Pure on purpose: no Streamlit import, no I/O, no globals. Manifest construction
is where the fiddly validation lives -- unknown option, missing pk, a widget id
reused -- and that should be testable without a Streamlit runtime or a browser.

Validation rejects loudly rather than dropping. A widget silently absent from a
page because a key was misspelled is the failure that costs an afternoon: the
page renders, nothing errors, and the widget is simply not there.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

#: Bumped only on breaking changes. The host refuses versions it does not know
#: rather than guessing, so additive changes must stay additive.
MANIFEST_VERSION = 1

#: Widget types this producer can emit. The manifest is typed so more can be
#: added without redesigning the transport.
KNOWN_TYPES = ("calculation", "calculation_log", "calculation_log_tree")

#: Options understood per widget type. Validated per type, so ``height`` on a
#: ``calculation`` (where the key is ``log_height``) is caught rather than
#: silently ignored.
KNOWN_OPTIONS = {
    "calculation": ("show_log", "log_height", "on_status"),
    "calculation_log": ("height", "calculation_id"),
    "calculation_log_tree": ("height", "calculation_id"),
}

PK = Union[str, int]


class WidgetSpecError(ValueError):
    """A widget could not be specified. Raised at call time, not render time."""


def calculation_spec(
    widget_id: str,
    model: str,
    pk: PK,
    *,
    show_log: bool = False,
    log_height: Optional[int] = None,
    on_status: bool = False,
) -> Dict[str, Any]:
    """Build one validated calculation widget spec.

    Raising here rather than at render time is deliberate: the traceback points
    at the ``page.calculation(...)`` line the author wrote, instead of at a
    component boundary several frames away.
    """
    if not isinstance(widget_id, str) or not widget_id:
        raise WidgetSpecError("widget id must be a non-empty string")
    if not isinstance(model, str) or not model:
        raise WidgetSpecError(f"{widget_id!r}: model must be a non-empty string")
    if not isinstance(pk, (str, int)) or isinstance(pk, bool):
        # bool is an int subclass; a True pk is a mistake, not a record.
        raise WidgetSpecError(f"{widget_id!r}: pk must be a string or int, got {type(pk).__name__}")
    if log_height is not None and (not isinstance(log_height, int) or log_height <= 0):
        raise WidgetSpecError(f"{widget_id!r}: log_height must be a positive int")

    options: Dict[str, Any] = {"show_log": bool(show_log), "on_status": bool(on_status)}
    if log_height is not None:
        options["log_height"] = log_height

    return {
        "id": widget_id,
        "type": "calculation",
        "model": model,
        "pk": pk,
        "options": options,
    }


def build_manifest(widgets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble validated specs into the manifest the host consumes."""
    seen = set()
    for spec in widgets:
        wid = spec.get("id")
        if wid in seen:
            raise WidgetSpecError(
                f"duplicate widget id {wid!r} -- ids address status events back to a "
                "widget, so a duplicate would misroute them"
            )
        seen.add(wid)

        wtype = spec.get("type")
        if wtype not in KNOWN_TYPES:
            raise WidgetSpecError(
                f"{wid!r}: unknown widget type {wtype!r}; known types: {', '.join(KNOWN_TYPES)}"
            )

        allowed = KNOWN_OPTIONS[wtype]
        unknown = sorted(set(spec.get("options", {})) - set(allowed))
        if unknown:
            raise WidgetSpecError(
                f"{wid!r} (type {wtype}): unknown option(s) {', '.join(unknown)}; "
                f"known options for this type: {', '.join(allowed)}"
            )

    return {
        "version": MANIFEST_VERSION,
        "widgets": list(widgets),
        "layout": {"kind": "rows"},
    }


def calculation_log_spec(
    widget_id: str,
    model: str,
    pk: PK,
    *,
    height: Optional[int] = None,
    calculation_id: Optional[str] = None,
    tree: bool = False,
) -> Dict[str, Any]:
    """Build a validated spec for the log on its own.

    Separate from :func:`calculation_spec` because a two-pane tree wants width
    and height while a Calculate control wants a line -- cramming the former
    under the latter is the wrong shape, and it is what made the embedded log
    unreadable.
    """
    if not isinstance(widget_id, str) or not widget_id:
        raise WidgetSpecError("widget id must be a non-empty string")
    if not isinstance(model, str) or not model:
        raise WidgetSpecError(f"{widget_id!r}: model must be a non-empty string")
    if not isinstance(pk, (str, int)) or isinstance(pk, bool):
        raise WidgetSpecError(f"{widget_id!r}: pk must be a string or int, got {type(pk).__name__}")
    if height is not None and (not isinstance(height, int) or height <= 0):
        raise WidgetSpecError(f"{widget_id!r}: height must be a positive int")

    options: Dict[str, Any] = {}
    if height is not None:
        options["height"] = height
    if calculation_id is not None:
        options["calculation_id"] = calculation_id

    return {
        "id": widget_id,
        # The plain log IS the live stream -- that is what someone who just
        # pressed Calculate wants. The tree answers a different question ("what
        # was the structure of this run") and is opted into explicitly.
        "type": "calculation_log_tree" if tree else "calculation_log",
        "model": model,
        "pk": pk,
        "options": options,
    }
