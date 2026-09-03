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

Cluster 01-init, batch 1ad, scenarios 1.261-1.280.

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
        # A wrapper rather than `follow` itself, so a widget's report is tagged
        # as such: the oscillation guard in 1.277 has to tell a re-read of
        # existing state from a deliberate change.
        assert "host.__lexThemeFollow = function" in html
        assert 'follow(mode, "widget")' in html

        shim = (
            pathlib.Path(__file__).resolve().parents[3]
            / "lex_app/streamlit/_widget_host_component/frontend/index.html"
        ).read_text()
        assert "window.parent.__lexThemeFollow(mode)" in shim
        # Storage is still written, so the pre-render ordering keeps working.
        assert "window.localStorage.setItem(_themeStorageKey, mode)" in shim


    def test_01_272_the_mode_is_read_from_streamlit_not_guessed(self):
        """Scenario 1.272: the current mode is asked for, never inferred.

        This scenario used to pin a fix to a luma heuristic: the follower
        classified the rendered background, and ``rgba(0, 0, 0, 0)`` parsed to
        four zeroes, which reads as pure black -- so a light page whose element
        painted nothing reported "dark" and the follower silently did nothing,
        every time.

        The heuristic is gone rather than fixed. Streamlit already stores which
        theme is selected, so the question has an exact answer:

            stActiveTheme-<pathname>-v2  ->  "Light" | "Dark" | "System"

        with "System" and unset both meaning the OS decides, which
        ``prefers-color-scheme`` answers exactly. The regression guard is that
        nobody reintroduces measurement -- a future "improvement" that inspects
        pixels brings the whole bug class back with it.
        """
        js = theme_follower_html()

        # Read from Streamlit's own store, scoped the way Streamlit scopes it.
        assert '"stActiveTheme-" + host.location.pathname + "-v"' in js
        assert "prefers-color-scheme: dark" in js

        # And measurement is not merely unused -- it is absent from the CODE.
        # Checked with comments stripped: the note above the replacement explains
        # what the luma heuristic did, and a naive substring search finds that
        # prose rather than any surviving call.
        code = "\n".join(
            line for line in js.splitlines()
            if not line.strip().startswith(("//", "*", "/*"))
        )
        for gone in ("getComputedStyle", "backgroundColor", "luma", "querySelector"):
            assert gone not in code, f"the mode is being inferred again, via {gone}"

    def test_01_276_it_writes_the_menu_rather_than_overriding_it(self):
        """Scenario 1.276: sync cooperates with Streamlit's theme menu.

        The previous mechanism reloaded with ``?embed_options=<mode>_theme``.
        That parameter is the top of Streamlit's resolver, so the Settings menu
        stopped applying -- and stopped SAVING, because Streamlit's own writer
        refuses while a URL theme is present::

            Cae = e => { if (!Pa() || (Rw(), xg() || Sg())) return; ... }

        Reported as "always in dark mode, you cannot change it". Writing the key
        the menu itself writes means the two cannot disagree: the menu keeps
        working, shows the truth, and a choice made there persists.
        """
        js = theme_follower_html()

        # Writes Streamlit's own preference, as the value Streamlit stores.
        assert 'host.localStorage.setItem(' in js
        assert 'JSON.stringify(mode === "dark" ? "Dark" : "Light")' in js

        # And it never MUTATES the URL. The string "embed_options" does still
        # appear -- in the stand-down message that tells a user how to unpin a
        # tag an earlier version left behind -- so the property to assert is the
        # absence of URL writing, not the absence of the word.
        for writer in ("searchParams", "location.replace", "location.href =",
                       "history.pushState", "history.replaceState"):
            assert writer not in js, f"the URL is being rewritten again, via {writer}"
        # A reload is still needed -- Streamlit reads the theme at boot -- but a
        # reload preserves the URL, which is the whole difference.
        assert "host.location.reload()" in js

    def test_01_276_a_url_pinned_theme_makes_it_stand_down(self):
        """Scenario 1.276 (second half): don't fight a parameter we cannot beat.

        A theme in the page's URL outranks the key this writes. Without standing
        down, every load would see a mismatch it can never resolve and spend its
        one reload trying -- a reload per load, forever. Earlier versions of this
        follower PUT such a parameter there, so a tab can still carry one; the
        log line says how to clear it rather than leaving the user stuck.
        """
        pinned = theme_follower_html("dark")
        assert "Standing" in pinned
        assert "without that parameter" in pinned


    def test_01_277_a_reload_loop_is_structurally_impossible(self):
        """Scenario 1.277: the guard against reloading survives the reload.

        Reported as the page flipping back and forth between light and dark
        without stopping -- the worst thing this file can do to a page, and
        caused by the guard being on the wrong object::

            if (host.__lexThemeReloading) return;
            host.__lexThemeReloading = true;
            host.location.reload();          // destroys the window holding it

        That stopped a second reload WITHIN one load and nothing across them.
        Two independent inputs feed ``follow()`` -- the stored agreement and a
        widget reporting its own palette -- so when they disagree, each load
        flips the other way, forever.

        The ledger lives in ``sessionStorage``: it survives a reload and is
        scoped to the tab, which is exactly the lifetime a cross-reload guard
        needs. Recording what was last reloaded FOR is what makes a
        contradiction recognisable rather than merely repeatable.
        """
        js = theme_follower_html()

        assert "sessionStorage" in js, "the guard cannot live on a window it destroys"
        assert "lex.theme.reloads" in js
        # A contradiction is detected by comparing targets, not just counting.
        assert "previous.to !== mode" in js
        assert "NOT reloading again" in js, "and it says so, rather than going quiet"

    def test_01_277_a_deliberate_change_is_never_refused(self):
        """Scenario 1.277 (second half): the guard must not become the bug.

        A cross-reload guard that cannot tell "these two disagree" from "the user
        just changed their mind" would break theme sync to fix the loop --
        trading a visible failure for a silent one.

        A ``storage`` event IS a fresh, deliberate change made somewhere else, so
        it clears the ledger and is always honoured. The install-time read and a
        widget's report are re-reads of existing state, and are the two that can
        argue with each other.
        """
        js = theme_follower_html()

        for reason in ('"install"', '"storage"', '"widget"'):
            assert reason in js, f"follow() cannot distinguish its inputs: {reason}"
        assert 'if (reason === "storage") {' in js
        assert "forgetReloads();" in js

    def test_01_277_the_guard_expires(self):
        """Scenario 1.277 (third half): it stops guarding once the page settles.

        A permanent refusal would be its own bug -- theme sync would work once
        per tab and then quietly stop for the rest of the session. The window
        bounds the episode, not the tab.

        The window is what stops a normal change being mistaken for a loop. It is
        deliberately NOT what stops a loop resuming -- see 1.279, where an
        expiring memory of a contradiction turned out to be the whole reason the
        page kept reloading while someone was working.
        """
        js = theme_follower_html()
        assert "LEDGER_WINDOW_MS" in js
        assert "Date.now() - v.at > LEDGER_WINDOW_MS" in js

    def test_01_278_a_widget_report_may_act_but_may_not_forget(self):
        """Scenario 1.278: the messenger is not silenced, only distrusted.

        This scenario shipped inverted and broke theme following outright.
        Reported as "theme switch isn't working": a light Streamlit page hosting
        a dark widget, with nothing able to reconcile them.

        The reasoning that produced it: a widget re-asserting its palette on
        every rerun was reloading the page mid-use, so a widget report was
        reclassified as "an observation, not a command" and refused a reload
        entirely. That is true of the *re-assertion* and false of the *report*.

        In a same-site deployment the widget frame IS the messenger. lex-app
        writes its preference, the frame reads it (same origin, so unlike the
        cross-site case it genuinely can), and tells the shim. Silencing that
        silences the only carrier -- and the relay cannot cover for it, because
        the shim has already written the same value and a `storage` event does
        not fire for an unchanged one. The change is swallowed whole.

        So every input may ACT. What a widget report may not do is **forget**:
        it must not clear the loop memory, because an assertion arriving several
        times a minute would wipe the record of a contradiction and let a
        bounded loop restart forever. That distinction -- act versus forget -- is
        the one that carries both requirements at once.
        """
        js = theme_follower_html()

        # No early return that stops a widget report before the reload path.
        reload_call = js.index("host.location.reload()")
        for refusal in ('if (reason === "widget") {', 'if (reason !== "storage") return'):
            assert refusal not in js[:reload_call], (
                f"a widget report is refused before it can act: {refusal}"
            )

        # Forgetting is gated on `storage` alone.
        forget_at = js.index("forgetReloads();")
        gate = js.rindex('if (reason === "storage")', 0, forget_at)
        assert forget_at - gate < 400, (
            "clearing the loop memory is not gated on a genuine external change"
        )

    def test_01_279_a_known_contradiction_is_not_forgotten(self):
        """Scenario 1.279: the stand-down outlives the window that detected it.

        The other half of "it reloads by itself". Even with widget reports
        silenced, a windowed memory is the wrong shape for this fact: a
        contradiction between two sources does not heal on a timer, so letting
        the ledger expire meant a fresh episode could begin every window --
        forever, at whatever interval the window happens to be.

        So the two memories have different lifetimes on purpose:

        * the **ledger** expires, so a deliberate change fifteen minutes later is
          not mistaken for the tail of an old loop (1.277)
        * the **stand-down** does not, because "these two disagree" stays true
          until something changes it

        What clears it is a ``storage`` event -- a real, deliberate change made
        somewhere else. That keeps the escape hatch the user actually has (switch
        the theme in lex-app, or in Streamlit's own menu) working.
        """
        js = theme_follower_html()

        assert "lex.theme.standown" in js
        # Sticky: nothing in the script may expire it on a timer.
        standown_lines = [ln for ln in js.splitlines() if "STANDOWN_KEY" in ln]
        assert standown_lines, "the stand-down key is not used"
        assert not any("WINDOW" in ln for ln in standown_lines), (
            "a stand-down that expires is the bug it exists to fix"
        )
        # And a deliberate change still clears it, or the escape hatch is gone.
        forget = js[js.index("function forgetReloads"):]
        forget = forget[: forget.index("}\n\n")]
        assert "STANDOWN_KEY" in forget and "LEDGER_KEY" in forget, (
            "a deliberate change must clear both memories, not just one"
        )


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


class TestCluster1ad_ThemeFollowerBehaviour:
    """The follower's *logic*, not its text.

    Every other class here asserts on the emitted script as a string, which is
    all a Python test can do -- and it is not enough for the one failure this
    file keeps producing. No property of a string proves a page settles.

    So the script is run against a DOM double that models the only distinction
    that matters for a reload loop: state that survives ``location.reload()``
    (``localStorage``, ``sessionStorage``) and state that does not (the window,
    its listeners, every flag on it). Eight cases, including both roads a widget
    report travels and the two real changes that must still be honoured.

    Skipped rather than failed when there is no JS runtime: this is a Python
    repository, and a missing ``node`` is a fact about the machine, not a defect
    in the code under test. It is wired into pytest anyway because the previous
    version of this harness was hand-run, lived in a temporary directory, and
    was gone by the next session -- which is why the loop it had already proved
    fixed came back in a different form.
    """

    def test_01_280_the_follower_settles(self):
        """Scenario 1.280: run the follower and watch it stop.

        Case 7 is the one that earns this class. Silencing the direct
        ``__lexThemeFollow`` route (1.278) looked like a complete fix and was
        not: the shim writes the agreed key *before* calling the page, and a
        same-origin iframe's write is delivered to its parent as a ``storage``
        event -- indistinguishable from a person changing the theme in another
        tab. The identical report simply arrived by the other road and reloaded
        the page anyway.
        """
        node = shutil.which("node")
        if node is None:
            pytest.skip("no JS runtime on this machine; the follower's logic is unproven here")

        harness = pathlib.Path(__file__).parent / "harness" / "theme_follower_harness.mjs"
        with tempfile.TemporaryDirectory() as tmp:
            script = pathlib.Path(tmp) / "follower.html"
            script.write_text(theme_follower_html(), encoding="utf-8")
            result = subprocess.run(
                [node, str(harness), str(script)],
                capture_output=True, text=True, timeout=60,
            )

        assert result.returncode == 0, (
            "the follower does not settle:\n" + result.stdout + result.stderr
        )
        # Guard the guard: a harness that silently stopped running its cases
        # would pass forever.
        assert result.stdout.count("  ok   ") >= 19, result.stdout
