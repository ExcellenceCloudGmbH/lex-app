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


#: The oldest Streamlit whose internals this module is written against.
#:
#: Not a preference. The theme sync reads and drives Streamlit's OWN surfaces --
#: the stored-theme key and the theme control in its menu -- and both changed
#: shape after this version:
#:
#:   1.54:  key `stActiveTheme-<pathname>`,   no theme control in the menu
#:   1.58:  key `stActiveTheme-<pathname>-v2`, `stMainMenuItem-theme-Light|Dark`
#:
#: On an older build the sync therefore writes a key nobody reads and looks for
#: a control that does not exist. It fails silently and completely, which is
#: exactly how it presented: "the switch never works", through four rewrites,
#: while every probe against a supported build passed.
#:
#: `requirements.txt` has said `streamlit>=1.58` all along. An unenforced floor
#: is not a floor -- hence :func:`streamlit_version_shortfall`.
MINIMUM_STREAMLIT = (1, 58)


def streamlit_version_shortfall(installed: str) -> str:
    """Explain why *installed* is too old, or ``""`` when it is fine.

    Takes the version as a string so it is testable without importing Streamlit,
    and returns prose rather than a bool because the caller's only useful action
    is to show a human what to do about it.
    """
    try:
        parts = tuple(int(p) for p in str(installed).split(".")[:2])
    except (TypeError, ValueError):
        return ""          # unparseable: say nothing rather than cry wolf
    if len(parts) < 2 or parts >= MINIMUM_STREAMLIT:
        return ""
    want = ".".join(str(p) for p in MINIMUM_STREAMLIT)
    return (
        f"Streamlit {installed} is installed, but lex-app needs >= {want} "
        f"(requirements.txt says so). Below that, Streamlit stores its theme "
        f"under a different key and has no theme control in its menu, so light/"
        f"dark sync between lex-app and Streamlit CANNOT work -- it fails "
        f"silently, with no error and no visible cause. Everything else still "
        f"runs. Fix with:  pip install --upgrade 'streamlit>={want}'"
    )


#: Spliced into the follower only when diagnostics are on. See
#: :func:`theme_debug_enabled`.
_DEBUG_PANEL_JS = """    // ── Visible diagnostics ──────────────────────────────────────────────
    // Spliced in only when LEX_THEME_DEBUG is set, rather than shipped inert and
    // branched at runtime: a page that never asked for this should not carry the
    // code, and a future edit to a runtime guard cannot leak a debug box into a
    // production dashboard.
    //
    // It shows the whole chain, in order, because the failure is always ONE link
    // and from the outside every break looks identical -- "the switch does
    // nothing". A screenshot of this box says which one.
    var box = document.createElement("pre");
    box.style.cssText =
      "margin:0;padding:8px 10px;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;" +
      "border:1px solid rgba(128,128,128,.45);border-radius:6px;white-space:pre-wrap;" +
      "color:inherit;background:rgba(128,128,128,.08)";
    document.body.appendChild(box);

    function line(n, label, value) {
      return n + ". " + label + ": " + value + "\\n";
    }

    function paint() {
      var agreed, storageErr = "";
      try { agreed = host.localStorage.getItem(KEY); }
      catch (e) { storageErr = " (unreadable: " + e.name + ")"; }

      var shimSaid = host.__lexThemeCurrent;
      var control = null;
      try { control = host.document.querySelector(MENU_BUTTON) ? "found" : "MISSING"; }
      catch (e) { control = "unreadable"; }

      box.textContent =
        "lex-theme chain (LEX_THEME_DEBUG) -- origin " + host.location.origin + "\\n" +
        line(1, "lex-app wrote the agreed mode here",
             (agreed === null || agreed === undefined
               ? "NO -- nothing on this origin. The relay has never delivered. " +
                 "lex-app is on a DIFFERENT origin, so it reaches this one only " +
                 "through the hidden /_lex/theme-relay frame; a browser that " +
                 "partitions third-party storage (Brave shields, Safari, strict " +
                 "Firefox) blocks that write and this stays empty forever."
               : agreed) + storageErr) +
        line(2, "Streamlit told the shim its theme",
             shimSaid ? shimSaid : "NO -- no widget host on this page, or the shim " +
                                   "has not rendered yet") +
        line(3, "Streamlit's theme control", control) +
        line(4, "this page is showing", currentMode() || "unknown") +
        line(5, "url pins a theme", URL_MODE ? URL_MODE + " (stripped on load)" : "no") +
        line(6, "last action", host.__lexThemeLastAction || "none yet");
    }

    paint();
    host.setInterval(paint, 1000);
"""


_FOLLOWER_HTML = """<script>
  (function () {
    var KEY = __KEY__;
    var DEFAULT_MODE = __DEFAULT_MODE__;
    var URL_MODE = __URL_MODE__;

    // Runs in a component iframe, same origin as the Streamlit server, so it may
    // read and drive its parent. `parent`, not `top` -- when lex-app embeds
    // Streamlit, `top` is lex-app's page on another origin and throws.
    var host = window.parent;
    if (!host || host === window) return;
    if (host.__lexThemeDriverInstalled) return;
    host.__lexThemeDriverInstalled = true;

    // ── Why this drives Streamlit's own control ──────────────────────────
    // Every previous version wrote Streamlit's stored theme key and RELOADED,
    // on the premise that Streamlit resolves its theme once at boot so a reload
    // was unavoidable. The premise was false. Streamlit's own menu changes the
    // theme LIVE -- measured: dark -> light with no navigation -- because it
    // goes through Streamlit's React state rather than storage.
    //
    // The reload was the whole problem. It made a theme change cost a full page
    // load (two, when the page booted wrong and had to be corrected), and every
    // piece of machinery this file used to carry -- an oscillation ledger, a
    // sticky stand-down, self-report marking, a one-reload-per-load flag --
    // existed only to make reloading survivable. None of it addressed the user's
    // problem; all of it was a source of new ones.
    //
    // Driving the control deletes that entire class. Nothing reloads, so a wrong
    // decision costs nothing and is corrected by the next report instead of
    // fighting it.
    var MENU_BUTTON = '[data-testid="stMainMenuButton"]';
    var POPOVER = '[data-testid="stMainMenuPopover"]';

    function itemFor(mode) {
      return '[data-testid="stMainMenuItem-theme-' +
             (mode === "dark" ? "Dark" : "Light") + '"]';
    }

    /**
     * The mode Streamlit is actually showing.
     *
     * Published by the widget-host shim, which receives the real theme object in
     * Streamlit's own RENDER event -- a first-class API, exact, and free. The
     * stored key is the fallback for a page with no widgets on it.
     */
    function currentMode() {
      if (host.__lexThemeCurrent === "light" || host.__lexThemeCurrent === "dark") {
        return host.__lexThemeCurrent;
      }
      try {
        var raw = host.localStorage.getItem(
          "stActiveTheme-" + host.location.pathname + "-v2"
        );
        var sel = raw ? JSON.parse(raw) : null;
        if (sel === "Light") return "light";
        if (sel === "Dark") return "dark";
        return host.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      } catch (e) {
        return "";
      }
    }

    /** Remove a theme pin an older version of this script left in the URL. */
    function stripUrlThemePin() {
      try {
        var url = new URL(host.location.href);
        var kept = [];
        var found = false;
        url.searchParams.getAll("embed_options").forEach(function (value) {
          String(value).split(",").forEach(function (part) {
            part = part.trim();
            if (part === "light_theme" || part === "dark_theme") { found = true; return; }
            if (part) kept.push(part);
          });
        });
        if (!found) return false;
        url.searchParams.delete("embed_options");
        kept.forEach(function (v) { url.searchParams.append("embed_options", v); });
        host.history.replaceState(null, "", url.toString());
        return true;
      } catch (e) {
        return false;
      }
    }

    /**
     * Click Streamlit's theme control, without the menu being seen.
     *
     * The popover only exists in the DOM while it is open, so it has to be
     * opened. React renders it a frame or two later, which is long enough to
     * flash -- so it is hidden for the duration and revealed again afterwards,
     * with a timeout that restores visibility even if nothing ever appears.
     */
    function drive(mode) {
      var doc = host.document;
      var button = doc.querySelector(MENU_BUTTON);
      if (!button) return false;

      var mask = doc.createElement("style");
      mask.textContent = POPOVER + "{opacity:0 !important;pointer-events:none !important}";
      doc.head.appendChild(mask);

      var settled = false;
      function finish() {
        if (settled) return;
        settled = true;
        observer.disconnect();
        try { mask.remove(); } catch (e) {}
      }

      var observer = new host.MutationObserver(function () {
        var item = doc.querySelector(itemFor(mode));
        if (!item) return;
        item.click();
        // Close it again: a radio group stays open after a choice.
        var open = doc.querySelector(POPOVER);
        if (open && doc.querySelector(MENU_BUTTON)) doc.querySelector(MENU_BUTTON).click();
        host.setTimeout(finish, 60);
      });
      observer.observe(doc.body, { childList: true, subtree: true });
      host.setTimeout(function () {
        if (!settled) {
          console.warn(
            "[lex-theme] Streamlit's theme control did not appear, so the theme " +
            "was left alone. This is safe -- nothing is reloaded or overridden -- " +
            "but it means a Streamlit upgrade may have renamed " +
            'data-testid="stMainMenuItem-theme-<Mode>".'
          );
        }
        finish();
      }, 2000);

      button.click();
      return true;
    }

    function apply(mode, reason) {
      if (mode !== "light" && mode !== "dark") return;
      var now = currentMode();
      console.info("[lex-theme] asked for", mode, "(" + (reason || "?") + ")",
                   "; showing", now || "(unknown)");
      if (now === mode) { host.__lexThemeLastAction = "already " + mode; return; }
      host.__lexThemeLastAction = "drove to " + mode + " (" + reason + ")";
      drive(mode);
    }

    if (URL_MODE) {
      // A theme pinned in the URL outranks Streamlit's own menu, so the control
      // this drives would stop persisting. Earlier versions of this script put
      // that parameter there, so clean up rather than stand down.
      if (stripUrlThemePin()) {
        console.info(
          "[lex-theme] removed a stale embed_options=" + URL_MODE + "_theme an " +
          "older version of this sync left in the URL; while present it outranks " +
          "Streamlit's own theme menu."
        );
      }
    }

    // Published so the shim can hand us the theme Streamlit gave it.
    host.__lexThemeApply = function (mode) { apply(mode, "widget"); };

    // Called directly, NOT through requestAnimationFrame: rAF does not fire
    // while a document is not being rendered, so a page that finished loading
    // unfocused -- the normal case, with lex-app and the dashboard side by side
    // -- never ran its first correction at all.
    var stored = null;
    try { stored = host.localStorage.getItem(KEY); } catch (e) {}
    apply(stored || DEFAULT_MODE, "install");

    /** Re-read the agreement and conform to it. Cheap: apply() no-ops if already right. */
    function reconcile(reason) {
      var agreed = null;
      try { agreed = host.localStorage.getItem(KEY); } catch (e) {}
      if (agreed) apply(agreed, reason);
    }

    // A deliberate change made in lex-app, in any window of this origin.
    host.addEventListener("storage", function (ev) {
      if (ev.key === KEY) apply(ev.newValue, "storage");
    });

    // ── ...and a `storage` event is not enough on its own ────────────────
    // Reported as: "sometimes it doesn't detect the switch. I reload it then it
    // detects it."
    //
    // The agreement is a VALUE, not an event. The relay deliberately writes only
    // when the value changes, so re-asserting the same mode produces no write
    // and no event at all. That is correct for the relay and fatal here the
    // moment this page has DRIFTED from the agreement -- the theme was changed
    // in Streamlit's own menu, or a drive() timed out -- because from then on
    // the only thing that could re-sync it is a change to a mode it is already
    // agreed on, which by definition never arrives. A reload fixed it because
    // install reads the value directly rather than waiting to be told.
    //
    // So re-read it when the user comes back to this page, which is exactly when
    // they have just changed the theme in the other window. Nothing else needs
    // to notice; conforming to a value we already hold is not an event we have
    // to be given.
    // On `document`, which is where visibilitychange is fired. It does bubble to
    // `window`, but depending on that is a needless bet when the real target is
    // one word away.
    host.document.addEventListener("visibilitychange", function () {
      if (!host.document.hidden) reconcile("revisit");
    });
    // `focus` covers coming back to an already-visible window -- alt-tabbing
    // between two windows side by side never changes visibility at all, and that
    // is exactly how these two products are used.
    host.addEventListener("focus", function () { reconcile("focus"); });
    // And a rerun is a free opportunity: the page is re-rendering anyway.
    host.__lexThemeReconcile = function () { reconcile("rerun"); };

    if (stored === null || stored === undefined) {
      console.warn(
        "[lex-theme] driver ready, but lex-app has never written '" + KEY + "' on " +
        host.location.origin + ". lex-app runs on a different origin and reaches " +
        "this one only through the hidden /_lex/theme-relay frame, so either that " +
        "frame never loaded or this browser partitions third-party storage (Brave " +
        "shields, Safari, strict Firefox), which blocks the write. Until then the " +
        "theme here is whatever Streamlit's own menu says. Set LEX_THEME_DEBUG=1 " +
        "for the full chain."
      );
    } else {
      console.info("[lex-theme] driver ready; agreed", stored, "; showing", currentMode());
    }
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
