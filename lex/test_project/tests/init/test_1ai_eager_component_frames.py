"""Intent: a widget must not wait to be scrolled to before it starts loading.

Reported as "the components trigger when I scroll to them". The cause is in
Streamlit itself (1.58, ``ComponentInstance``), not in the widgets::

    styled('iframe')(({ componentReady }) => ({
      display: componentReady ? 'initial' : 'none',
    }))

    <iframe data-testid="stCustomComponentV1" height={frameHeight ?? 0} ... />

A component's frame is ``display: none`` until the code inside it calls
``Streamlit.setComponentReady()`` -- which it can only do once loaded. Browsers
deprioritise hidden frames, and off-screen ones are commonly deferred outright,
so the frame does not fetch until scrolling changes its visibility. Streamlit
shows a skeleton placeholder in the meantime, which is what was visible.

``loading="eager"`` on our own inner iframe cannot reach this: that frame lives
INSIDE the hidden one.

The fix holds the frame open across its load -- ``display: block; height: 0``,
rendered but occupying nothing -- and hands it straight back once Streamlit sets
a height, which only happens after the component reported ready.

The properties below are the ones that make the fix safe rather than merely
effective. Anything that keeps a frame open too long, or fails to restore it,
breaks Streamlit's own sizing rather than the widget's, which is much harder to
attribute.

Cluster 01-init, batch 1ai, scenarios 1.306-1.307.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ai_eager_component_frames.py
"""

import pytest

from lex.lex_app.streamlit.eager_frames import _MAX_WAIT_MS, eager_frames_js

pytestmark = pytest.mark.init


class TestCluster1ai_EagerComponentFrames:
    """The override is targeted, temporary, and fully reversible."""

    def test_01_306_it_targets_streamlit_component_frames_only(self):
        """Scenario 1.306: only custom-component frames are touched.

        ``stCustomComponentV1`` is Streamlit's own test id for a declared
        component's frame. Widening this to all iframes would sweep up the app's
        own embeds, the theme relay and anything a page author added -- forcing
        layout on elements that were hidden deliberately.
        """
        js = eager_frames_js()
        assert 'iframe[data-testid="stCustomComponentV1"]' in js
        assert "querySelectorAll" in js
        # Not a blanket selector.
        assert "querySelectorAll('iframe')" not in js
        assert 'querySelectorAll("iframe")' not in js

    def test_01_306_the_override_is_beatable_by_streamlit(self):
        """Scenario 1.306 (second half): inline styles, never ``!important``.

        Inline already outranks the emotion class, so ``!important`` would buy
        nothing and cost everything: Streamlit could no longer restyle its own
        element, and the frame would be pinned at zero height forever once the
        component became ready.
        """
        js = eager_frames_js()

        # Check the code, not the prose — the comment above the assignment says
        # "NOT !important", which a naive substring search would trip over.
        code = "\n".join(
            line for line in js.splitlines() if not line.strip().startswith("//")
        )
        assert "!important" not in code
        assert "setProperty(" not in code, (
            "setProperty is how a priority gets set; plain assignment cannot"
        )
        assert 'frame.style.display = "block"' in js
        assert 'frame.style.height = "0px"' in js

    def test_01_307_every_exit_restores_the_element(self):
        """Scenario 1.307: ready, timeout and removal all release the frame.

        A frame left with our inline styles is worse than the original bug -- it
        would be permanently zero-height AFTER loading, i.e. an invisible widget
        rather than a slow one. So release must not depend on the happy path.
        """
        js = eager_frames_js()

        # One release path, used by all three exits.
        assert js.count("function release(") == 1
        for prop in ("display", "height", "border"):
            assert f'frame.style.removeProperty("{prop}")' in js
        assert "delete frame.dataset.lexEager" in js

        # Ready: Streamlit only writes `height` after the component reported in.
        assert 'frame.getAttribute("height")' in js
        # Timeout: bounded, and long enough not to fire mid-load.
        assert "waited >= MAX_WAIT" in js
        assert _MAX_WAIT_MS >= 10000, "too short — would release a slow frame mid-load"
        assert _MAX_WAIT_MS <= 120000, "too long — a dead component would hold the frame"
        # Detached: stop polling something that is gone.
        assert "!frame.isConnected" in js

    def test_01_307_it_installs_once_and_survives_reruns(self):
        """Scenario 1.307 (second half): one installation, but a live sweep.

        Streamlit rebuilds the element tree on every rerun, so a one-shot sweep
        would only ever catch the first render and every later widget would go
        back to loading on scroll. A MutationObserver keeps it current; the
        install flag lives on the page, which is the object that outlives the
        component iframe this script runs in.
        """
        js = eager_frames_js()
        assert "host.__lexEagerFramesInstalled" in js
        assert "MutationObserver" in js
        assert "subtree: true" in js
        # `parent`, not `top`: when lex-app embeds Streamlit, `top` is another
        # origin and every access throws.
        assert "window.parent" in js
        assert "window.top" not in js
