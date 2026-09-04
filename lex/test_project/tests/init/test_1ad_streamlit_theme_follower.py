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

Cluster 01-init, batch 1ad, scenarios 1.261-1.289.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ad_streamlit_theme_follower.py
"""

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from lex.streamlit_theme import (
    DEBUG_PANEL_HEIGHT,
    DEFAULT_MODE,
    LIGHT,
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



    def test_01_283_nothing_reloads_the_page(self):
        """Scenario 1.283: the reload is gone, and with it a whole bug family.

        Every earlier version of this file wrote Streamlit's stored theme key and
        reloaded, on the premise that Streamlit resolves its theme once at boot.
        The premise was false. Streamlit's own menu changes the theme LIVE --
        measured, dark to light with no navigation -- because it goes through
        Streamlit's React state rather than storage.

        The reload was the entire problem the user reported: "it's slow and
        unreliable, I can easily break it." Slow because a change cost a full
        page load, and two when the page booted wrong and had to be corrected.
        Unreliable because an oscillation ledger, a sticky stand-down,
        self-report marking and a one-reload-per-load flag were all needed to
        make reloading survivable -- none of which addressed the theme, and all
        of which could get stuck.

        Deleting the reload does not fix those bugs. It makes them unreachable.
        """
        js = theme_follower_html()

        assert "location.reload" not in js, "the reload is back"
        for gone in ("LEDGER", "STANDOWN", "isSelfReport", "__lexThemeReloading"):
            assert gone not in js, f"machinery for surviving reloads is back: {gone}"

    def test_01_284_it_drives_streamlits_own_control(self):
        """Scenario 1.284: use the control, do not reimplement it.

        Clicking Streamlit's own theme item goes through Streamlit's state, so
        the change applies instantly, persists by Streamlit's own code, and
        cannot drift from whatever key or value format a future version adopts.
        Writing storage reimplemented all three, badly.

        The testids are Streamlit's own testing surface, and the dependency
        fails HARMLESSLY: if the control is not found the theme is left alone,
        with a log saying so. Nothing reloads, nothing is overridden, nothing
        loops -- which is the property the old design could never offer.
        """
        js = theme_follower_html()

        assert "stMainMenuItem-theme-" in js
        assert "stMainMenuButton" in js
        # It must say something when the control is missing, rather than failing mute.
        assert "did not appear" in js

    def test_01_285_the_menu_is_never_seen(self):
        """Scenario 1.285: driving the control must not flash the menu open.

        The theme item only exists in the DOM while the menu is open, so opening
        it is unavoidable. React renders the popover a frame or two later, which
        is long enough to be seen.

        It is masked for the duration and revealed again after -- including when
        nothing ever appears, because a mask left behind would hide the real menu
        from the user permanently. Confirmed live: ``menuEverVisible: false``,
        ``popoverLeftOpen: false``.
        """
        js = theme_follower_html()

        assert "stMainMenuPopover" in js
        assert "opacity:0" in js

        # Revealed again on BOTH exit paths -- the click, and the give-up
        # timeout. A mask left behind would hide the real menu from the user for
        # the rest of the page's life, which is a worse bug than the flash.
        assert "host.setTimeout(finish, 60)" in js, "not revealed after a successful click"
        assert "finish();" in js, "not revealed when the control never appears"
        # The only early return happens before the mask exists.
        before_mask = js[: js.index("doc.head.appendChild(mask)")]
        assert "return false;" in before_mask

    def test_01_286_the_showing_mode_comes_from_streamlit_itself(self):
        """Scenario 1.286: ask Streamlit, do not infer.

        The shim receives Streamlit's real resolved theme in the RENDER event --
        ``{base: "light"|"dark", ...}`` -- and publishes it to the page. This
        reads that.

        Three earlier versions inferred it instead: by measuring the rendered
        background (which read a transparent element as black and so reported
        "dark" for a light page), then by parsing a storage key whose name and
        version we had to guess. Streamlit hands the answer over on every render,
        for free, and has since before any of this was written.

        The storage read survives only as a fallback for a page with no widgets,
        where nothing is receiving RENDER.
        """
        js = theme_follower_html()

        assert "__lexThemeCurrent" in js
        assert "stActiveTheme-" in js, "the no-widget fallback is gone"
        # And never by measuring pixels again.
        for banned in ("getComputedStyle", "backgroundColor", "luma"):
            assert banned not in js, f"the mode is being inferred again, via {banned}"

    def test_01_287_the_entry_points_and_their_guard_share_one_lifetime(self):
        """Scenario 1.287: install guard, entry point and listener all on `host`.

        Streamlit destroys and recreates this component iframe on every rerun
        while the page persists. A guard on the page with a listener on this
        window would survive exactly one render; an entry point published here
        would vanish from under the shim that calls it.
        """
        js = theme_follower_html()

        for on_host in ("host.__lexThemeDriverInstalled",
                        "host.__lexThemeApply",
                        'host.addEventListener("storage"'):
            assert on_host in js, f"{on_host} does not belong to the page"

    def test_01_288_a_stale_url_pin_is_still_removed(self):
        """Scenario 1.288: clean up the parameter older versions left behind.

        A theme pinned in the URL outranks Streamlit's own menu, so the control
        this drives would apply but stop persisting. Earlier versions of this
        script put that parameter there, so it is removed rather than obeyed --
        every other embed option survives.
        """
        pinned = theme_follower_html("dark")

        assert "stripUrlThemePin" in pinned
        assert "kept.push(part)" in pinned
        assert 'searchParams.append("embed_options", v)' in pinned

    def test_01_289_the_first_correction_does_not_wait_to_be_looked_at(self):
        """Scenario 1.289: install must not depend on the page being visible.

        It ran inside ``requestAnimationFrame``, which does not fire while a
        document is not being rendered. Anyone whose Streamlit page finished
        loading unfocused never got the first correction -- the normal case, with
        lex-app and the dashboard open side by side.
        """
        js = theme_follower_html()

        assert "requestAnimationFrame(" not in js
        assert 'apply(stored || DEFAULT_MODE, "install");' in js


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


