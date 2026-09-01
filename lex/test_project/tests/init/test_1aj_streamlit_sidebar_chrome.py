"""Intent: the Streamlit sidebar should read as part of lex-app, safely.

It was a teal link floating in an otherwise empty navy column -- no branding, no
indication of who is signed in, and a log-out styled as the most prominent thing
on screen. This gives it the same chrome as lex-app's sidenav: a brand lockup,
the signed-in user, hairlines, and a log-out row weighted like a navigation item
rather than a call to action.

Two boundaries make it safe rather than merely nicer, and both are asserted here
because both are invisible when crossed:

* it owns the CONTAINER only. Whatever ``streamlit_structure.main()`` puts in the
  sidebar is the author's, and is not styled, wrapped or reordered.
* it depends on no Streamlit internals. Every element is markup we emit with
  inline styles, so a Streamlit upgrade that renames a class or a test id cannot
  silently break it -- the failure mode that made ``stActiveTheme`` and
  ``stCustomComponentV1`` worth pinning elsewhere in this cluster.

And one that is a genuine hazard rather than a style point: the display name
comes from the identity provider, so it is untrusted input rendered through
``unsafe_allow_html``.

Cluster 01-init, batch 1aj, scenarios 1.309-1.310.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1aj_streamlit_sidebar_chrome.py
"""

import pytest

from lex.lex_app.design_system import lex_tokens
from lex.lex_app.streamlit.sidebar import (
    NAV_ACCENT,
    NAV_SURFACE,
    _display_name,
    _initials,
    identity_html,
    logout_row_html,
)

pytestmark = pytest.mark.init


class TestCluster1aj_SidebarChrome:
    """The chrome matches lex-app, and stops at the author's content."""

    def test_01_309_colours_come_from_the_vendored_tokens(self):
        """Scenario 1.309: the palette is derived, not retyped.

        A sidebar imitating lex-app that hardcodes its colours drifts the moment
        the brand moves, and drifts silently -- it still renders, just slightly
        wrong, in the one place a user compares the two products side by side.
        """
        assert NAV_SURFACE == lex_tokens.NAVY
        assert NAV_ACCENT == lex_tokens.TEAL

    def test_01_309_the_display_name_cannot_inject_markup(self):
        """Scenario 1.309 (second half): identity is escaped.

        The name comes from the identity provider and is rendered through
        ``unsafe_allow_html``, which is the whole reason this needs a test
        rather than a review comment. A hostile ``preferred_username`` would
        otherwise execute in the session of whoever opened the dashboard.
        """
        hostile = '<script>alert(1)</script>'
        markup = identity_html(hostile, subtitle='" onmouseover="alert(2)')

        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup
        assert 'onmouseover="alert(2)' not in markup

    def test_01_309_the_logout_href_is_escaped_too(self):
        """Scenario 1.309 (third half): so is the URL.

        The sign-out target is built from the request's own base URL, so it is
        as untrusted as anything else that arrives over the wire. An unescaped
        quote would end the attribute and let the rest become markup.
        """
        markup = logout_row_html('/out?rd="><img src=x onerror=alert(1)>')
        assert "<img" not in markup
        assert "&quot;" in markup or "&#x27;" in markup

    def test_01_310_it_renders_no_navigation_of_its_own(self):
        """Scenario 1.310: the container only -- the author's items are theirs.

        The agreed boundary. Emitting nav items here would mean two components
        deciding what a page's navigation is, and the author's would be the one
        that loses, quietly, depending on call order.
        """
        markup = identity_html("Hazem Sahbani", subtitle="h@example.com")

        # Identity and branding, yes.
        assert "Hazem Sahbani" in markup
        assert "EXCELLENCE" in markup
        # Navigation, no.
        for nav in ("<nav", "page_link", "<ul", "<li"):
            assert nav not in markup, f"the sidebar is rendering navigation: {nav}"

    def test_01_310_it_depends_on_no_streamlit_internals(self):
        """Scenario 1.310 (second half): no Streamlit selectors anywhere.

        Everything is our own markup with inline styles. Reaching into
        Streamlit's DOM -- ``[data-testid=...]``, an emotion class, ``.st-``
        anything -- would work until an upgrade renamed it, and then fail by
        looking slightly wrong rather than by raising.

        The cost of that boundary is accepted and worth naming: the log-out row
        cannot be truly bottom-pinned, because pinning needs those selectors. It
        is last in call order instead, which puts it at the bottom without
        touching Streamlit's layout.
        """
        markup = identity_html("A B") + logout_row_html("/out")

        for internal in ('data-testid', 'stSidebar', 'emotion-cache', 'class="st-'):
            assert internal not in markup, f"depends on a Streamlit internal: {internal}"

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Hazem Sahbani", "HS"),
            ("hazem", "HA"),
            ("h.sahbani", "HS"),
            ("anna maria de vries", "AV"),
            ("", "?"),
        ],
    )
    def test_01_310_initials_always_produce_something(self, name, expected):
        """Scenario 1.310 (third half): the avatar is never blank.

        A name can arrive as one word, dotted, hyphenated, or missing entirely
        depending on how the realm is configured. An empty circle reads as a
        broken image; a single letter reads as a person.
        """
        assert _initials(name) == expected

    def test_01_310_the_name_falls_back_through_what_the_realm_provides(self):
        """Scenario 1.310 (fourth half): pick the most human name available.

        Realms differ in which claims they populate. Preferring `name` over
        `preferred_username` over the email means the sidebar shows "Hazem
        Sahbani" where it can and something true where it cannot, rather than a
        UUID.
        """
        assert _display_name({"user_info": {"name": "Hazem Sahbani"}}) == "Hazem Sahbani"
        assert _display_name({"user_info": {"preferred_username": "hazem"}}) == "hazem"
        assert _display_name({"user_email": "h@example.com"}) == "h@example.com"
        # Never blank, even with nothing to go on.
        assert _display_name({}) == "Signed in"
