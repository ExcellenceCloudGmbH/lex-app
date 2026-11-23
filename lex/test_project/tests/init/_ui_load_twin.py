"""A state named like one in test_1cj_ui_load, in another module (scenario 1.775).

Reflex allows two states with the same class name in different modules; their loads must not be
mistaken for each other.
"""

from __future__ import annotations

from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.state import State

STARTED: list = []


class Twin(State):
    filled: str = ""

    @handlers.load
    def open(self):
        STARTED.append("twin-other")
        self.filled = "other"
