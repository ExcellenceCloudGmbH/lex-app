"""Theme agreement between lex-app and Streamlit.

Both halves of the handshake need the same storage key and the same idea of what
``embed_options`` means, and neither half can import the other: ``proxy.py`` runs
as a bare Starlette app with no Django settings, while ``streamlit_app.py`` only
exists inside a Streamlit script run. So the shared, dependency-free parts live
here -- importing this module pulls in nothing but the standard library.

The remaining mirror is ``THEME_STORAGE_KEY`` in the frontend's ``themeRelay.ts``.
That one is unavoidable: it is a different language in a different repository.
"""

from __future__ import annotations

import json
from typing import Iterable

#: Storage key every participant agrees on. Mirrored in the frontend's
#: themeRelay.ts -- change one and the handshake silently stops working.
THEME_STORAGE_KEY = "lex.theme.mode"

#: The two values a mode can take. Anything else is treated as "unknown".
LIGHT = "light"
DARK = "dark"

#: What a page shows when nobody has expressed a preference.
#:
#: Light, to agree with lex-app, which sets ``defaultTheme="light"`` for the same
#: reason: left alone, both products fall back to the OS preference, so a
#: dark-OS user was handed a dark app and a dark dashboard without ever choosing
#: one. A stored choice still wins on both sides -- this decides the FIRST load
#: only.
DEFAULT_MODE = LIGHT


def embed_theme_from_params(values: Iterable[object]) -> str:
    """Read a mode out of raw ``embed_options`` values.

    Streamlit documents repeated parameters (``?embed_options=a&embed_options=b``)
    but comma-joined values appear in hand-written URLs, so both are accepted.

    Returns ``""`` when no theme is requested -- which is the common case, and
    means the page is showing whatever the OS or ``config.toml`` decided. Dark
    wins a contradictory URL, arbitrarily but predictably; the alternative is
    raising over a query string nobody will read the error for.
    """
    flat = {part.strip() for value in values for part in str(value).split(",")}
    if "dark_theme" in flat:
        return DARK
    if "light_theme" in flat:
        return LIGHT
    return ""


#: Spliced into the follower only when diagnostics are on. See
#: :func:`theme_debug_enabled`.
_DEBUG_PANEL_JS = """    // ── Visible diagnostics ──────────────────────────────────────────────
    // Spliced in only when LEX_THEME_DEBUG is set, rather than shipped inert and
    // branched at runtime: a page that never asked for this should not carry the
    // code, and a future edit to a runtime guard cannot leak a debug box into a
    // production dashboard.
    //
    // The console is the natural home for this, but a theme problem crosses
    // three browsing contexts and is usually reported with a screenshot. A panel
    // in the page turns one screenshot into the whole picture.
    var box = document.createElement("pre");
    box.style.cssText =
      "margin:0;padding:8px 10px;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;" +
      "border:1px solid rgba(128,128,128,.45);border-radius:6px;white-space:pre-wrap;" +
      "color:inherit;background:rgba(128,128,128,.08)";
    document.body.appendChild(box);

    function line(label, value) {
      return label + ": " + value + "\\n";
    }

    function paint() {
      var agreed, storageErr = "";
      try { agreed = host.localStorage.getItem(KEY); }
      catch (e) { storageErr = " (unreadable: " + e.name + ")"; }

      var report = host.__lexThemeLastReport;

      box.textContent =
        "lex-theme diagnostics (LEX_THEME_DEBUG)\\n" +
        line("page showing", effectiveMode() || "unknown") +
        line("streamlit menu", (selection() || "(unset)") + "   key " + activeThemeKey()) +
        line("url pins a theme", URL_MODE ? URL_MODE + " -- sync stood down" : "no") +
        line("agreed with lex-app", (agreed === null || agreed === undefined
              ? "(nothing yet)" : agreed) + storageErr) +
        line("widget last reported", report
              ? report.mode + " via " + report.route + ", " + report.ago() + "s ago"
              : "(nothing yet -- no widget has announced a theme)") +
        line("reload already used", host.__lexThemeReloading ? "yes" : "no");
    }

    paint();
    host.setInterval(paint, 1000);
"""


_FOLLOWER_HTML = """<script>
  (function () {
    var KEY = __KEY__;
    var DEFAULT_MODE = __DEFAULT_MODE__;
    var URL_MODE = __URL_MODE__;

    // This runs in a srcdoc iframe: same origin as the Streamlit server, so it
    // shares this origin's localStorage and may read and reload its parent.
    // `parent`, not `top` -- when lex-app embeds Streamlit, `top` is lex-app's
    // page on another origin and every access to it throws.
    var host = window.parent;
    if (!host || host === window) return;

    // A theme pinned in the page's own URL outranks everything below, including
    // the key this script writes. Stand down rather than fight it: without this,
    // every load would see a mismatch it can never resolve and spend its one
    // reload on it. Earlier versions of this follower PUT such a parameter
    // there, so a tab can still be carrying one.
    if (URL_MODE) {
      console.info(
        "[lex-theme] this page's URL pins embed_options=" + URL_MODE + "_theme, " +
        "which outranks both this sync and Streamlit's own theme menu. Standing " +
        "down. Load the page once without that parameter to hand control back."
      );
      return;
    }

    // Guard AND listener both belong to `host`. Streamlit destroys and recreates
    // this iframe across reruns while the page persists, so a flag on the page
    // with a listener on this window would survive exactly one render.
    if (host.__lexThemeFollowerInstalled) return;
    host.__lexThemeFollowerInstalled = true;

    // ── Streamlit's OWN theme preference ─────────────────────────────────
    // Cooperating with it rather than overriding it, which is the whole point
    // of this version. The previous approach reloaded with
    // ?embed_options=<mode>_theme; that sits above everything in Streamlit's
    // resolver, so the Settings menu stopped applying AND stopped saving --
    // Streamlit's own writer bails out while a URL theme is present:
    //
    //     Cae = e => { if (!Pa() || (Rw(), xg() || Sg())) return; ... }
    //
    // Writing this key instead means the menu keeps working, shows the truth,
    // and a choice made there persists. Key shape and version are Streamlit's:
    //     `stActiveTheme-${location.pathname}-v2`  ->  JSON "Light"|"Dark"|"System"
    var STREAMLIT_THEME_VERSION = 2;

    function activeThemeKey() {
      return "stActiveTheme-" + host.location.pathname + "-v" + STREAMLIT_THEME_VERSION;
    }

    /** Streamlit's stored selection: "Light", "Dark", "System", or null. */
    function selection() {
      try {
        var raw = host.localStorage.getItem(activeThemeKey());
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    }

    /**
     * The mode actually on screen.
     *
     * Read, not measured. The previous version classified the rendered
     * background by luma, which misread a transparent element as pure black and
     * so reported "dark" for a light page -- a silent no-op every time. Asking
     * Streamlit what it selected removes that whole class of bug: "System" and
     * unset both mean the OS decides, and the OS is a question with an exact
     * answer.
     */
    function effectiveMode() {
      var sel = selection();
      if (sel === "Light") return "light";
      if (sel === "Dark") return "dark";
      try {
        return host.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      } catch (e) {
        return "";
      }
    }

    // ── Oscillation guard ────────────────────────────────────────────────
    // `location.reload()` destroys the window, so a flag on it resets every
    // load: it stops a second reload WITHIN one load and nothing across them.
    // Two independent inputs feed follow() -- the stored agreement, and a widget
    // reporting its own palette -- and when those disagree each load flips the
    // other way. That is a reload loop, and it is the worst thing this file can
    // do to a page.
    //
    // sessionStorage survives a reload and is per-tab, which is exactly the
    // lifetime the guard needs. Recording what we last reloaded FOR lets a
    // contradiction be recognised rather than acted on.
    var LEDGER_KEY = "lex.theme.reloads";
    //: Set once a contradiction has been seen, and never expired. The ledger
    //: below is windowed so a normal change is not mistaken for a loop; THIS is
    //: what stops a permanent disagreement restarting the loop every window.
    //: Cleared only by a deliberate change arriving over `storage`.
    var STANDOWN_KEY = "lex.theme.standown";
    //: How long after a widget marks a write we still recognise the resulting
    //: `storage` event as that widget's own. Generous: the event is dispatched
    //: within the same task, so this only has to survive scheduling.
    var SELF_REPORT_MS = 3000;
    var LEDGER_WINDOW_MS = 15000;
    var LEDGER_MAX = 2;

    function ledger() {
      try {
        var raw = host.sessionStorage.getItem(LEDGER_KEY);
        var v = raw ? JSON.parse(raw) : null;
        if (!v || typeof v.n !== "number") return null;
        // Outside the window this is a new episode, not an oscillation.
        return Date.now() - v.at > LEDGER_WINDOW_MS ? null : v;
      } catch (e) {
        return null;
      }
    }

    function noteReload(mode) {
      try {
        var prev = ledger();
        host.sessionStorage.setItem(LEDGER_KEY, JSON.stringify({
          n: (prev ? prev.n : 0) + 1, at: Date.now(), to: mode
        }));
      } catch (e) {}
    }

    function forgetReloads() {
      try {
        host.sessionStorage.removeItem(LEDGER_KEY);
        host.sessionStorage.removeItem(STANDOWN_KEY);
      } catch (e) {}
    }

    /**
     * True when this storage change is a widget in THIS page describing itself.
     *
     * The shim writes the agreed key and, being a same-origin document, that
     * write reaches this page as a `storage` event -- the same shape as a real
     * change made in another tab. Without this, silencing the direct
     * `__lexThemeFollow` route accomplished nothing: the identical report simply
     * arrived by the other road and reloaded the page anyway.
     */
    function isSelfReport(mode) {
      var report;
      try { report = host.__lexThemeSelfReport; } catch (e) { return false; }
      if (!report || report.mode !== mode) return false;
      return (Date.now() - report.at) <= SELF_REPORT_MS;
    }

    function standingDown() {
      try { return host.sessionStorage.getItem(STANDOWN_KEY); } catch (e) { return null; }
    }

    /**
     * @param mode    "light" or "dark"
     * @param reason  "install" | "storage" | "widget" -- how we heard about it.
     *                A `storage` event is a fresh, deliberate change made
     *                somewhere else, so it clears the ledger and is always
     *                honoured; the other two are re-reads of existing state and
     *                are the ones that can argue with each other.
     */
    function follow(mode, reason) {
      if (mode !== "light" && mode !== "dark") return;
      var now = effectiveMode();
      console.info("[lex-theme] asked for", mode, "(" + (reason || "?") + ")",
                   "; showing", now || "(unknown)",
                   "; menu is", selection() || "(unset -> system)");
      if (!now || now === mode) return;     // already right, or unknowable

      // Every input may ACT. What differs is whether it may FORGET.
      //
      // A `widget` report is the carrier of the change in a same-site
      // deployment: lex-app writes its preference, the widget frame reads it
      // (same origin, so it can), and tells us. Refusing to act on that -- which
      // an earlier version of this function did, to stop mid-use reloads --
      // silences the only messenger and theme following stops entirely.
      //
      // But it must not clear the loop memory below. A widget re-asserts its
      // palette on every rerun, so treating each assertion as fresh news would
      // wipe the record of a contradiction several times a minute and let a
      // bounded loop restart forever. Only a `storage` event that no widget
      // claimed is a genuinely new decision made somewhere else.
      if (reason === "storage") {
        // A deliberate change outranks every refusal below -- including one this
        // load already made. Otherwise the escape hatch we tell people about in
        // the stand-down message ("change the theme in lex-app or Streamlit's
        // menu") would be closed by the very refusal that suggests it.
        forgetReloads();
        host.__lexThemeReloading = false;
      }

      var stood = standingDown();
      if (stood) {
        host.__lexThemeReloading = true;
        console.warn(
          "[lex-theme] standing down for this tab: a contradiction was already " +
          "seen (" + stood + "). Reloading again would only flip it back. Change " +
          "the theme in lex-app or Streamlit's menu to clear this."
        );
        return;
      }

      var previous = ledger();
      if (previous && (previous.n >= LEDGER_MAX || previous.to !== mode)) {
        // Sticky: a contradiction does not resolve itself, so remembering it for
        // only the window would let a fresh episode start every window forever.
        try { host.sessionStorage.setItem(STANDOWN_KEY, mode + "/" + now); } catch (e) {}
        host.__lexThemeReloading = true;    // stop asking for the rest of this load
        console.warn(
          "[lex-theme] NOT reloading again. This page already reloaded for '" +
          previous.to + "' and is now being asked for '" + mode + "' while showing '" +
          now + "'. Two sources disagree about the theme, and reloading again would " +
          "just flip it back. Leaving the page as it is."
        );
        return;
      }

      if (host.__lexThemeReloading) return; // one reload per load
      host.__lexThemeReloading = true;
      noteReload(mode);
      try {
        // Exactly what the Settings menu would write, so the two cannot disagree.
        host.localStorage.setItem(
          activeThemeKey(),
          JSON.stringify(mode === "dark" ? "Dark" : "Light")
        );
        // Streamlit reads the theme at boot, so a reload is still required --
        // but the URL is left alone, so this is the only cost and the menu
        // survives it.
        host.location.reload();
      } catch (e) {
        host.__lexThemeReloading = false;
      }
    }

    // Published so a writer already inside this frame tree -- the widget-host
    // shim -- can call in directly rather than going through a storage event.
    host.__lexThemeFollow = function (mode) { follow(mode, "widget"); };

    // A mode agreed before this block rendered, which is the common ordering.
    // With nothing agreed, fall back to DEFAULT_MODE rather than leaving the
    // page on Streamlit's own default, which is the OS preference.
    host.requestAnimationFrame(function () {
      var stored = null;
      try { stored = host.localStorage.getItem(KEY); } catch (e) {}
      follow(stored || DEFAULT_MODE, "install");
    });

    // `storage` fires in every OTHER window of this origin, which is how a
    // writer without a handle to this page gets through.
    host.addEventListener("storage", function (ev) {
      if (ev.key !== KEY) return;
      // Route, not authority: the same event carries both a person changing the
      // theme in another tab and a widget in this page announcing its own. Only
      // the first is a reason to reload.
      follow(ev.newValue, isSelfReport(ev.newValue) ? "widget" : "storage");
    });

    console.info("[lex-theme] follower ready; showing", effectiveMode(),
                 "; menu", selection() || "(unset -> system)");
__DEBUG_PANEL__  })();
</script>
"""


#: Height the follower block needs when the diagnostics panel is on. Zero
#: otherwise -- an inert block must not take space.
DEBUG_PANEL_HEIGHT = 132


def theme_follow_enabled(environ: "dict[str, str] | None" = None) -> bool:
    """Whether a Streamlit page should follow lex-app's theme. ON by default.

    Following works by reloading with ``?embed_options=<mode>_theme``. That
    parameter is the top of Streamlit's own precedence -- it outranks the stored
    theme and Streamlit's theme menu alike -- so the cost is real and worth
    stating plainly: **on a following page, Streamlit's own theme menu stops
    having any effect**, and the app file cannot override it either, because a
    query parameter is not something app code gets a say in.

    That is the deliberate trade. The two surfaces are meant to be one product,
    so lex-app decides the mode and Streamlit matches it, rather than a dashboard
    showing branded widgets on a mismatched page.

    Opt out per deployment when a page needs its own theme control::

        LEX_THEME_FOLLOW=0

    A page pinned by an earlier follow is freed by opening it once without
    ``embed_options`` in the URL, once following is off.
    """
    import os

    raw = (environ if environ is not None else os.environ).get("LEX_THEME_FOLLOW", "")
    if raw.strip() == "":
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def theme_debug_enabled(environ: "dict[str, str] | None" = None) -> bool:
    """Whether to render the visible diagnostics panel.

    Off unless ``LEX_THEME_DEBUG`` is set to something truthy. Accepts the usual
    spellings rather than only ``1``, because an operator who writes ``true`` and
    sees nothing happen will reasonably conclude the feature is broken.
    """
    import os

    raw = (environ if environ is not None else os.environ).get("LEX_THEME_DEBUG", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def theme_follower_html(url_mode: str = "", debug: bool = False) -> str:
    """The script that makes a Streamlit page follow a theme change.

    A running Streamlit script cannot repaint itself -- the theme arrives as a
    boot parameter -- so following a change means reloading with a corrected
    ``embed_options``. Everything here exists to make sure that reload happens
    only when it genuinely has to:

    * it compares against the mode actually on screen, measured rather than
      assumed, so a tab the user opened themselves (no ``embed_options`` at all)
      still participates;
    * it does nothing when it cannot measure, degrading to no sync rather than to
      a reload loop;
    * it reloads at most once per change, and installs itself once per window.

    ``url_mode`` is what this page's own URL asks for, from
    :func:`embed_theme_from_params`. Pass ``""`` when the URL asks for nothing.

    Known cost, stated rather than hidden: the reload discards in-progress widget
    state on the page. That is the price of Streamlit's boot-time theming.
    """
    return (
        _FOLLOWER_HTML.replace("__KEY__", _js_literal(THEME_STORAGE_KEY))
        .replace("__DEBUG_PANEL__", _DEBUG_PANEL_JS if debug else "")
        .replace("__URL_MODE__", _js_literal(url_mode or ""))
        .replace("__DEFAULT_MODE__", _js_literal(DEFAULT_MODE))
    )


def _js_literal(value: str) -> str:
    """A string as a JS literal that is safe inside an inline ``<script>``.

    ``json.dumps`` alone is not enough. It escapes what JavaScript needs, but the
    HTML parser finds ``</script>`` before JavaScript ever sees the string, so a
    value containing that sequence ends the element early and anything after it
    becomes markup. Escaping the angle brackets as ``\\u003c``/``\\u003e`` keeps
    the value identical to JavaScript while making the sequence unspellable to
    the HTML parser.
    """
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
