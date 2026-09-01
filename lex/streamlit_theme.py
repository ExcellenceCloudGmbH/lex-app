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
      var stored, storageErr = "";
      try { stored = host.localStorage.getItem(KEY); }
      catch (e) { storageErr = " (unreadable: " + e.name + ")"; }

      var report = host.__lexThemeLastReport;
      var bg = "";
      try {
        var el = host.document.querySelector(".stApp") || host.document.body;
        bg = host.getComputedStyle(el).backgroundColor;
      } catch (e) { bg = "(unreadable)"; }

      box.textContent =
        "lex-theme diagnostics (LEX_THEME_DEBUG)\\n" +
        line("page showing", (currentMode() || "unknown") + "   measured bg " + bg) +
        line("url embed_options", URL_MODE || "(none)") +
        line("stored on this origin", (stored === null || stored === undefined
              ? "(unset)" : stored) + storageErr) +
        line("widget last reported", report
              ? report.mode + " via " + report.route + ", " + report.ago() + "s ago"
              : "(nothing yet -- no widget has announced a theme)") +
        line("follow entry point", typeof host.__lexThemeFollow) +
        line("reload already used", host.__lexThemeReloading ? "yes" : "no");
    }

    paint();
    host.setInterval(paint, 1000);
"""


_FOLLOWER_HTML = """<script>
  (function () {
    var KEY = __KEY__;
    var URL_MODE = __URL_MODE__;

    // This runs in a srcdoc iframe: same origin as the Streamlit server, so it
    // shares this origin's localStorage and may read, listen on and navigate its
    // parent. `parent`, not `top` -- when lex-app embeds Streamlit, `top` is
    // lex-app's page on another origin and every access to it throws.
    var host = window.parent;
    if (!host || host === window) return;

    // Guard AND listener both belong to `host`, and that pairing is the point.
    // Streamlit destroys and recreates this iframe across reruns, while the
    // Streamlit page persists. A flag on the page with a listener on this
    // window would survive exactly one render: the next iframe would see the
    // flag, return early, and the iframe that actually held the listener would
    // already be gone -- leaving nothing listening anywhere, silently.
    if (host.__lexThemeFollowerInstalled) return;
    host.__lexThemeFollowerInstalled = true;

    /** Light/dark from one element's own background, or "" if it has none. */
    function paintedMode(el) {
      if (!el) return "";
      try {
        var parts = host.getComputedStyle(el).backgroundColor.match(/[\\d.]+/g);
        if (!parts || parts.length < 3) return "";
        // A fully transparent background carries NO information, and this is the
        // case that has to be rejected explicitly: `rgba(0, 0, 0, 0)` parses to
        // four zeroes, which reads as pure black and would answer "dark" for any
        // page whose element simply paints nothing. That answer is worse than no
        // answer -- it makes a light page look already-correct, so the follower
        // no-ops and the theme silently never changes.
        if (parts.length > 3 && parseFloat(parts[3]) === 0) return "";
        // Rec. 601 luma. Streamlit's two themes sit at the extremes, so the
        // midpoint is a safe cut -- this is not trying to be a colour library.
        var luma = 0.299 * +parts[0] + 0.587 * +parts[1] + 0.114 * +parts[2];
        return luma < 128 ? "dark" : "light";
      } catch (e) {
        return "";
      }
    }

    /** What is on screen right now, or "" if we genuinely cannot tell. */
    function currentMode() {
      // The URL wins when it specifies a mode: we put it there last time.
      if (URL_MODE) return URL_MODE;

      // Otherwise Streamlit is following the OS or a config default, and the
      // honest way to learn which is to look at what it painted. Which element
      // carries the theme background is Streamlit's business and has moved
      // between versions, so try the plausible ones and take the first that
      // actually paints.
      var d;
      try { d = host.document; } catch (e) { return ""; }
      var candidates = [
        d.querySelector(".stApp"),
        d.querySelector('[data-testid="stAppViewContainer"]'),
        d.querySelector('[data-testid="stMain"]'),
        d.body,
        d.documentElement
      ];
      for (var i = 0; i < candidates.length; i++) {
        var painted = paintedMode(candidates[i]);
        if (painted) return painted;
      }

      // Nothing opaque anywhere. With no theme in the URL Streamlit follows the
      // OS preference, so ask the OS -- a reasoned answer rather than a guess,
      // and better than refusing outright.
      try {
        return host.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      } catch (e) {
        return "";
      }
    }

    function follow(mode) {
      if (mode !== "light" && mode !== "dark") return;
      var now = currentMode();
      // One line per decision, because this crosses three contexts with no UI of
      // its own -- without it, "did not arrive" and "arrived and was correct"
      // look identical from the outside.
      console.info("[lex-theme] asked for", mode, "; showing", now || "(unknown)");
      if (!now || now === mode) return;          // already right, or unknowable
      if (host.__lexThemeReloading) return;      // one reload per change
      host.__lexThemeReloading = true;
      try {
        var url = new URL(host.location.href);
        var kept = [];
        url.searchParams.getAll("embed_options").forEach(function (value) {
          value.split(",").forEach(function (opt) {
            if (opt && opt !== "light_theme" && opt !== "dark_theme") kept.push(opt);
          });
        });
        url.searchParams.delete("embed_options");
        kept.forEach(function (o) { url.searchParams.append("embed_options", o); });
        url.searchParams.append("embed_options", mode + "_theme");
        host.location.replace(url.toString());
      } catch (e) {
        host.__lexThemeReloading = false;
      }
    }

    // Published on the page so a writer already INSIDE this frame tree can just
    // call it. The widget-host shim does, which turns the embedded path from
    //
    //   widget -> shim -> localStorage -> storage event -> follower -> reload
    //
    // into
    //
    //   widget -> shim -> follower -> reload
    //
    // Three fewer things that can fail without saying so. The storage route
    // stays for writers that have no handle to this page -- the relay iframe,
    // and a sibling tab on this origin.
    host.__lexThemeFollow = follow;

    // A change that landed while this page was loading -- including one an
    // embedded widget announced before this block rendered, which is the common
    // ordering, and the reason the shim writes storage as well as calling in.
    // Deferred one frame so the app's styles have painted before we measure them.
    host.requestAnimationFrame(function () {
      try { follow(host.localStorage.getItem(KEY)); } catch (e) {}
    });

    // On `host`, not `window`: see the lifetime note above. `storage` fires in
    // every OTHER window of this origin, which is how a writer without a handle
    // to us gets through.
    host.addEventListener("storage", function (ev) {
      if (ev.key === KEY) follow(ev.newValue);
    });

    console.info("[lex-theme] follower ready; page is showing", currentMode() || "(unknown)");

__DEBUG_PANEL__  })();
</script>
"""


#: Height the follower block needs when the diagnostics panel is on. Zero
#: otherwise -- an inert block must not take space.
DEBUG_PANEL_HEIGHT = 132


def theme_follow_enabled(environ: "dict[str, str] | None" = None) -> bool:
    """Whether a Streamlit page should follow lex-app's theme. OFF by default.

    Following works by reloading with ``?embed_options=<mode>_theme``, and that
    parameter is the TOP of Streamlit's own precedence: it outranks the stored
    theme and Streamlit's theme menu alike. So a page that follows has, from the
    user's side, simply lost its theme control -- the menu stops doing anything
    and nothing in the app file can override it, because a query parameter is
    not something app code gets a say in.

    That is a fair trade only where someone asked for the two surfaces to match.
    It is not a reasonable default, so it is opt-in::

        LEX_THEME_FOLLOW=1

    A page that has already been pinned is freed by opening it once without
    ``embed_options`` in the URL; with following off, nothing puts it back.
    """
    import os

    raw = (environ if environ is not None else os.environ).get("LEX_THEME_FOLLOW", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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
        .replace("__URL_MODE__", _js_literal(url_mode or ""))
        .replace("__DEBUG_PANEL__", _DEBUG_PANEL_JS if debug else "")
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
