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


_FOLLOWER_HTML = """<script>
  (function () {
    var KEY = __KEY__;
    var URL_MODE = __URL_MODE__;

    // This runs in a srcdoc iframe: same origin as the Streamlit server, so it
    // shares this origin's localStorage and may read and navigate its parent.
    // `parent`, not `top` -- when lex-app embeds Streamlit, `top` is lex-app's
    // page on another origin and every access to it throws.
    var host = window.parent;
    if (!host || host === window || host.__lexThemeFollowerInstalled) return;
    host.__lexThemeFollowerInstalled = true;

    /** What is on screen right now, or "" if we cannot tell. */
    function currentMode() {
      // The URL wins when it specifies a mode: we put it there last time.
      if (URL_MODE) return URL_MODE;
      // Otherwise Streamlit is following the OS or a config default, and the
      // only honest way to learn which is to look at what it painted.
      try {
        var el = host.document.querySelector(".stApp") || host.document.body;
        var parts = host.getComputedStyle(el).backgroundColor.match(/[\\d.]+/g);
        if (!parts || parts.length < 3) return "";
        // Rec. 601 luma. Streamlit's two themes sit at the extremes, so the
        // midpoint is a safe cut -- this is not trying to be a colour library.
        var luma = 0.299 * +parts[0] + 0.587 * +parts[1] + 0.114 * +parts[2];
        return luma < 128 ? "dark" : "light";
      } catch (e) {
        return "";
      }
    }

    function follow(mode) {
      if (mode !== "light" && mode !== "dark") return;
      var now = currentMode();
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

    // A change that landed while this page was loading. Deferred one frame so
    // the app's own styles have painted before we measure them.
    host.requestAnimationFrame(function () {
      try { follow(window.localStorage.getItem(KEY)); } catch (e) {}
    });

    // `storage` fires in every OTHER window on this origin, which is how the
    // relay's write reaches us.
    window.addEventListener("storage", function (ev) {
      if (ev.key === KEY) follow(ev.newValue);
    });
  })();
</script>
"""


def theme_follower_html(url_mode: str = "") -> str:
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
    return _FOLLOWER_HTML.replace("__KEY__", _js_literal(THEME_STORAGE_KEY)).replace(
        "__URL_MODE__", _js_literal(url_mode or "")
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
