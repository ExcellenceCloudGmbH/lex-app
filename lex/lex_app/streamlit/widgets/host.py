"""``lex_widgets()`` -- put calculations on a Streamlit page, one runtime for all.

    with lex_widgets() as page:
        status = page.calculation("quarter", pk=42, show_log=True, on_status=True)
        page.calculation("quarter", pk=43)

    if status and status["status"] == "SUCCESS":
        st.dataframe(load_results())

Everything inside the block is collected, and the host iframe renders **once**
when the block closes. That is the whole point: a dashboard of thirteen
calculations is thirteen widgets in one React runtime, not thirteen iframes with
a React bundle, a JS context and four WebSockets each.

The cost, stated plainly: widgets appear where the ``with`` block closes, not
where each call sits. Interleaving ``st.write()`` between two widgets is not
available. The context manager makes that boundary a language construct rather
than a convention someone has to remember.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from lex.lex_app.streamlit._widget_host_component import render_widget_host
from lex.lex_app.streamlit.embed import _resolve_base_url
from lex.lex_app.streamlit.widgets.spec import (
    PK,
    build_manifest,
    calculation_log_spec,
    calculation_spec,
)

#: Route on the lex-app frontend that renders a manifest.
HOST_PATH = "/embed/widgets"

#: Height for the log inside the page-level dialog. Generous on purpose -- the
#: whole reason the dialog exists is that the log is unreadable when it is
#: bounded by a widget's box rather than by the browser window.
_DIALOG_LOG_HEIGHT = 820


class WidgetPage:
    """Collects widget specs inside a ``lex_widgets()`` block.

    Not constructed directly -- ``lex_widgets()`` yields one.
    """

    def __init__(self) -> None:
        self._specs: List[Dict[str, Any]] = []
        self._result: Optional[dict] = None
        self._auto_id = 0

    def calculation(
        self,
        model: str,
        pk: PK,
        *,
        show_log: bool = False,
        log_height: Optional[int] = None,
        on_status: bool = False,
        id: Optional[str] = None,
    ) -> Optional[dict]:
        """Add a calculation widget: the Calculate control, its status, its log.

        Returns the latest status envelope when ``on_status=True``, else
        ``None``. The value is from the *previous* run of the script, because
        Streamlit reruns top-to-bottom on each event -- the same contract every
        Streamlit input widget has.
        """
        self._auto_id += 1
        widget_id = id or f"w{self._auto_id}"
        self._specs.append(
            calculation_spec(
                widget_id,
                model,
                pk,
                show_log=show_log,
                log_height=log_height,
                on_status=on_status,
            )
        )
        # Route the stored envelope back to the widget that produced it, so two
        # widgets on a page do not read each other's status.
        #
        # The type check is load-bearing, not defensive noise: every envelope
        # type shares one component value, so an `open_log` click for THIS
        # widget would otherwise be handed back as if it were a status. Its
        # payload has no "status" key, so the caller's
        # ``status["payload"]["status"]`` raised KeyError.
        result = self._result
        if (
            isinstance(result, dict)
            and result.get("type") == "calculation_status"
            and (result.get("payload") or {}).get("widget_id") == widget_id
        ):
            return result
        return None

    def calculation_log(
        self,
        model: str,
        pk: PK,
        *,
        height: Optional[int] = None,
        calculation_id: Optional[str] = None,
        stream: bool = False,
        id: Optional[str] = None,
    ) -> None:
        """Add the calculation log on its own, sized by you.

        Prefer this over ``calculation(..., show_log=True)`` when the log is
        the point. A two-pane tree wants width and height; a Calculate control
        wants a line. Declaring them separately lets the control sit in a
        narrow column and the log run full width beneath it -- which is what
        makes the log readable rather than a letterbox.

        Returns ``None``: a log emits no status. Use ``calculation(...)`` for
        that.
        """
        self._auto_id += 1
        self._specs.append(
            calculation_log_spec(
                id or f"w{self._auto_id}",
                model,
                pk,
                height=height,
                calculation_id=calculation_id,
                stream=stream,
            )
        )
        return None

    # ── internals ───────────────────────────────────────────────────────
    def _manifest(self) -> Dict[str, Any]:
        return build_manifest(self._specs)

    def _is_empty(self) -> bool:
        return not self._specs


class _LexWidgets:
    """Context manager returned by :func:`lex_widgets`."""

    def __init__(self, *, key: Optional[str], min_height: int) -> None:
        self._key = key
        self._min_height = min_height
        self._page = WidgetPage()

    def __enter__(self) -> WidgetPage:
        import streamlit as st

        # Streamlit stores a keyed component's current value in session_state
        # under that key, so read it directly rather than stashing a copy of our
        # own. An earlier version kept a private key and wrote it in __exit__,
        # which meant the value was always one rerun behind what the component
        # already knew -- so the first Calculate never surfaced in Python.
        self._component_key = self._key or "lex_widget_host"
        self._page._result = st.session_state.get(self._component_key)
        return self._page

    def __exit__(self, exc_type, exc, tb) -> bool:
        import streamlit as st

        # A failure inside the block is the author's, not ours -- let it
        # propagate untouched rather than rendering a half-built page.
        if exc_type is not None:
            return False

        if self._page._is_empty():
            return False

        base = _resolve_base_url()
        # embed=true is what actually strips the app chrome (sidebar, appbar,
        # breadcrumb) -- see useEmbedContext.detectEmbed. Without it the widget
        # host renders inside the full CustomLayout, which is both wrong to look
        # at and 100vh-based, so it feeds the host's content-height resize into a
        # runaway growth loop.
        url = f"{base}{HOST_PATH}?embed=true"
        parsed = urllib.parse.urlparse(base)
        expected_origin = (
            f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
        )

        # Streamlit keeps the returned value in session_state[key] for us; the
        # next run's __enter__ reads it there. Nothing to stash.
        render_widget_host(
            url=url,
            manifest=self._page._manifest(),
            expected_origin=expected_origin,
            min_height=self._min_height,
            key=self._component_key,
        )

        self._maybe_open_log_dialog(url, expected_origin)
        return False

    def _maybe_open_log_dialog(self, url: str, expected_origin: Optional[str]) -> None:
        """Show the log in a PAGE-LEVEL dialog when a widget asks for it.

        Why it cannot simply pop up inside the widget: the log dialog renders in
        a portal with ``position: fixed``, so inside an embedded frame it is
        clipped to that frame's viewport and does not grow ``scrollHeight`` --
        the host's content-height resize cannot rescue it. A Streamlit dialog
        lives on the page instead of in the frame, so it is bounded by the
        browser window rather than by a widget's box.

        The dialog hosts its own single-widget manifest. That reuses the whole
        rendering path rather than adding a second way to draw a log.
        """
        import streamlit as st

        event = st.session_state.get(self._component_key)
        if not isinstance(event, dict) or event.get("type") != "open_log":
            return

        payload = event.get("payload") or {}
        seen_key = f"_lex_widgets_log_seen_{self._component_key}"
        # One dialog per click. Without this the dialog reopens on every rerun,
        # because the component value persists after being consumed.
        if st.session_state.get(seen_key) == event.get("id"):
            return
        st.session_state[seen_key] = event.get("id")

        model = payload.get("model")
        pk = payload.get("pk")
        calculation_id = payload.get("calculationId") or payload.get("calculation_id")
        if not model or pk is None:
            return

        title = f"Calculation log — {model} #{pk}"

        @st.dialog(title, width="large")  # type: ignore[misc]
        def _log_dialog() -> None:
            spec = calculation_log_spec(
                "dialog_log",
                str(model),
                pk,
                height=_DIALOG_LOG_HEIGHT,
                calculation_id=calculation_id or None,
                # The popup streams. Someone who just pressed Calculate wants to
                # watch output arrive, not navigate a structure.
                stream=True,
            )
            render_widget_host(
                url=url,
                manifest=build_manifest([spec]),
                expected_origin=expected_origin,
                min_height=_DIALOG_LOG_HEIGHT,
                key=f"{self._component_key}_log_dialog",
            )

        _log_dialog()


def lex_widgets(*, key: Optional[str] = None, min_height: int = 200) -> _LexWidgets:
    """Open a widget page. See the module docstring for the contract.

    ``key`` distinguishes two hosts on one page; ``min_height`` is the frame
    height before the host reports its real content height.
    """
    return _LexWidgets(key=key, min_height=min_height)


__all__ = ["lex_widgets", "WidgetPage"]
