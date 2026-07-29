"""
End-to-end demo dashboard for ``lex_view`` bidirectional callbacks.

This is a runnable example, not a test. It demonstrates every public
surface of the new bidirectional ``lex_view`` flow described in
``docs/features/access-and-ui/lex_view callbacks.md``:

* ``serializer="..."``      — render the embedded list/detail through a
  named serializer rather than the model's default one.
* ``on_create=True``        — Streamlit re-runs with a ``create`` event
  envelope whenever the embedded form creates a record.
* ``on_update=True``        — same for updates.
* ``on_select=True``        — debounced 150 ms event whenever the AG-Grid
  selection in the embedded list changes.
* ``on_navigate=True``      — event on every embedded React-Router change.
* ``on_flow_step=True``     — event when a configured ``Flow`` step
  resolves a redirect.

Run with::

    streamlit run lex/test_project/dashboards/lex_view_callbacks_demo.py

The dashboard is intentionally minimal: it shows the latest event
envelope and a per-type running counter so a human can verify each
opt-in flag fires end-to-end against a live ``lex-app`` backend +
React frontend.

There is no automated test that *executes* this file (no headless
Streamlit in CI). The import-smoke test
``lex/test_project/tests/init/test_1r_lex_view_embed_helper.py::test_1_156_demo_dashboard_module_contract``
guards against syntax / API drift so a refactor to ``lex_view()``'s
signature can't silently break the published example.
"""

from __future__ import annotations

import streamlit as st

from lex.lex_app.streamlit.embed import Flow, lex_view


def render() -> dict | None:
    """Render the demo dashboard and return the latest event (or ``None``).

    Extracted into a function so the import-smoke test can drive it
    without a Streamlit runtime — every call inside is a no-op when
    the Streamlit script-run context is missing, and ``lex_view`` is
    monkey-patched by the test.
    """
    st.set_page_config(layout="wide", page_title="lex_view callbacks demo")
    st.title("lex_view bidirectional demo")
    st.caption(
        "Every opt-in flag is on. Interact with the embedded view "
        "below; the latest event envelope appears in the sidebar."
    )

    # Keep a per-type running counter across re-runs so a tester can
    # verify that selecting / creating / updating actually re-runs the
    # script. (Streamlit clears regular vars on every re-run; session
    # state is the supported channel.)
    counters = st.session_state.setdefault(
        "_lex_view_demo_counts",
        {"create": 0, "update": 0, "select": 0, "navigate": 0, "flow_step": 0},
    )

    event = lex_view(
        "investor",
        height=720,
        # Demonstrates the serializer-override surface (Cluster 12h).
        serializer="default",
        # Demonstrates the flow surface, which also drives flow_step events.
        flow=Flow().after_create("investor", "/cashflow/{id}/edit"),
        # All five opt-in flags on — this is the only call site in the
        # codebase that exercises every event type at once.
        on_create=True,
        on_update=True,
        on_select=True,
        on_navigate=True,
        on_flow_step=True,
        key="lex_view_demo",
    )

    if event:
        event_type = event.get("type", "unknown")
        if event_type in counters:
            counters[event_type] += 1
        with st.sidebar:
            st.subheader("Latest event")
            st.json(event)
            st.subheader("Counters")
            st.json(counters)
    else:
        with st.sidebar:
            st.info("Waiting for the first event from the embedded view.")
            st.json(counters)

    return event


if __name__ == "__main__":
    render()
