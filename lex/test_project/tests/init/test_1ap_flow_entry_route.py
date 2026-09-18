"""Intent: a sequence flow already says where it starts, so the caller does not.

``Flow().create("investor")`` states that the chain opens ``/investor/create``.
Writing ``lex_view("investor/create", flow=flow)`` states it a second time, and
two statements of one route can disagree -- silently, because neither side
validates the other. The embed opens whatever ``path`` said and the program then
walks from its own step 0, so the user starts one step into a chain, or on a
route the flow never mentions, with nothing on screen to explain it.

Taking the entry from the flow removes the second statement rather than checking
it. What cannot be written down cannot be wrong.

Three things stay exactly as they were, and each is a scenario below: an
explicit path still wins, because addressing a route directly is what ``path``
is for; a MAPPING flow names no starting point -- its rules fire whenever their
operation happens, from wherever the embed already is -- so it derives nothing;
and no flow at all still embeds the application root.

Cluster 01-init, batch 1ap, scenarios 1.348-1.350.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ap_flow_entry_route.py -v
"""

import urllib.parse

import pytest

from lex.lex_app.streamlit import Flow, ref

pytestmark = pytest.mark.init


def _embedded_path(monkeypatch, **kwargs) -> str:
    """The route ``lex_view`` actually opened, with the query string dropped.

    Drives the public entry point rather than ``Flow.entry_path`` directly: the
    contract customers depend on is the URL the embed loads, and a derivation
    that never reached the URL would pass a test of the helper alone.
    """
    from lex.lex_app.streamlit import embed as embed_mod

    captured = {}
    monkeypatch.setattr(
        embed_mod,
        "render_lex_view_component",
        lambda **kw: captured.update(kw) or None,
    )
    # on_create routes through the component, which is where `url` is visible.
    embed_mod.lex_view(on_create=True, **kwargs)
    return urllib.parse.urlparse(captured["url"]).path


class TestCluster1ap_TheFlowNamesItsOwnEntry:
    """Scenario 1.348: an omitted path comes from the flow's first step."""

    def test_01_348_a_first_create_opens_that_resources_create_form(self, monkeypatch):
        """Scenario 1.348: ``create`` first means the chain starts on its form.

        Given: a sequence whose first step is ``create("investor")``
        When:  lex_view is called with no path at all
        Then:  the embed opens ``/investor/create``
        """
        path = _embedded_path(
            monkeypatch, flow=Flow().create("investor").create("vehicle")
        )

        assert path == "/investor/create", (
            "a sequence beginning with create('investor') must open that "
            f"resource's create form, not {path!r}"
        )

    def test_01_348_a_first_update_opens_the_record_it_names(self, monkeypatch):
        """Scenario 1.348: ``update`` first can only mean a literal id.

        An implicit id has no previous step to take from and a reference can
        only point backwards, so both are refused at author time -- which is
        what makes a first ``update`` safe to resolve without asking anyone.

        Given: a sequence whose first step is ``update("investor", id=7)``
        When:  lex_view is called with no path
        Then:  the embed opens that record
        """
        path = _embedded_path(monkeypatch, flow=Flow().update("investor", id=7).table())

        assert path == "/investor/7", (
            f"a first update with a literal id must open that record, got {path!r}"
        )

    def test_01_348_a_first_goto_opens_the_route_as_written(self, monkeypatch):
        """Scenario 1.348: ``goto`` is the escape hatch, including at the start.

        Given: a sequence whose first step is ``goto("/reports/overview")``
        When:  lex_view is called with no path
        Then:  that route is opened verbatim
        """
        path = _embedded_path(
            monkeypatch,
            flow=Flow().goto("/reports/overview").create("investor").table(),
        )

        assert path == "/reports/overview", (
            f"goto must be opened as written, got {path!r}"
        )

    def test_01_348_the_derived_entry_matches_the_step_the_cursor_starts_on(
        self, monkeypatch
    ):
        """Scenario 1.348: the entry and the cursor must name the same step.

        The cursor ships at 0, so the route the embed opens has to be step 0's
        route. If they disagree the user is standing somewhere the program does
        not think it is -- the exact failure this change exists to remove.
        """
        from lex.lex_app.streamlit import embed as embed_mod

        flow = Flow().create("investor", as_="inv").update("investor", id=ref("inv"))
        captured = {}
        monkeypatch.setattr(
            embed_mod,
            "render_lex_view_component",
            lambda **kw: captured.update(kw) or None,
        )
        embed_mod.lex_view(flow=flow, on_create=True)

        parsed = urllib.parse.urlparse(captured["url"])
        params = urllib.parse.parse_qs(parsed.query)
        first = flow.to_wire()["steps"][0]

        assert params["lex_step"] == ["0"], "a sequence must ship its cursor at zero"
        assert parsed.path == f"/{first['res']}/create", (
            "the opened route and step 0 must be the same place; "
            f"opened {parsed.path!r} for step {first!r}"
        )


class TestCluster1ap_AnExplicitPathStillWins:
    """Scenario 1.349: deriving an entry never overrides one that was given."""

    def test_01_349_an_explicit_path_is_not_replaced_by_the_flow(self, monkeypatch):
        """Scenario 1.349: addressing a route directly is what ``path`` is for.

        Given: a sequence starting at ``create("investor")``
        When:  a different path is passed explicitly
        Then:  the explicit path is what loads
        """
        path = _embedded_path(
            monkeypatch,
            path="vehicle/create",
            flow=Flow().create("investor").create("vehicle"),
        )

        assert path == "/vehicle/create", (
            f"an explicit path must survive a flow that names another, got {path!r}"
        )

    def test_01_349_an_explicit_path_still_gets_its_leading_slash(self, monkeypatch):
        """Scenario 1.349: the normalisation that was already there is intact.

        The derivation is inserted before it, so a path written without a
        leading slash must still be repaired rather than concatenated onto the
        base URL as a suffix of the host.
        """
        path = _embedded_path(monkeypatch, path="investor")

        assert path == "/investor", f"a bare path must gain its slash, got {path!r}"


class TestCluster1ap_WhatNamesNoEntryChangesNothing:
    """Scenario 1.350: only a sequence can say where it starts."""

    def test_01_350_a_mapping_flow_derives_nothing(self, monkeypatch):
        """Scenario 1.350: a mapping has no first step to take a route from.

        Its rules fire whenever their operation happens, from wherever the
        embed already is. Treating the first KEY as a starting point would
        invent an order the author never wrote.

        Given: a mapping flow and no path
        When:  lex_view is called
        Then:  the application root is embedded, exactly as before
        """
        path = _embedded_path(
            monkeypatch, flow=Flow().after_create("investor", "/vehicle/create")
        )

        assert path == "", (
            f"a mapping names no entry, so the path must stay empty, got {path!r}"
        )

    def test_01_350_a_plain_dict_derives_nothing(self, monkeypatch):
        """Scenario 1.350: ``flow=`` never required a ``Flow``.

        The documented signature is ``Optional[Dict[str, str]]`` and callers
        pass literals. A plain dict has no steps and no ``entry_path``, so the
        derivation must not assume it can ask.
        """
        path = _embedded_path(monkeypatch, flow={"investor/create": "/vehicle/create"})

        assert path == "", (
            f"a plain dict has no entry to derive, got {path!r}"
        )

    def test_01_350_no_flow_and_no_path_is_still_the_root(self, monkeypatch):
        """Scenario 1.350: the oldest behaviour of all is untouched."""
        path = _embedded_path(monkeypatch)

        assert path == "", f"no path and no flow must embed the root, got {path!r}"

    def test_01_350_entry_path_reports_none_rather_than_guessing(self):
        """Scenario 1.350: the helper says "I cannot" instead of inventing one.

        ``lex_view`` turns ``None`` into "leave the path alone". A helper that
        returned a plausible-looking route for a flow that never named one would
        put that guess in the URL, which is worse than the root: the root is
        obviously not the flow, and a wrong record looks right.
        """
        assert Flow().entry_path() is None, "an empty flow names no entry"
        assert Flow({"investor/create": "/vehicle"}).entry_path() is None, (
            "a mapping names no entry"
        )
