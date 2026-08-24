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
KNOWN_TYPES = ("calculation",)

#: Options understood for a calculation widget.
KNOWN_OPTIONS = ("show_log", "log_height", "on_status")

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

        unknown = sorted(set(spec.get("options", {})) - set(KNOWN_OPTIONS))
        if unknown:
            raise WidgetSpecError(
                f"{wid!r}: unknown option(s) {', '.join(unknown)}; "
                f"known options: {', '.join(KNOWN_OPTIONS)}"
            )

    return {
        "version": MANIFEST_VERSION,
        "widgets": list(widgets),
        "layout": {"kind": "rows"},
    }
