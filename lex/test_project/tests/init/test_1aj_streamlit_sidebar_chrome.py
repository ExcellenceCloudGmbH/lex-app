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

Cluster 01-init, batch 1aj, scenarios 1.309-1.311.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1aj_streamlit_sidebar_chrome.py
"""

import pytest

from lex.lex_app.design_system import lex_tokens
from lex.lex_app.streamlit.sidebar import (
    _LOGO_PATH,
    NAV_ACCENT,
    NAV_SURFACE,
    _display_name,
    _initials,
    identity_html,
    _PIN_TO_BOTTOM_CSS,
    logo_is_available,
    logout_row_html,
    render_account,
    render_logo,
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

        # Identity, yes. Branding is no longer here at all -- the logo goes
        # through st.logo's own slot, which 1.311 covers.
        assert "Hazem Sahbani" in markup
        # Navigation, no.
        for nav in ("<nav", "page_link", "<ul", "<li"):
            assert nav not in markup, f"the sidebar is rendering navigation: {nav}"

    def test_01_310_the_content_markup_depends_on_no_streamlit_internals(self):
        """Scenario 1.310 (second half): the content reaches into nothing.

        Everything rendered is our own markup with inline styles, so a Streamlit
        upgrade that renames a class cannot change how any of it looks.
        """
        markup = identity_html("A B") + logout_row_html("/out")

        for internal in ("data-testid", "stSidebar", "emotion-cache", 'class="st-'):
            assert internal not in markup, f"depends on a Streamlit internal: {internal}"

    def test_01_310_exactly_one_selector_is_used_and_it_degrades(self):
        """Scenario 1.310 (third half): the bottom-pin, and its blast radius.

        Bottom-pinning genuinely cannot be done without a selector -- call order
        alone puts the account block under the navigation, not at the foot of the
        panel. So there is exactly one, and this pins both halves of that
        bargain:

        * it is a ``data-testid``, Streamlit's own testing surface, which is far
          more stable than a generated emotion class;
        * it only ever sets layout. If it stops matching, the block sits in
          normal flow -- where it would be anyway -- so the failure mode is "not
          pinned", not "broken".

        A rule that changed colour, size or visibility would fail differently:
        invisibly, and in a way nobody could attribute to a Streamlit upgrade.
        """
        css = _PIN_TO_BOTTOM_CSS
        assert 'data-testid="stSidebarUserContent"' in css

        # Layout only -- nothing that could hide or restyle the block.
        for forbidden in ("display: none", "visibility:", "color:", "background",
                          "opacity", "!important"):
            assert forbidden not in css, f"the pin does more than layout: {forbidden}"

        # And the markup it targets is ours, not Streamlit's.
        assert "data-lex-account" in css

    def test_01_310_the_account_block_is_identity_and_logout_together(self):
        """Scenario 1.310 (fourth half): one block, rendered last.

        Identity and the way out are one thing -- an account block -- and they
        belong at the foot of the panel, not split across it. Rendering them
        together after ``main()`` also means the app's own navigation sits above
        them without either side coordinating.

        The logo is deliberately NOT here: it goes through ``st.logo`` into the
        header slot, which sits above even Streamlit's page navigation.
        """
        rendered = []

        class FakeSt:
            class sidebar:
                @staticmethod
                def markdown(html, **kwargs):
                    rendered.append(html)

        render_account(
            FakeSt(),
            {"user_info": {"name": "Hazem Sahbani", "email": "h@example.com"}},
            logout_href="/oauth2/sign_out",
        )

        joined = "".join(rendered)
        assert "Hazem Sahbani" in joined
        assert "Log out" in joined
        assert "<img" not in joined, "the logo belongs in st.logo's slot, not here"

        # Identity above the log-out, within the one block.
        body = rendered[-1]
        assert body.index("Hazem Sahbani") < body.index("Log out")

    def test_01_310_no_logout_row_when_logout_is_disabled(self):
        """Scenario 1.310 (fifth half): identity still renders without it.

        ``?is_logout_enabled=false`` exists for embeds where the host owns the
        session. Losing the row must not lose the identity with it -- the block
        is who you are, and only sometimes also the way out.
        """
        rendered = []

        class FakeSt:
            class sidebar:
                @staticmethod
                def markdown(html, **kwargs):
                    rendered.append(html)

        render_account(FakeSt(), {"user_info": {"name": "Hazem Sahbani"}}, logout_href=None)

        joined = "".join(rendered)
        assert "Hazem Sahbani" in joined
        assert "Log out" not in joined

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


class TestCluster1aj_BrandLockup:
    """The real logo, shipped and placed by Streamlit rather than by hand."""

    def test_01_311_the_real_logo_is_vendored(self):
        """Scenario 1.311: the sidebar uses lex-app's own logo asset.

        The same file lex-app's sidenav imports -- the DARK variant, white
        wordmark on teal, which is what a navy surface needs.

        Vendored into the package rather than fetched from the frontend build at
        runtime: those filenames carry content hashes and change on every
        rebuild, so a reference would break at the next deploy and show a broken
        image in the one place branding matters.
        """
        assert _LOGO_PATH.is_file(), f"logo not vendored at {_LOGO_PATH}"
        assert _LOGO_PATH.read_bytes().lstrip().startswith(b"<svg"), "not an SVG"
        assert logo_is_available()

    def test_01_311_it_goes_in_streamlits_logo_slot_not_our_markup(self):
        """Scenario 1.311 (second half): ``st.logo``, not a hand-placed image.

        Streamlit reserves a header slot for exactly this -- top of the sidebar,
        on the same line as the collapse control, which is where lex-app puts
        its logo too.

        Hand-placing an ``<img>`` in the sidebar's USER CONTENT is what made it
        look misplaced: that area begins below the header, so the image landed
        under the collapse button with the header's whitespace above it. No
        amount of negative margin fixes that honestly, which is why this asserts
        the slot rather than a measurement.
        """
        calls = []

        class FakeSt:
            def logo(self, image, **kwargs):
                calls.append((image, kwargs))

        render_logo(FakeSt())
        assert len(calls) == 1, "the logo did not go through st.logo"
        assert calls[0][0].endswith("dark-lex-logo.svg")

        # And the identity markup no longer carries it.
        markup = identity_html("Hazem Sahbani", "h@example.com")
        assert "<img" not in markup
        assert "base64" not in markup

    def test_01_311_a_missing_asset_costs_the_logo_not_the_page(self, monkeypatch):
        """Scenario 1.311 (third half): skip the call rather than raise.

        ``st.logo`` raises on a missing file, so an unguarded call would take
        the whole page down over a packaging mistake. Not hypothetical: writing
        this batch turned up that the widget-host component's own frontend was
        absent from package-data, which an editable install hides completely.
        """
        import lex.lex_app.streamlit.sidebar as sb

        monkeypatch.setattr(sb, "_LOGO_PATH", sb._LOGO_PATH.with_name("does-not-exist.svg"))

        called = []

        class FakeSt:
            def logo(self, *a, **k):
                called.append(a)

        sb.render_logo(FakeSt())
        assert called == [], "st.logo was called with a file that does not exist"

    def test_01_311_every_static_asset_directory_is_declared_package_data(self):
        """Scenario 1.311 (fourth half): the assets actually ship.

        A static file missing from ``package-data`` works perfectly in an
        editable install -- which reads the source tree -- and is simply absent
        from the wheel. The widget-host shim was in exactly that state: the whole
        ``lex_widgets()`` feature would have failed to find its frontend on a
        real install, and nothing here would have caught it.
        """
        root = _LOGO_PATH.resolve().parents[1]          # the `lex` package
        pyproject = (root.parent / "pyproject.toml").read_text()

        assert '"lex.assets" = ["**/*"]' in pyproject

        for component in sorted((root / "lex_app/streamlit").glob("_*_component")):
            if not (component / "frontend").is_dir():
                continue
            dotted = f"lex.lex_app.streamlit.{component.name}"
            assert f'"{dotted}" = ["frontend/**/*"]' in pyproject, (
                f"{dotted} ships a frontend/ that would be absent from the wheel"
            )
