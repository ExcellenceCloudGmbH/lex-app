"""lex_view sets an embed's filters, names its filter bar and hears its filter changes.

Intent: a Streamlit page passes filters to an embedded table and can change them
on a later rerun without reloading the frame, and two embeds can be linked by
passing one's selection or filters into the other. The filters therefore ride
the component's arguments, never its URL or key: the URL is the component's
identity, and changing it remounts the frame and wipes the user's grid state.
The first load carries them in the iframe URL; later changes are posted, once
the frame has loaded, only when they differ from what the frame has. Every load
-- a reload, a sign-in round trip -- starts an app holding only the filters its
URL carried, so the shim re-posts the host's filters when they differ. A token
longer than 2,000 characters (a long id list) stays out of the URL, where it
could hit a 414 and land in access logs, and arrives by message only.
Cluster 01-init, batch 1ay — scenarios 1.411–1.419. Type: U.
Covers: lex/lex_app/streamlit/embed.py (``lex_view``, ``normalise_filters``,
``encode_filters_token``), lex/lex_app/streamlit/_lex_view_component/__init__.py,
lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js and index.html.
Run: python -m lex pytest lex/test_project/tests/init/test_1ay_lex_view_filters.py -v
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.init

FRONTEND = Path(__file__).resolve().parents[3] / "lex_app/streamlit/_lex_view_component/frontend"


def _decode(token: str) -> dict:
    padded = token + "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def _capture_component(monkeypatch) -> dict:
    from lex.lex_app.streamlit import embed as embed_mod

    captured: dict = {}
    monkeypatch.setattr(
        embed_mod, "render_lex_view_component",
        lambda **kwargs: captured.update(kwargs) or None,
    )
    return captured


def _query(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)


def _run_shim(program: str) -> list:
    """Run ``program`` under node with filter_sync.js loaded as ``s``; it prints JSON."""
    script = FRONTEND / "filter_sync.js"
    out = subprocess.run(
        ["node", "-e", f"const s = require({json.dumps(str(script))});" + program],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


class TestCluster1ay_LexViewFilters:
    """Cluster 1ay: lex_view filters, filter_bar and on_filter."""

    def test_01_411_filters_go_to_the_component_not_the_url(self, monkeypatch):
        """
        Scenario 1.411: the component's identity does not change with the filters
        Given: lex_view with filters
        When: it renders
        Then: the URL carries no filters; the component gets them normalised and as a token
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", filters={"status": ["open", "closed"], "issued": date(2026, 3, 1), "region": None})

        assert "filters" not in _query(captured["url"]), "filters in the URL would remount the frame"
        assert captured["filters"] == {"status": ["open", "closed"], "issued": "2026-03-01", "region": None}
        assert _decode(captured["filters_token"]) == captured["filters"], "the token is the same filters"

    def test_01_412_filters_alone_select_the_component(self, monkeypatch):
        """
        Scenario 1.412: only the component can post a later change
        Given: lex_view with filters and no on_* flag
        When: it renders
        Then: the component renders, not a plain iframe
        """
        from lex.lex_app.streamlit import embed as embed_mod

        captured = _capture_component(monkeypatch)
        monkeypatch.setattr(embed_mod.components, "iframe", lambda *a, **k: pytest.fail("plain iframe used"))
        embed_mod.lex_view("invoice", filters={"status": "open"})
        assert captured["filters"] == {"status": "open"}

    def test_01_413_filter_bar_rides_the_url(self, monkeypatch):
        """
        Scenario 1.413: the bar's columns
        Given: filter_bar as a list, as an empty list, and absent
        When: lex_view renders
        Then: filter_bar=a,b; filter_bar= (hide); no parameter at all
        """
        from lex.lex_app.streamlit import embed as embed_mod

        captured = _capture_component(monkeypatch)
        embed_mod.lex_view("invoice", filter_bar=["status", "region"], on_select=True)
        assert _query(captured["url"])["filter_bar"] == ["status,region"]
        embed_mod.lex_view("invoice", filter_bar=[], on_select=True)
        assert _query(captured["url"])["filter_bar"] == [""], "an empty bar is sent, and means hide"
        embed_mod.lex_view("invoice", on_select=True)
        assert "filter_bar" not in _query(captured["url"])

    def test_01_414_on_filter_opts_in_to_filter_events(self, monkeypatch):
        """
        Scenario 1.414: the event is opt-in, like every other
        Given: on_filter=True
        When: lex_view renders
        Then: emit_filter=true and the component renders
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", on_filter=True)
        assert _query(captured["url"])["emit_filter"] == ["true"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"filters": ["status"]},
            {"filters": {1: ["open"]}},
            {"filters": {"status": [object()]}},
            {"filter_bar": "status"},
            {"filter_bar": ["status", 3]},
        ],
    )
    def test_01_415_a_malformed_argument_is_refused_at_the_call(self, monkeypatch, kwargs):
        """
        Scenario 1.415: mistakes surface in the line that made them
        Given: filters that are not a dict of names to values, or a filter_bar that is not a list of names
        When: lex_view is called
        Then: TypeError, before anything renders
        """
        from lex.lex_app.streamlit.embed import lex_view

        _capture_component(monkeypatch)
        with pytest.raises(TypeError):
            lex_view("invoice", **kwargs)

    def test_01_416_changing_filters_keeps_the_same_url(self, monkeypatch):
        """
        Scenario 1.416: a rerun with new filters is not a new frame
        Given: two renders that differ only in their filters
        When: both render
        Then: the URL handed to the component is identical; the tokens differ
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", filters={"status": ["open"]})
        first_url, first_token = captured["url"], captured["filters_token"]
        lex_view("invoice", filters={"status": ["closed"]})
        assert captured["url"] == first_url
        assert captured["filters_token"] != first_token

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_01_417_the_shim_posts_a_changed_filter_only_after_load(self):
        """
        Scenario 1.417: when the component posts
        Given: the shim's decision function
        When: it is asked with the frame loading, loaded, unchanged, cleared and changed
        Then: it posts only for a loaded frame whose named filters differ from the frame's
        """
        script = FRONTEND / "filter_sync.js"
        program = (
            f"const s = require({json.dumps(str(script))});"
            "const cases = ["
            "[{loaded: false, desired: 'a', current: null}, false],"
            "[{loaded: true, desired: 'a', current: null}, true],"
            "[{loaded: true, desired: 'a', current: 'a'}, false],"
            "[{loaded: true, desired: null, current: 'a'}, false],"
            "[{loaded: true, desired: 'b', current: 'a'}, true],"
            "];"
            "console.log(JSON.stringify(cases.map(([state, want]) => s.shouldPost(state) === want)));"
        )
        out = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout
        assert json.loads(out) == [True] * 5, out

        html = (FRONTEND / "index.html").read_text()
        assert '<script src="filter_sync.js"></script>' in html, "the shim loads the decision function"
        assert 'type: "filter"' in html, "the shim posts filter messages"
        assert "LexViewFilterSync.shouldPost" in html, "and uses the tested function to decide"

    def test_01_418_a_long_filters_token_is_left_out_of_the_url(self, monkeypatch):
        """
        Scenario 1.418: a long filter list is never put in the frame's URL
        Given: filters whose token is exactly 2,000 characters, filters whose token is 2,002, and a
               long id list
        When: lex_view renders each
        Then: only the 2,000-character token is offered for the URL (url_filters_token); for the
              longer ones it is None, while the component still gets the filters and their token,
              so they are delivered by message after load
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        envelope = len('{"order":""}')  # compact JSON around the one string value
        lex_view("invoice", filters={"order": "x" * (1500 - envelope)})  # 1,500 bytes -> 2,000 chars
        assert len(captured["filters_token"]) == 2000
        assert captured["url_filters_token"] == captured["filters_token"], "at the limit it rides the URL"

        lex_view("invoice", filters={"order": "x" * (1501 - envelope)})  # 1,501 bytes -> 2,002 chars
        assert len(captured["filters_token"]) == 2002
        assert captured["url_filters_token"] is None, "over the limit it stays out of the URL"

        ids = list(range(10_000, 10_600))
        lex_view("invoice", filters={"order": ids})
        assert len(captured["filters_token"]) > 2000
        assert captured["url_filters_token"] is None
        assert "filters" not in _query(captured["url"])
        assert captured["filters"] == {"order": ids}, "the filters still reach the component, to be posted"
        assert _decode(captured["filters_token"]) == {"order": ids}

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_01_418_filters_left_out_of_the_url_are_posted_after_load(self):
        """
        Scenario 1.418 (shim): filters left out of the URL arrive by message
        Given: a frame whose URL carried no filters token
        When: it loads, with host filters and without
        Then: the shim treats the frame as holding no filters and posts the host's; with no host
              filters it posts nothing
        """
        results = _run_shim(
            "console.log(JSON.stringify(["
            "s.afterLoad({urlToken: null, desired: 'long-token'}),"
            "s.afterLoad({urlToken: null, desired: null}),"
            "]));"
        )
        assert results == [
            {"current": None, "post": True},
            {"current": None, "post": False},
        ], results

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_01_419_every_load_resets_what_the_frame_holds(self):
        """
        Scenario 1.419: a new load forgets what was posted before it
        Given: a frame whose URL carried filters "a", to which "b" was posted later
        When: the frame loads again -- a reload or a sign-in round trip
        Then: the frame is taken to hold "a" again, so host filters "b" are posted again; host
              filters equal to the URL's "a" are not posted; the shim runs this after every load
        """
        results = _run_shim(
            "const posted = 'b';"  # what an earlier sync posted; a load must not trust it
            "console.log(JSON.stringify(["
            "s.afterLoad({urlToken: 'a', desired: posted}),"
            "s.afterLoad({urlToken: 'a', desired: 'a'}),"
            "]));"
        )
        assert results == [
            {"current": "a", "post": True},
            {"current": "a", "post": False},
        ], results

        html = (FRONTEND / "index.html").read_text()
        assert "LexViewFilterSync.afterLoad" in html, "the shim decides after a load with the tested function"
        assert "args.url_filters_token" in html, "the URL carries only the token Python allows in it"
        assert "{ once: true }" not in html, "the load handler runs after every load, not the first only"
