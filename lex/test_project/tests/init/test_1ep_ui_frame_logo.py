"""lex.ui's frame: the project's mark in the header, leading home.

Intent: an application shows its tenant's mark in the header, and the mark with the product's name
leads home -- the shell's usual rule. A project gives the mark's address in `ui.frame(logo=)`; RFDS's
header has a logo slot for it, so the look stays lex-app's. The mark is decorative beside the product's
name unless the project names it. A regression is a header without the mark, a mark that does not lead
home, a screen reader reading the name twice, or an address lex-app did not check.

Cluster 1ep -- scenarios 1.1020-1.1021. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame logo=, logo_label=), lex/lex_app/reflex/shell.py
(lex_chrome, brand).
Run: python -m lex pytest lex/test_project/tests/init/test_1ep_ui_frame_logo.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


class TestCluster01ep_FrameLogo(FrameCase):
    """1.1020-1.1021: the mark in RFDS's logo slot, with the product's name, leading home."""

    # -- 1.1020 --------------------------------------------------------
    def test_1_1020_the_projects_mark_leads_home(self) -> None:
        """
        Scenario 1.1020: the mark.
        Given: lex-app's frame without a logo, then ui.frame(logo="/acme.svg"), then with
               logo_label="Acme", in a deployment named "Order desk".
        When:  the frame is compiled.
        Then:  without a logo the product's name leads home; with one, RFDS's logo slot holds the
               mark, decorative (alt="") beside the name, and mark and name are one link home; named,
               the mark is read as its label; the product's name is still the deployment's.
        """
        with patch.dict("os.environ", {"LEX_DASHBOARD_TITLE": "Order desk"}):
            _, plain = compile_page("/plain", shell.lex_chrome(rx.text("body")))
            ui.frame(logo="/acme.svg")
            _, marked = compile_page("/plain", shell.lex_chrome(rx.text("body")))
            ui.frame(logo="https://cdn.example.com/acme.svg", logo_label="Acme")
            _, named = compile_page("/plain", shell.lex_chrome(rx.text("body")))
        for module in (plain, marked, named):
            self.assertIn("Order desk", module, "the product's name is the deployment's")
            self.assertIn("lex-header-home", module, "the brand leads home")
        self.assertNotIn("rfds-header-logo", plain)
        for expected in ("rfds-header-logo", '"/acme.svg"', 'alt:""'):
            self.assertIn(expected, marked.replace('alt: ""', 'alt:""'), f"the mark: {expected}")
        self.assertIn('"https://cdn.example.com/acme.svg"', named)
        self.assertIn('"Acme"', named, "a named mark is read as its label")
        from lex.lex_app.reflex._js import HERE as JS_DIR

        css = (JS_DIR / "lex_rfds.css").read_text()
        self.assertIn(".rfds-header .lex-header-home", css, "the brand link in the chrome band's colours")

    # -- 1.1021 --------------------------------------------------------
    def test_1_1021_the_marks_address_is_checked(self) -> None:
        """
        Scenario 1.1021: logo= written wrongly.
        Given: a relative address, a plain-http address, a label without a logo, a label that is not text.
        When:  each is configured.
        Then:  each is refused with what to write.
        """
        for kwargs, says in (({"logo": "acme.svg"}, "/acme.svg"), ({"logo": "http://example.com/a.svg"}, "https://"),
                             ({"logo_label": "Acme"}, "logo="), ({"logo": "/acme.svg", "logo_label": 3}, "logo_label=")):
            with self.assertRaises((TypeError, ValueError), msg=f"{kwargs}") as caught:
                ui.frame(**kwargs)
            self.assertIn(says, str(caught.exception))
