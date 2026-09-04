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

Cluster 01-init, batch 1aj, scenarios 1.309-1.315.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1aj_streamlit_sidebar_chrome.py
"""

import json
import re

import pytest

from lex.lex_app.design_system import lex_tokens
from lex.lex_app.streamlit.sidebar import (
    EMBED_PARAM,
    _HIDE_SIDEBAR_RULES,
    HIDE_SIDEBAR_CSS,
    embedded_in_lex_app,
    hide_sidebar_when_framed_js,
    _LOGO_COLLAPSED_PATH,
    _LOGO_PATH,
    NAV_ACCENT,
    NAV_SURFACE,
    _display_name,
    _initials,
    identity_html,
    _CHROME_CSS,
    _CONTENT_INSET,
    _PIN_TO_BOTTOM_CSS,
    logo_is_available,
    logout_row_html,
    render_account,
    render_logo,
)

pytestmark = pytest.mark.init

def _rules_only(css: str) -> str:
    """The CSS with its comments removed.

    Assertions here are about what the browser is told, and a comment is not
    that. Without this, explaining a rejected approach in a comment -- "not
    `calc(100vh - <nav>)`, because..." -- fails the test that forbids it, which
    trains you to write worse comments to keep the suite green.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)




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
        css = _CHROME_CSS
        assert 'data-testid="stSidebarUserContent"' in css

        # Layout only -- nothing that could hide or restyle anything. This holds
        # for EVERY rule in the block, not just the pin: the logo alignment added
        # later obeys the same limit, which is what keeps the whole dependency
        # cheap to lose.
        for forbidden in ("display: none", "visibility:", "color:", "background",
                          "opacity", "!important"):
            assert forbidden not in _rules_only(css), (
                f"the chrome CSS does more than layout: {forbidden}"
            )

        # And the markup it targets is ours, not Streamlit's.
        assert "data-lex-account" in css

    def test_01_314_the_markup_survives_streamlits_markdown_parser(self):
        """Scenario 1.314: no newline reaches ``unsafe_allow_html``.

        ``st.markdown(..., unsafe_allow_html=True)`` runs the string through a
        MARKDOWN parser before any HTML is honoured, and markdown has opinions
        about whitespace that HTML does not: a blank line ends an HTML block,
        and a four-space indent is a code block.

        Pretty-printed markup satisfies both. When the optional subtitle is
        omitted its placeholder leaves a whitespace-only line -- markdown reads
        that as blank, closes the HTML block, and renders the following indented
        ``</div>`` as literal text in a code box, in the sidebar, to the user.

        Invisible in every screenshot taken until now, because every account
        anyone had looked at HAD an email to put in the subtitle. It took
        rendering the real component with the optional piece missing.

        The fix removes the class rather than the instance: no builder emits a
        newline at all, so no future edit can reintroduce a blank line or an
        indent for markdown to interpret.
        """
        with_subtitle = identity_html("Hazem Sahbani", subtitle="h@example.com")
        without = identity_html("Hazem Sahbani", subtitle=None)
        logout = logout_row_html("/logout")

        for label, markup in (("identity", with_subtitle), ("identity/no subtitle", without),
                              ("logout", logout)):
            assert "\n" not in markup, f"{label} hands markdown a newline to interpret"

        # Joined with a space, not welded: attributes are whitespace-separated,
        # and `target="_top"style=` is one unparseable attribute.
        assert 'target="_top" style=' in logout, "attributes were welded together"

        # And the omitted piece leaves no orphan.
        assert without.count("<div") == without.count("</div>")

    def test_01_315_a_framed_page_has_no_sidebar_at_all(self):
        """Scenario 1.315: a guest surface draws no host furniture.

        When lex-app frames a Streamlit page, lex-app is already drawing a
        sidenav, the logo, the signed-in user and a way out -- immediately to the
        left of the frame. A second sidebar inside it is the same furniture
        twice, and the inner one navigates a different app.

        Gone, not collapsed. Collapsing leaves a control that reopens a panel the
        host never wanted, which is a worse end state than not having one; the
        expand control is hidden with it. The content column's left offset goes
        too, or the page keeps a gutter for furniture that is not there.

        The signal is an explicit parameter rather than an inference.
        ``is_logout_enabled=false`` and ``auth_token`` both happen to correlate
        with being framed today, but they mean other things, and a signal that is
        true by coincidence stops being true the moment either is used elsewhere.
        """
        assert EMBED_PARAM == "lex_embed"

        assert embedded_in_lex_app({"lex_embed": "true"})
        assert embedded_in_lex_app({"lex_embed": ["1"]}), "Streamlit hands back lists"
        assert not embedded_in_lex_app({})
        assert not embedded_in_lex_app({"lex_embed": "false"})

        css = _rules_only(HIDE_SIDEBAR_CSS)
        assert 'section[data-testid="stSidebar"]' in css
        assert "display: none" in css
        # The control that would bring it back goes too.
        assert "stSidebarCollapsedControl" in css or "stExpandSidebarButton" in css
        # And no gutter is left behind.
        assert "margin-left: 0" in css

    def test_01_315_framing_is_the_fallback_until_the_frontend_ships(self):
        """Scenario 1.315 (second half): work before the parameter arrives.

        The parameter is exact and costs no repaint, but only once the frontend
        that sends it has been rebuilt and deployed. Whether this page sits
        inside a frame is knowable from the page itself and needs nobody's
        cooperation, so it covers every deployment in between -- which in this
        project is not a hypothetical gap.

        It asks about ``top``, not ``parent``: this script's parent is the
        Streamlit page, and the question is whether THAT page is framed. A
        cross-origin ``top`` throws on access, and throwing IS the answer -- only
        a framed page can fail that read.

        Both paths share one definition of the rules, because two copies of a
        stylesheet drift and the drift is invisible until someone is looking at
        the wrong surface. That already happened once here: the first version
        extracted the rules with a regex that matched the file's FIRST style
        block, shipped the account-block layout as the hide rules, and logged
        "sidebar is hidden" over a fully visible sidebar.
        """
        js = hide_sidebar_when_framed_js()

        assert "host.top !== host" in js
        assert "catch (e) { framed = true; }" in js, (
            "a cross-origin top must count as framed, not as an error"
        )
        # One definition, shared. Decode the embedded literal rather than
        # pattern-matching escaped text: the rules travel through _js_literal,
        # which JSON-escapes the quotes, so asserting on the raw selector would
        # fail for the wrong reason and asserting on the escaped form would pin
        # the escaping instead of the rules.
        embedded = json.loads(js[js.index('style.textContent = ') + 20 : js.index(";\n", js.index("style.textContent"))])
        assert 'section[data-testid="stSidebar"]' in embedded
        assert "display: none" in embedded
        assert embedded == _HIDE_SIDEBAR_RULES, "the two paths have drifted apart"
        # Installed once, however many reruns recreate this frame.
        assert "__lexSidebarHidden" in js

    def test_01_313_the_pin_matches_the_element_that_can_actually_move(self):
        """Scenario 1.313: the pin lands on a flex child, not on a wrapper.

        Reported as "put logout and user to the bottom of the sidebar" -- a
        second time, because the first attempt shipped a selector that matched
        and still did nothing::

            div:has(> div[data-lex-account]) { margin-top: auto; }

        The child combinator matched the innermost wrapper Streamlit puts around
        markdown output. That element is not a flex child of the column, so
        ``auto`` had no free space to consume and the block stayed exactly where
        it rendered. Nothing errored; the CSS was live and inert -- which is why
        no test asserting "the selector exists" caught it.

        So assert the two things the pin actually needs, both of which the broken
        version failed:

        * a **descendant** match, since Streamlit's wrapping depth is not ours to
          predict;
        * an unbroken flex column from the panel down, or ``margin-top: auto``
          resolves to zero however well it matches.
        """
        css = _CHROME_CSS

        assert ":has(div[data-lex-account])" in css
        assert ":has(> div[data-lex-account])" not in css, (
            "a child match binds to Streamlit's wrapper depth, which is not ours"
        )

        # The chain: panel -> content -> user content -> vertical block.
        for testid in ("stSidebarContent", "stSidebarUserContent", "stVerticalBlock"):
            assert f'data-testid="{testid}"' in css, (
                f"{testid} is not made a flex column; the pin has nothing to push into"
            )
        assert css.count("flex-direction: column") >= 2

    def test_01_313_only_the_account_block_gives_up_space(self):
        """Scenario 1.313 (third half): the pin may not squash its neighbours.

        Read from Streamlit 1.58's own styles rather than guessed. ``stSidebarContent``
        is ``position: relative; height: 100%; overflow: auto`` -- a *scroll*
        container, and not a flex container until we make it one. Its header is::

            {{ display: flex, ..., height: theme.sizes.headerHeight }}

        A fixed ``height`` on a block is a fixed height. On a flex item it is a
        starting point, and the default ``flex-shrink: 1`` lets the column take it
        back -- so making the panel a flex column, which the pin requires, hands
        it permission to squash the logo on a short sidebar. The navigation below
        it is the author's, and no more ours to compress.

        The account block is the only thing that flexes, and it may only *grow*:
        ``flex: 1 0 auto``. Letting it shrink would squeeze the way out of the app
        on exactly the sidebar that is already too full, while the real scroll
        container sits one level up and would have handled it.
        """
        css = _rules_only(_CHROME_CSS)

        for testid in ("stSidebarHeader", "stSidebarNav"):
            assert f'data-testid="{testid}"' in css, (
                f"{testid} is free to shrink once the panel is a flex column"
            )
        assert "flex: 0 0 auto" in css, "the header and nav are not held at their size"
        assert "flex: 1 0 auto" in css, "the account block may grow, never shrink"
        assert "flex: 1 1 auto" not in css

    def test_01_313_the_panel_height_is_not_a_guess(self):
        """Scenario 1.313 (second half): no constant stands in for the nav.

        The first working version reserved the header and navigation with
        ``min-height: calc(100vh - 9rem)``. That number is right for exactly one
        app: the navigation's height is however many pages the author declared,
        so a smaller nav leaves the block floating mid-panel and a larger one
        grows a scrollbar in a sidebar that had no reason to scroll.

        ``min-height: 100%`` on a flex column asks for the same thing without
        knowing anything -- and it grows instead of clipping when the content is
        genuinely taller than the panel, which a viewport calculation does not.
        """
        css = _rules_only(_CHROME_CSS)

        assert "min-height: 100%" in css
        assert "100vh" not in css, "the panel's height is measured, not assumed"
        assert "calc(" not in css

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

    def test_01_312_each_slot_gets_the_variant_its_background_needs(self):
        """Scenario 1.312: dark logo for the sidebar, light one for the corner.

        ``st.logo`` fills two slots with different backgrounds. The sidebar is
        brand-navy in either mode, so it takes the white wordmark. The app's
        upper-left -- where the mark moves when the sidebar is COLLAPSED --
        follows the page theme, so it takes the navy one.

        Passing only the dark file is what made the collapsed logo nearly
        invisible: a white wordmark on a light page, which is how this was
        reported.
        """
        calls = []

        class FakeSt:
            def logo(self, image, **kwargs):
                calls.append((image, kwargs))

        render_logo(FakeSt())

        assert len(calls) == 1
        image, kwargs = calls[0]
        assert image.endswith("dark-lex-logo.svg"), "the sidebar slot needs the dark variant"
        assert kwargs["icon_image"].endswith("lex-logo.svg"), (
            "the collapsed slot needs the light variant"
        )

    def test_01_312_the_two_variants_are_not_swapped(self):
        """Scenario 1.312 (second half): check the pixels, not the filenames.

        The names differ by one word, the files differ by three hex values, and
        swapping them produces a logo that is invisible on exactly one surface --
        a change no reviewer would catch and no test asserting filenames would
        either. So assert what each file actually contains.
        """
        dark = _LOGO_PATH.read_text()
        light = _LOGO_COLLAPSED_PATH.read_text()

        # Dark variant: white wordmark, for the navy sidebar.
        assert "#FFFFFF" in dark
        assert "#282f63" not in dark

        # Light variant: navy wordmark, for a light page.
        assert "#282f63" in light
        assert "#FFFFFF" not in light

        # Both carry the brand accent, which is what makes them the same mark.
        assert "#24b6bb" in dark and "#24b6bb" in light

    def test_01_312_a_missing_light_variant_still_renders_the_sidebar_logo(
        self, monkeypatch
    ):
        """Scenario 1.312 (third half): the second file is an enhancement.

        The sidebar is the slot that matters and the one that is always right.
        If the collapsed variant is missing, ``st.logo`` should still be called
        with the dark one rather than skipped -- losing the sidebar logo to fix
        the collapsed corner would be the wrong trade.
        """
        import lex.lex_app.streamlit.sidebar as sb

        monkeypatch.setattr(
            sb, "_LOGO_COLLAPSED_PATH", sb._LOGO_COLLAPSED_PATH.with_name("gone.svg")
        )
        calls = []

        class FakeSt:
            def logo(self, image, **kwargs):
                calls.append((image, kwargs))

        sb.render_logo(FakeSt())
        assert len(calls) == 1
        assert calls[0][0].endswith("dark-lex-logo.svg")
        assert "icon_image" not in calls[0][1]

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

    def test_01_312_the_logo_is_sized_and_aligned_in_both_slots(self):
        """Scenario 1.312 (fourth half): bigger, and lined up with its neighbours.

        Size comes from ``st.logo``'s own parameter rather than CSS -- a native
        API beats a rule that has to survive Streamlit's markup.

        Alignment cannot: each slot sits flush against a different edge. In the
        sidebar the logo was further left than the navigation it heads; in the
        app header, when the sidebar is COLLAPSED, it sat against the window edge
        while the page's own text began well inside it. Both are pure inset
        corrections, so they fall under the same layout-only limit as the
        bottom-pin and degrade the same way -- if the selector stops matching,
        the logo is merely back where Streamlit put it.
        """
        calls = []

        class FakeSt:
            def logo(self, image, **kwargs):
                calls.append((image, kwargs))

        render_logo(FakeSt())
        assert calls[0][1]["size"] == "large", "size belongs to st.logo, not to CSS"

        css = _CHROME_CSS
        assert 'section[data-testid="stSidebar"] [data-testid="stLogo"]' in css
        assert 'header [data-testid="stLogo"]' in css
        # The collapsed logo lines up with the content column, so the inset it
        # uses has to be the one the content uses.
        assert _CONTENT_INSET in css
        assert _CONTENT_INSET.endswith("rem"), "an inset in px will not track text scaling"
