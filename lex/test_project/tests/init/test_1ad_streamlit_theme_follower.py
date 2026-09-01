"""Intent: a theme change in lex-app must reach Streamlit, and must not misfire.

Streamlit's theme is boot-time configuration, so a page can only follow a change
by reloading. That makes the cost of a wrong decision high -- a needless reload
discards whatever the user had typed -- and the cost of a missed one merely
cosmetic. These tests pin the decisions on the Python side of that trade:

* what a URL is asking for, including the forms Streamlit does not document;
* that "asking for nothing" is distinguishable from "asking for light", because
  the first must not trigger a reload and the second may;
* that the storage key has exactly one definition, since the relay in proxy.py
  and the follower here silently stop talking if it drifts;
* that the emitted script reaches its host through ``parent`` rather than
  ``top``, which throws when lex-app embeds Streamlit cross-origin.

Not covered here: the emitted script's runtime behaviour. It was exercised
against DOM doubles during development -- seven cases: follows a real change,
no-ops when already correct, preserves ``embed`` and unrelated ``embed_options``,
refuses to act on an unmeasurable background, reloads once under an event burst,
ignores a garbage mode. That harness needs a JS runtime, which this repository
does not have, so it is not in CI. The batch note records the gap.

Cluster 01-init, batch 1ad, scenarios 1.261-1.274.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ad_streamlit_theme_follower.py
"""

import json
import pathlib

import pytest

from lex.streamlit_theme import (
    DEBUG_PANEL_HEIGHT,
    THEME_STORAGE_KEY,
    theme_debug_enabled,
    theme_follow_enabled,
    embed_theme_from_params,
    theme_follower_html,
)

pytestmark = pytest.mark.init


class TestCluster1ad_StreamlitThemeFollower:
    """Reading a theme out of a URL, and the script that acts on it."""

    def test_01_261_reads_repeated_embed_options(self):
        """Scenario 1.261: the documented repeated-parameter form is understood.

        Given ?embed_options=show_toolbar&embed_options=dark_theme
        When the mode is read
        Then it is dark, and the unrelated option is simply ignored
        """
        assert embed_theme_from_params(["show_toolbar", "dark_theme"]) == "dark"

    def test_01_262_reads_comma_joined_embed_options(self):
        """Scenario 1.262: the comma-joined form is understood too.

        Streamlit documents repeated parameters, but hand-written and
        copy-pasted URLs use commas. Accepting one and not the other would
        produce a page that ignores the theme with nothing to show why.
        """
        assert embed_theme_from_params(["show_toolbar,light_theme"]) == "light"
        assert embed_theme_from_params([" dark_theme , show_toolbar "]) == "dark"

    def test_01_263_absent_theme_is_not_light(self):
        """Scenario 1.263: a URL asking for no theme reports no theme.

        This is the distinction the whole design rests on. A page with no
        ``embed_options`` is showing the OS preference or a config default -- not
        necessarily light -- so the caller must be able to tell "unspecified"
        from "light" and decline to reload on a guess.
        """
        assert embed_theme_from_params([]) == ""
        assert embed_theme_from_params(["show_toolbar"]) == ""
        assert embed_theme_from_params([""]) == ""

    def test_01_264_contradictory_url_resolves_deterministically(self):
        """Scenario 1.264: a URL naming both themes picks one, always the same.

        Raising would be defensible, but nothing reads an exception from a query
        string. Choosing predictably keeps the page usable and keeps this
        function total.
        """
        both = ["light_theme", "dark_theme"]
        assert embed_theme_from_params(both) == "dark"
        assert embed_theme_from_params(list(reversed(both))) == "dark"

    def test_01_265_script_carries_the_agreed_key_and_no_placeholders(self):
        """Scenario 1.265: the emitted script is complete and uses the real key.

        A missed replacement would ship ``__KEY__`` to the browser, where it
        fails silently -- the listener simply never matches.
        """
        html = theme_follower_html("dark")
        assert "__KEY__" not in html
        assert "__URL_MODE__" not in html
        assert f'"{THEME_STORAGE_KEY}"' in html
        assert '"dark"' in html

    def test_01_266_key_has_exactly_one_python_definition(self):
        """Scenario 1.266: proxy.py does not define its own copy of the key.

        The relay writes the key and the follower reads it, from two modules that
        cannot import each other's frameworks. A second literal is the failure
        mode this guards: both sides keep working alone, and theme sync just
        stops, with nothing logged anywhere.
        """
        proxy = (pathlib.Path(__file__).resolve().parents[3] / "proxy.py").read_text()
        assert "from lex.streamlit_theme import THEME_STORAGE_KEY" in proxy
        assert f'"{THEME_STORAGE_KEY}"' not in proxy, (
            "proxy.py contains a second literal copy of the storage key"
        )

    def test_01_267_reaches_its_host_via_parent_not_top(self):
        """Scenario 1.267: the script never touches ``window.top``.

        When lex-app embeds Streamlit, ``top`` is lex-app's page on a different
        origin and every property access throws, killing the follower for exactly
        the users who most need it. ``parent`` is the Streamlit page in both the
        embedded and standalone cases.
        """
        html = theme_follower_html()
        assert "window.parent" in html
        assert "window.top" not in html
        # `top` is also a bare global alias for `window.top`, so a future edit
        # writing `top.location` would reintroduce the same fault.
        assert "top." not in html.replace("window.top", "")


    def test_01_270_guard_and_listener_share_one_lifetime(self):
        """Scenario 1.270: the listener is attached to the object being guarded.

        Streamlit destroys and recreates a component iframe across reruns, while
        the Streamlit page persists. So a flag on the page paired with a listener
        on the iframe's own window survives exactly one render: the next iframe
        sees the flag and returns early, and the iframe that held the listener is
        already gone. Nothing is listening, nothing is logged, and the theme
        simply stops following after the first interaction.

        Both must live on the same object. Since the flag has to be on the page
        (that is what makes it idempotent across reruns), the listener goes there
        too.
        """
        html = theme_follower_html("dark")
        assert "host.__lexThemeFollowerInstalled" in html
        assert "host.addEventListener(" in html
        assert "window.addEventListener(" not in html, (
            "listener attached to the component iframe, which Streamlit recreates"
        )


    def test_01_271_follower_publishes_a_direct_entry_point(self):
        """Scenario 1.271: a writer inside the frame tree can skip the storage hop.

        The embedded path used to be widget -> shim -> localStorage -> storage
        event -> follower -> reload. Every link there is silent when it breaks,
        and three of them were only reachable by guessing. The shim is already
        inside the page's frame tree and same-origin with it, so it can call the
        follower outright.

        The storage route is not removed -- it is the only way in for a writer
        with no handle to the page (the relay iframe, a sibling tab, and a shim
        that reports before the follower has rendered).
        """
        html = theme_follower_html("dark")
        assert "host.__lexThemeFollow = follow;" in html

        shim = (
            pathlib.Path(__file__).resolve().parents[3]
            / "lex_app/streamlit/_widget_host_component/frontend/index.html"
        ).read_text()
        assert "window.parent.__lexThemeFollow(mode)" in shim
        # Storage is still written, so the pre-render ordering keeps working.
        assert "window.localStorage.setItem(_themeStorageKey, mode)" in shim


    def test_01_272_transparent_background_is_not_read_as_dark(self):
        """Scenario 1.272: a transparent background yields no answer, not "dark".

        This was the bug that made theme sync appear completely dead. The
        measurement parsed ``rgba(0, 0, 0, 0)`` into four zeroes, passed the
        ``length < 3`` check, and computed a luma of 0 -- pure black. So any page
        whose measured element painted nothing of its own reported "dark". On a
        light page asked to become dark, the follower then saw ``now === mode``
        and returned. A silent no-op, every single time, with a log line claiming
        the page was already correct.

        A transparent colour carries no information. The guard must be on alpha,
        not on component count, and the fallbacks must be tried in order:
        another element that does paint, then the OS preference -- which is what
        Streamlit itself follows when the URL names no theme.
        """
        html = theme_follower_html()

        # Alpha zero is rejected explicitly. Component count cannot catch it:
        # rgba(0,0,0,0) has four components, one more than the old guard needed.
        assert "parseFloat(parts[3]) === 0" in html

        # More than one candidate element, because which one carries the theme
        # background is Streamlit's business and has moved between versions.
        for selector in (".stApp", "stAppViewContainer", "d.body", "d.documentElement"):
            assert selector in html, f"missing background candidate: {selector}"

        # And a reasoned last resort rather than a guess.
        assert "prefers-color-scheme: dark" in html


    def test_01_273_diagnostics_are_off_and_weightless_by_default(self):
        """Scenario 1.273: the diagnostics panel costs nothing unless asked for.

        A theme problem spans three browsing contexts, so the panel exists to
        make one screenshot sufficient. But it renders into a block on the page,
        and a block that is accidentally always on would put a debug box at the
        bottom of every production dashboard.

        Two properties: the flag defaults off for every spelling of "not set",
        and when off the emitted script contains no panel code to run.
        """
        for value in ({}, {"LEX_THEME_DEBUG": ""}, {"LEX_THEME_DEBUG": "0"},
                      {"LEX_THEME_DEBUG": "false"}, {"LEX_THEME_DEBUG": "no"}):
            assert theme_debug_enabled(value) is False, value

        # Accepts the spellings an operator actually types -- someone who writes
        # "true" and sees nothing would reasonably call the feature broken.
        for value in ("1", "true", "TRUE", "yes", "on"):
            assert theme_debug_enabled({"LEX_THEME_DEBUG": value}) is True, value

        # Spliced, not gated: when off, the panel code is ABSENT rather than
        # present-and-skipped. A runtime guard would ship this to every page and
        # would only have to be edited wrong once to surface in production.
        off = theme_follower_html("", debug=False)
        assert "lex-theme diagnostics" not in off
        assert "setInterval" not in off, "an inert page must not schedule work"

        on = theme_follower_html("", debug=True)
        assert on.count("lex-theme diagnostics") == 1
        assert len(on) > len(off), "the panel should add code, not replace it"
        assert DEBUG_PANEL_HEIGHT > 0, "an on panel needs room or it renders clipped"

        # Whichever variant is emitted, the follower itself is unchanged --
        # diagnostics must observe the mechanism, never alter it.
        for html in (off, on):
            assert "host.__lexThemeFollow = follow;" in html
            assert 'host.addEventListener("storage"' in html
            assert "__DEBUG" not in html, "unreplaced marker would ship to the browser"


    def test_01_274_following_is_on_but_escapable(self):
        """Scenario 1.274: on by default, and switchable off per deployment.

        The two surfaces are meant to read as one product, so lex-app decides the
        mode and Streamlit matches -- that is the intended default.

        It has a cost that must stay visible rather than being discovered: a
        following page reloads with ``?embed_options=<mode>_theme``, which is the
        top of Streamlit's precedence, so **Streamlit's own theme menu stops
        having any effect** and the app file cannot override it either. This was
        reported once as "always in dark mode, you cannot change it", so the
        opt-out is part of the contract and is tested, not just documented.
        """
        # On unless explicitly switched off -- including when the variable is
        # present but blank, which is what an unfilled deployment template
        # produces and must not read as "disabled".
        for value in ({}, {"LEX_THEME_FOLLOW": ""}, {"LEX_THEME_FOLLOW": "   "},
                      {"LEX_THEME_FOLLOW": "1"}, {"LEX_THEME_FOLLOW": "true"}):
            assert theme_follow_enabled(value) is True, value

        # Every spelling an operator reaching for the escape hatch would try.
        for value in ("0", "false", "FALSE", "no", "off", "Off"):
            assert theme_follow_enabled({"LEX_THEME_FOLLOW": value}) is False, value


class TestCluster1ad_ThemeEnvelopeWiring:
    """The in-frame path: a widget tells the host page its palette directly.

    The relay covers a Streamlit window lex-app holds no handle to. This covers
    the opposite and commoner case -- widgets embedded IN a Streamlit page, where
    a postMessage up the frame boundary needs no storage at all. That matters
    because a cross-site relay frame gets partitioned storage in current
    browsers, so on separate domains the relay writes somewhere the standalone
    page never reads, while this path is unaffected.
    """

    def test_01_269_storage_key_reaches_the_shim(self):
        """Scenario 1.269: the key is threaded from Python to the static shim.

        The shim writes the storage key that wakes the follower, and it cannot
        define the key itself without becoming a second copy. So Python passes
        it. Every link is silent if broken -- a dropped kwarg leaves the shim with
        no key, and its handler returns without writing, which looks exactly like
        a theme that never changed.
        """
        import ast
        import inspect

        from lex.lex_app.streamlit._widget_host_component import render_widget_host

        # Link 1: the component accepts it and forwards it.
        assert "theme_storage_key" in inspect.signature(render_widget_host).parameters
        forwarded = ast.parse(inspect.getsource(render_widget_host))
        call = next(
            n
            for n in ast.walk(forwarded)
            if isinstance(n, ast.Call) and any(k.arg == "url" for k in n.keywords)
        )
        assert "theme_storage_key" in {k.arg for k in call.keywords}

        # Link 2: every host render supplies it. Both call sites — the page and
        # the log dialog — or the dialog would open unthemed.
        host = (pathlib.Path(__file__).resolve().parents[3] / "lex_app/streamlit/widgets/host.py").read_text()
        assert host.count("theme_storage_key=THEME_STORAGE_KEY") == 2
        assert "from lex.streamlit_theme import THEME_STORAGE_KEY" in host

        # Link 3: the shim reads it from args rather than hardcoding a copy.
        shim = (
            pathlib.Path(__file__).resolve().parents[3]
            / "lex_app/streamlit/_widget_host_component/frontend/index.html"
        ).read_text()
        assert "args.theme_storage_key" in shim
        assert f'"{THEME_STORAGE_KEY}"' not in shim, "shim hardcodes a second copy of the key"


class TestCluster1ad_ThemeFollowerEncoding:
    """The mode reaches JavaScript as data, not as source text."""

    @pytest.mark.parametrize(
        "mode",
        [
            "dark",
            '"; alert(1); //',            # breaks out of a naive JS string
            "</script><script>alert(1)</script>",  # breaks out of the ELEMENT
            "dark\\",
            "\n",
        ],
    )
    def test_01_268_mode_is_encoded_not_interpolated(self, mode):
        """Scenario 1.268: an odd mode stays data, and stays intact.

        ``embed_theme_from_params`` only ever returns "", "light" or "dark", so
        this is defence in depth rather than a live hole. It is worth pinning
        because ``theme_follower_html`` is a public function, and both cheap ways
        to write it are wrong: an f-string loses to the quote case, and plain
        ``json.dumps`` loses to the ``</script>`` case -- the HTML parser closes
        the element before JavaScript ever reads the quoting.

        The property asserted is round-trip fidelity: exactly one script element,
        and the literal JavaScript receives decodes back to what was passed.
        """
        html = theme_follower_html(mode)

        assert html.count("<script") == 1
        assert html.count("</script>") == 1, "the value terminated the element early"

        line = next(ln for ln in html.splitlines() if "var URL_MODE" in ln)
        literal = line.split("=", 1)[1].strip().rstrip(";")
        assert json.loads(literal) == mode, "JavaScript would see a different value"
