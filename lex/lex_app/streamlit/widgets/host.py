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
from lex.lex_app.streamlit.widgets.spec import PK, build_manifest, calculation_spec

#: Route on the lex-app frontend that renders a manifest.
HOST_PATH = "/embed/widgets"


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
        if self._result and self._result.get("payload", {}).get("widget_id") == widget_id:
            return self._result
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

        # Read the previous run's event before the body executes, so a
        # ``page.calculation(...)`` call can return it on this run.
        state_key = f"_lex_widgets_result_{self._key or 'default'}"
        self._page._result = st.session_state.get(state_key)
        self._state_key = state_key
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
        url = f"{base}{HOST_PATH}"
        parsed = urllib.parse.urlparse(base)
        expected_origin = (
            f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
        )

        value = render_widget_host(
            url=url,
            manifest=self._page._manifest(),
            expected_origin=expected_origin,
            min_height=self._min_height,
            key=self._key or "lex_widget_host",
        )
        if value is not None:
            st.session_state[self._state_key] = value
        return False


def lex_widgets(*, key: Optional[str] = None, min_height: int = 200) -> _LexWidgets:
    """Open a widget page. See the module docstring for the contract.

    ``key`` distinguishes two hosts on one page; ``min_height`` is the frame
    height before the host reports its real content height.
    """
    return _LexWidgets(key=key, min_height=min_height)


__all__ = ["lex_widgets", "WidgetPage"]
