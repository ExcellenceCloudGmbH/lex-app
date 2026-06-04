"""Cluster 1r: ``lex_view`` embed-iframe query-parameter contract.

Intent
------
``lex/lex_app/streamlit/embed.py`` exposes ``lex_view()`` — the single helper a
Streamlit dashboard uses to embed a React page in an iframe. The React app keys
its behaviour off the query parameters this helper bakes into the iframe URL
(``embed``, ``hide_toolbar``, ``hide_actions``, the ``redirect_after*`` family,
and now ``no_refresh``). If a boolean toggle silently stops appearing in the URL,
the embedded view silently loses that behaviour with no error — exactly the kind
of regression a customer only notices in production.

The ``no_refresh`` toggle is the host-side opt-out for the live list auto-refresh
shipped in cluster 9e: when set, the embedded React ``ModelDataUpdate`` socket
listener ignores ``record_mutation`` broadcasts so an open list view stays a
static snapshot. The contract this batch pins is narrow but load-bearing:

* ``no_refresh=True``  → ``no_refresh=true`` must be present in the iframe query.
* ``no_refresh`` defaulting / ``False`` → the parameter must be **absent** (so an
  un-opted view keeps auto-refreshing — the default behaviour must not regress).
* the toggle must compose with the other flags and never disturb the mandatory
  ``embed=true`` marker or the ``#embed`` fragment the React layout detects on.

All scenarios are pure URL-building logic, no DB, no Streamlit runtime — the only
external boundary (``streamlit.components.v1.iframe``) is patched to capture the
URL the helper would render.

Cluster 1r — scenarios 1.148–1.151. Type: U.
Covers: lex/lex_app/streamlit/embed.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1r_embed_view_params.py -v
"""

from __future__ import annotations

import urllib.parse
from unittest import TestCase, mock

import pytest

pytestmark = pytest.mark.init

from lex.lex_app.streamlit import embed


def _capture_iframe_url(**kwargs) -> str:
    """Call ``lex_view`` with ``components.iframe`` patched and return the URL.

    Patches the one external boundary (Streamlit's iframe renderer) so the test
    exercises the real query-string assembly without a Streamlit runtime.
    """
    with mock.patch.object(embed.components, "iframe") as mock_iframe:
        embed.lex_view("quarter", base_url="http://localhost:8000", **kwargs)
    assert mock_iframe.called, "lex_view must render exactly one iframe"
    # First positional arg to components.iframe is the final URL.
    return mock_iframe.call_args.args[0]


def _query_params(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


class TestCluster01r_NoRefreshParam(TestCase):
    """Cluster 1r: ``lex_view(no_refresh=...)`` URL contract."""

    def test_1_148_no_refresh_true_sets_query_flag(self):
        """
        Scenario 1.148: no_refresh=True surfaces as no_refresh=true.
        Given: a lex_view embed call
        When: no_refresh=True is passed
        Then: the iframe URL carries ?no_refresh=true so the React listener opts out
        """
        url = _capture_iframe_url(no_refresh=True)
        params = _query_params(url)
        self.assertEqual(
            params.get("no_refresh"),
            ["true"],
            msg=f"no_refresh=True must add no_refresh=true to the iframe URL; got {url!r}",
        )

    def test_1_149_default_omits_no_refresh(self):
        """
        Scenario 1.149: the parameter is absent by default.
        Given: a lex_view embed call
        When: no_refresh is not passed
        Then: no_refresh is absent so the view keeps auto-refreshing (no regression)
        """
        url = _capture_iframe_url()
        params = _query_params(url)
        self.assertNotIn(
            "no_refresh",
            params,
            msg=f"no_refresh must not appear unless explicitly enabled; got {url!r}",
        )

    def test_1_150_false_omits_no_refresh(self):
        """
        Scenario 1.150: explicit no_refresh=False is treated as the default.
        Given: a lex_view embed call
        When: no_refresh=False is passed explicitly
        Then: the parameter is still absent (only a truthy opt-in emits it)
        """
        url = _capture_iframe_url(no_refresh=False)
        params = _query_params(url)
        self.assertNotIn(
            "no_refresh",
            params,
            msg=f"no_refresh=False must behave like the default (absent); got {url!r}",
        )

    def test_1_151_composes_with_flags_and_embed_marker(self):
        """
        Scenario 1.151: no_refresh composes with other flags without disturbing embed.
        Given: a lex_view embed call with several toggles
        When: no_refresh=True is combined with hide_toolbar/hide_actions
        Then: all flags are present, embed=true survives, and the #embed fragment is kept
        """
        url = _capture_iframe_url(no_refresh=True, hide_toolbar=True, hide_actions=True)
        params = _query_params(url)
        self.assertEqual(params.get("no_refresh"), ["true"], msg=f"no_refresh missing in {url!r}")
        self.assertEqual(params.get("hide_toolbar"), ["true"], msg=f"hide_toolbar missing in {url!r}")
        self.assertEqual(params.get("hide_actions"), ["true"], msg=f"hide_actions missing in {url!r}")
        self.assertEqual(
            params.get("embed"),
            ["true"],
            msg=f"the mandatory embed=true marker must survive; got {url!r}",
        )
        self.assertTrue(
            urllib.parse.urlparse(url).fragment.endswith("embed"),
            msg=f"the #embed fragment must be preserved so the React layout detects embed mode; got {url!r}",
        )
