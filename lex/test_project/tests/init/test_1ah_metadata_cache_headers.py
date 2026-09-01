"""Intent: N embedded frames must not each rebuild the whole model tree.

``api/model-structure`` describes the shape of the project. Producing it
deepcopies the structure, then instantiates every model class and evaluates its
list permission -- per request. It is also permission-pruned, so it is per-user.

The cost that hurt was repetition, not size. Every embedded lex-app frame is its
own JS realm with its own query cache, so a Streamlit page with six widget blocks
asked for the whole tree six times, concurrently, on every load -- observed as
five to six `model-structure` requests in one page's network log, inside 171
requests total. Nothing was looping; six independent runtimes each wanted it once.

The browser HTTP cache IS shared across same-origin frames, so a short
``private`` window collapses that to one request with no client-side
coordination and no shared-state machinery.

``private`` is the load-bearing word. The tree is pruned to the requesting user's
permissions, so a shared cache serving it to a second user would leak which
models the first can see. That is a disclosure bug, not a performance one, which
is why it is asserted here rather than left to the header string being right.

Cluster 01-init, batch 1ah, scenarios 1.304-1.305.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ah_metadata_cache_headers.py
"""

import pytest

from lex.api.views.ModelStructureObtainView import (
    _METADATA_CACHE_SECONDS_DEFAULT,
    _cache_per_user,
    _metadata_cache_seconds,
)

pytestmark = pytest.mark.init


class _FakeResponse(dict):
    """Enough of an HttpResponse for header assertions."""

    def get(self, key, default=None):  # noqa: D102
        return dict.get(self, key, default)


class TestCluster1ah_MetadataCacheHeaders:
    """A brief per-user window, and never a shared one."""

    def test_01_304_the_response_is_reusable_by_one_browser_only(self):
        """Scenario 1.304: private, time-boxed, and varying on the cookie.

        Given a metadata response
        When the cache headers are applied
        Then a browser may reuse it briefly and a shared cache may not touch it
        """
        headers = _cache_per_user(_FakeResponse(), seconds=30)

        cache_control = headers["Cache-Control"]
        assert "private" in cache_control, (
            "a shared cache would serve one user's visible models to another"
        )
        assert "max-age=30" in cache_control
        assert "public" not in cache_control
        assert "no-store" not in cache_control

        # Belt and braces for anything that honours Vary but ignores private.
        assert "Cookie" in headers["Vary"]

    def test_01_304_an_existing_vary_is_extended_not_replaced(self):
        """Scenario 1.304 (second half): don't clobber a Vary someone else set.

        DRF and middleware set ``Vary`` for content negotiation. Overwriting it
        would make the cache ignore ``Accept``, which is a correctness bug that
        shows up only under a cache -- exactly where it is hardest to see.
        """
        headers = _cache_per_user(_FakeResponse({"Vary": "Accept"}), seconds=30)
        assert "Accept" in headers["Vary"]
        assert "Cookie" in headers["Vary"]

    def test_01_305_caching_can_be_turned_off_completely(self, monkeypatch):
        """Scenario 1.305: zero means no-store, not "default".

        The window is a staleness budget for permission changes: for up to that
        long, a revoked permission still shows in the tree. A deployment that
        cannot accept any such window must be able to opt out, and the opt-out
        has to be unambiguous -- silently falling back to the default would be
        the worst outcome, since it reads as disabled.
        """
        monkeypatch.setenv("LEX_METADATA_CACHE_SECONDS", "0")
        assert _metadata_cache_seconds() == 0

        headers = _cache_per_user(_FakeResponse())
        assert headers["Cache-Control"] == "no-store"
        assert "max-age" not in headers["Cache-Control"]

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("60", 60),
            ("0", 0),
            ("", _METADATA_CACHE_SECONDS_DEFAULT),      # set but blank
            ("not-a-number", _METADATA_CACHE_SECONDS_DEFAULT),
            ("-5", 0),                                   # clamped, never negative
        ],
    )
    def test_01_305_the_window_is_read_defensively(self, monkeypatch, value, expected):
        """Scenario 1.305 (second half): a bad value must not break the endpoint.

        This is an env var, so it will eventually be set to a blank string by a
        template or to a word by a typo. Neither may take the endpoint down, and
        a negative value must not become a negative max-age -- browsers treat
        that inconsistently, so it is clamped rather than passed through.
        """
        monkeypatch.setenv("LEX_METADATA_CACHE_SECONDS", value)
        assert _metadata_cache_seconds() == expected

    def test_01_305_an_unset_variable_uses_the_documented_default(self, monkeypatch):
        """Scenario 1.305 (third half): the default applies with nothing set."""
        monkeypatch.delenv("LEX_METADATA_CACHE_SECONDS", raising=False)
        assert _metadata_cache_seconds() == _METADATA_CACHE_SECONDS_DEFAULT
        assert 0 < _METADATA_CACHE_SECONDS_DEFAULT <= 300, (
            "the default is a staleness budget for permissions -- keep it short"
        )
