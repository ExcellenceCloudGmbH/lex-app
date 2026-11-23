"""What a lex.ui handler returns or yields, and the refusal it may raise."""

from __future__ import annotations

import dataclasses
from typing import Any

TONES = ("info", "success", "warning", "error")

#: Tests set this to a list: :func:`to_events` then also records the values it converts.
RECORDER: list | None = None


class Refuse(Exception):
    """An expected refusal: ``message`` is shown to the user as written, and nothing is logged."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = str(message)


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
    """Hand the user a file: by ``url``, or ``data`` with a ``filename``."""
    if url is None and data is None:
        raise ValueError("ui.download() needs url= or data=")
    return Download(url, filename, data)


def progress(fraction: float, message: str = "") -> Progress:
    """What a job yields to report progress: a fraction from 0 to 1 and a message."""
    return Progress(min(1.0, max(0.0, float(fraction))), str(message))


def split(value: Any, *, handler: str) -> tuple[list, Progress | None]:
    """A handler's returned or yielded value as (results, progress report)."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    out: list = []
    report: Progress | None = None
    for item in items:
        if item is None:        # a helper that had nothing to say: `return [ui.toast("Saved"), self._read()]`
            continue
        if isinstance(item, Progress):
            report = item
        elif isinstance(item, (Goto, Toast, Download)):
            out.append(item)
        else:
            raise TypeError(
                f"{handler} returned {item!r}. A lex.ui handler returns ui.goto(), ui.toast() or "
                "ui.download(), and a job may yield ui.progress(). Use @ui.custom for raw Reflex events."
            )
    return out, report


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
        elif isinstance(item, Toast):
            import rfds

            extra = {"description": item.description} if item.description else {}
            events.append(getattr(rfds.toast, item.tone)(item.message, **extra))
        else:
            events.append(rx.download(url=item.url, filename=item.filename, data=item.data))
    return events


def message(key: str, default: str, **params: Any) -> str:
    """A standard message in the app's UI language (RFDS's translation), else ``default``."""
    try:
        from rfds.i18n import translate

        from lex.lex_app.reflex.design import format_settings

        return translate(format_settings()["ui_language"], key, default, **params)
    except Exception:  # noqa: BLE001 - a message must never fail the handler that reports a failure
        return default.format(**params) if params else default
