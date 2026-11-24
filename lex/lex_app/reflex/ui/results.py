"""What a lex.ui handler returns or yields, and the refusal it may raise."""

from __future__ import annotations

import dataclasses
from typing import Any

TONES = ("info", "success", "warning", "error")

#: Tests set this to a list: :func:`to_events` then also records the values it converts.
RECORDER: list | None = None


class Refuse(Exception):
    """An expected refusal: ``message`` is shown to the user as written, and nothing is logged.

    ``title`` is an optional short heading above it: ``ui.Refuse("No order in this region has an
    invoice.", title="Nothing to export")``. A form's handler's refusal is shown in the form, above
    its submit button, until the form is sent again; elsewhere it is a toast, headed by the title.
    """

    def __init__(self, message: str, *, title: str = ""):
        if not isinstance(title, str):
            raise TypeError(f"ui.Refuse(title=) is a short text, such as title=\"Nothing to export\"; got {title!r}")
        super().__init__(message)
        self.message = str(message)
        self.title = title


@dataclasses.dataclass(frozen=True)
class Goto:
    path: str
    replace: bool = False


@dataclasses.dataclass(frozen=True)
class Toast:
    message: str
    tone: str = "info"
    description: str = ""


@dataclasses.dataclass(frozen=True)
class Download:
    url: str | None = None
    filename: str | None = None
    data: Any = None
    media_type: str | None = None


class Refresh(list):
    """What ``ui.refresh`` returns: the Reflex events that make those tables read again.

    A list of events, so a handler written in raw Reflex can return it as it is.
    """

    handlers: tuple = ()


@dataclasses.dataclass(frozen=True)
class Progress:
    fraction: float
    message: str = ""


def goto(path: str, *, replace: bool = False) -> Goto:
    """Send the browser to ``path`` (a page of the dashboards, or a full URL)."""
    if not isinstance(path, str) or not path:
        raise ValueError("ui.goto() takes a path such as '/tickets/1' or a full URL")
    return Goto(path, bool(replace))


def toast(message: str, *, tone: str = "info", description: str = "") -> Toast:
    """Show a short message; ``tone`` is one of info, success, warning, error."""
    if tone not in TONES:
        raise ValueError(f"ui.toast() tone must be one of {', '.join(TONES)}; got {tone!r}")
    return Toast(str(message), tone, str(description))


def download(*, url: str | None = None, filename: str | None = None, data: Any = None) -> Download:
    """Hand the user a file: by ``url``, or ``data`` with a ``filename``. The browser is told what
    the file is from its name (``report.pdf`` is a PDF)."""
    if url is None and data is None:
        raise ValueError("ui.download() needs url= or data=")
    import mimetypes

    kind = mimetypes.guess_type(filename)[0] if (data is not None and filename) else None
    return Download(url, filename, data, kind)


def refresh(*handlers: Any) -> Refresh:
    """Make the tables that read these ``@ui.rows`` handlers read again: ``return ui.refresh(State.rows)``.

    A table already reads again after a handler of its own state; this is for a handler of another state.
    """
    from lex.lex_app.reflex.ui import rows

    if not handlers or not all(rows.is_rows(h) for h in handlers):
        raise TypeError("ui.refresh() takes @ui.rows handlers: `return ui.refresh(FundState.holdings)`")
    from lex.lex_app.reflex.ui.pages import rows_table

    out = Refresh(rows_table.reread_events(handlers=handlers))
    out.handlers = tuple(handlers)
    return out


def progress(fraction: float, message: str = "") -> Progress:
    """What a job yields to report progress: a fraction from 0 to 1 and a message."""
    return Progress(min(1.0, max(0.0, float(fraction))), str(message))


def split(value: Any, *, handler: str, state: str | None = None) -> tuple[list, Progress | None]:
    """A handler's returned or yielded value as (results, progress report).

    ``state`` is the full name of the running handler's state: a handler of that same state is
    refused, since one handler does not start another.
    """
    items = ([] if value is None else [value] if isinstance(value, Refresh)
             else list(value) if isinstance(value, (list, tuple)) else [value])
    out: list = []
    report: Progress | None = None
    for item in items:
        if item is None:        # a helper that had nothing to say: `return [ui.toast("Saved"), self._read()]`
            continue
        if isinstance(item, Progress):
            report = item
        elif isinstance(item, (Goto, Toast, Download, Refresh)):
            out.append(item)
        elif _is_state_handler(item):
            _check_hand_on(item, handler=handler, state=state)
            out.append(item)
        else:
            raise TypeError(
                f"{handler} returned {item!r}. A lex.ui handler returns ui.goto(), ui.toast(), ui.download(), "
                "ui.refresh() or another state's handler (`Other.reload`, `Other.open(key)`), and a job may "
                "yield ui.progress(). Use @ui.custom for raw Reflex events such as rx.call_script()."
            )
    return out, report


def _is_state_handler(item: Any) -> bool:
    """Another state's handler, as a handler may hand it on: ``Other.reload`` or ``Other.open(key)``.

    Only a handler of a state: Reflex's own events (``rx.call_script``, ``rx.redirect``,
    ``rx.download``...) stay in the custom region (``@ui.custom``) - a toast, a goto and a download
    have lex.ui results of their own.
    """
    from reflex.event import EventHandler, EventSpec

    target = item.handler if isinstance(item, EventSpec) else item
    return isinstance(target, EventHandler) and bool(target.state_full_name)


def _check_hand_on(item: Any, *, handler: str, state: str | None) -> None:
    """Refuse, when the handler returns, a hand-on that could only fail later in the browser."""
    from reflex.event import EventSpec

    target = item.handler if isinstance(item, EventSpec) else item
    name = getattr(target.fn, "__ui_key__", target.fn.__qualname__)
    if getattr(target.fn, "__ui_kind__", None) == "rows":
        raise TypeError(f"{handler} returned the @ui.rows handler {name}. A table reads it; to make its tables "
                        f"read again, return ui.refresh({name}).")
    if state is not None and target.state_full_name == state:
        raise TypeError(f"{handler} returned {name}, a handler of its own state; one handler does not start "
                        "another. Put the shared work in a method whose name starts with `_` and call it.")


def _external(path: str) -> bool:
    """Whether ``path`` leaves the dashboards: a full URL, or ``//host/...`` (another site to a browser)."""
    return "://" in path or path.startswith("//")


def to_events(items: list) -> list:
    """The Reflex events for a handler's results, in order."""
    import reflex as rx

    if RECORDER is not None:
        RECORDER.extend(items)
    events = []
    for item in items:
        if isinstance(item, Goto):
            if _external(item.path):
                from lex.lex_app.reflex.session import navigate

                events.append(navigate(item.path))
            else:
                events.append(rx.redirect(item.path, replace=item.replace))
        elif isinstance(item, Refresh):
            from lex.lex_app.reflex.ui.pages import rows_table

            events.extend(rows_table.reread_events(handlers=item.handlers))
        elif isinstance(item, Toast):
            import rfds

            extra = {"description": item.description} if item.description else {}
            events.append(getattr(rfds.toast, item.tone)(item.message, **extra))
        elif _is_state_handler(item):
            events.append(item)
        else:
            events.append(rx.download(url=item.url, filename=item.filename, data=item.data, mime_type=item.media_type))
    return events


def message(key: str, default: str, **params: Any) -> str:
    """A standard message in the app's UI language (RFDS's translation), else ``default``."""
    try:
        from rfds.i18n import translate

        from lex.lex_app.reflex.design import format_settings

        return translate(format_settings()["ui_language"], key, default, **params)
    except Exception:  # noqa: BLE001 - a message must never fail the handler that reports a failure
        return default.format(**params) if params else default
