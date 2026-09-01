"""Make Streamlit's custom-component frames load without being scrolled to.

Streamlit renders every custom component like this (1.58, ``ComponentInstance``)::

    styled('iframe')(({ componentReady }) => ({
      display: componentReady ? 'initial' : 'none',
    }))

    <iframe data-testid="stCustomComponentV1" height={frameHeight ?? 0} ... />

So the frame is ``display: none`` until the component inside it calls
``Streamlit.setComponentReady()`` -- which it can only do after loading. Browsers
deprioritise, and off-screen often defer entirely, the loading of a hidden frame.
The result is a component that starts loading when you scroll to it, showing
Streamlit's skeleton placeholder until then.

``loading="eager"`` on the inner iframe cannot help: that frame lives *inside*
the hidden one, so it is not reached until the outer frame is fetched.

The fix is to stop the frame being ``display: none`` while it loads. It is given
``display: block; height: 0``, which renders it -- so the browser fetches it --
without occupying any space. Deliberately as INLINE styles and without
``!important``: inline already beats the emotion class, and staying beatable
means Streamlit gets the element back intact the moment the override is removed.

The override is removed as soon as the frame reports a height, which Streamlit
only sets after the component reported ready. So this holds the frame open
exactly across the load, and hands it straight back.
"""

from __future__ import annotations

#: Streamlit's own test id for a declared custom component's frame. Covers
#: lex_widgets() and lex_view() alike -- both have the same problem.
_COMPONENT_FRAME = 'iframe[data-testid="stCustomComponentV1"]'

#: Give up after this long and restore the element regardless, so a component
#: that never reports ready leaves nothing of ours behind.
_MAX_WAIT_MS = 30000

_EAGER_FRAMES_JS = """
    // ── Load component frames without waiting for a scroll ───────────────
    (function () {
      var host = window.parent;
      if (!host || host === window || host.__lexEagerFramesInstalled) return;
      host.__lexEagerFramesInstalled = true;

      var SELECTOR = '__SELECTOR__';
      var MAX_WAIT = __MAX_WAIT__;

      function release(frame, timer) {
        // Remove OUR properties only, so Streamlit's own styling is untouched.
        frame.style.removeProperty("display");
        frame.style.removeProperty("height");
        frame.style.removeProperty("border");
        delete frame.dataset.lexEager;
        if (timer) host.clearInterval(timer);
      }

      function nudge(frame) {
        if (frame.dataset.lexEager) return;
        var display;
        try { display = host.getComputedStyle(frame).display; } catch (e) { return; }
        if (display !== "none") return;   // already rendered; nothing to do

        frame.dataset.lexEager = "1";
        // Inline, and NOT !important: enough to beat the emotion class, and
        // still weak enough that removing it restores Streamlit's own rules.
        frame.style.display = "block";
        frame.style.height = "0px";
        frame.style.border = "0";

        var waited = 0;
        var timer = host.setInterval(function () {
          // Streamlit writes `height` only after the component reported ready,
          // so a non-zero height is proof the load finished.
          var ready = parseInt(frame.getAttribute("height") || "0", 10) > 0;
          waited += 100;
          if (ready || waited >= MAX_WAIT || !frame.isConnected) {
            release(frame, timer);
          }
        }, 100);
      }

      function sweep() {
        try {
          var frames = host.document.querySelectorAll(SELECTOR);
          for (var i = 0; i < frames.length; i++) nudge(frames[i]);
        } catch (e) {}
      }

      sweep();
      // Streamlit rebuilds the element tree on every rerun, so new frames keep
      // appearing; a one-shot sweep would only ever catch the first render.
      try {
        new MutationObserver(sweep).observe(host.document.body, {
          childList: true,
          subtree: true,
        });
      } catch (e) {}
    })();
"""


def eager_frames_js() -> str:
    """The script, ready to embed in a page-level component block."""
    return _EAGER_FRAMES_JS.replace("__SELECTOR__", _COMPONENT_FRAME).replace(
        "__MAX_WAIT__", str(_MAX_WAIT_MS)
    )
