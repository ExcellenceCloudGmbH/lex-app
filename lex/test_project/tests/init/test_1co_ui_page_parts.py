"""lex.ui pages: parts describe, sentences are translated, and a wrong part is named.

Intent: a page is made of parts that only describe what is on it; lex-app builds the components.
That is what lets a slot refuse anything outside the vocabulary with a message that says what to
write, lets the author write the sentence the user reads (lex-app passes it to RFDS's translation
as both id and default), and lets a value say what it is (kind=) and who sees it (visible=)
without a styling argument anywhere. A regression is a raw component slipping into a slot, an
untranslated label, or an error that does not say what to do.

Cluster 1co -- scenarios 1.788-1.790. Type: U.
Covers: lex/lex_app/reflex/ui/pages/parts.py, ui/pages/vocab.py (text, fact, status, message).
Run: python -m lex pytest lex/test_project/tests/init/test_1co_ui_page_parts.py -v
"""

from __future__ import annotations

import inspect

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import parts, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class PartsDesk(ui.State):
    name: str = ""
    amount: float = 0.0
    stamp: str = ""
    open_now: bool = False


CTX = Build(page="fund", route="/funds/[key]")


class TestCluster01co_PageParts(SimpleTestCase):
    """1.788-1.790: sentences, values and visibility; the slot check."""

    # -- 1.788 ---------------------------------------------------------
    def test_1_788_the_sentence_is_the_message(self) -> None:
        """
        Scenario 1.788: text.
        Given: ui.text, ui.message and ui.status written with English sentences, one with a
               named value, and a text whose content is a state value.
        When:  they are built.
        Then:  each sentence reaches RFDS's translation as both the id and the English default,
               with its values; a state value is shown as it is, untranslated; a tone outside
               the five is refused when the part is declared.
        """
        built = str(vocab.text("{count} open requests", count=3).build(CTX))
        self.assertIn('rfdsI18n.t("{count} open requests", "{count} open requests", {"count": 3})', built,
                      "the sentence is the id and the default; values are passed")
        self.assertIn('rfdsI18n.t("Careful", "Careful"', str(vocab.message("Careful", tone="warning").build(CTX)),
                      "a message's sentence is translated")
        self.assertIn('rfdsI18n.t("Open", "Open"', str(vocab.status("Open", tone="info").build(CTX)),
                      "a status word is translated")
        shown = str(vocab.text(PartsDesk.name).build(CTX))
        self.assertIn("name_rx_state_", shown, "a state value is shown")
        self.assertNotIn("rfdsI18n", shown, "and never translated")
        with self.assertRaises(ValueError, msg="a tone outside the five is refused"):
            vocab.status("Open", tone="blue")

    # -- 1.789 ---------------------------------------------------------
    def test_1_789_a_value_says_what_it_is_and_who_sees_it(self) -> None:
        """
        Scenario 1.789: kind= and visible=.
        Given: facts of each kind, and parts with visible= a permission and a state value.
        When:  they are built.
        Then:  money, number, percent and datetime go through RFDS's formatters (formatted for
               the user's locale); an unknown kind is refused when the fact is declared;
               visible=ui.edit(Model) shows the part only to a user holding that permission
               (RFDS's permission check, the same string a page's requires= gives);
               visible=<state value> shows it while the value holds; no component of the
               vocabulary takes a styling argument.
        """
        money = str(vocab.fact("Net asset value", PartsDesk.amount, kind="money").build(CTX))
        plain = str(vocab.fact("Name", PartsDesk.name).build(CTX))
        self.assertIn('rfdsI18n.t("Net asset value"', money, "the label is a sentence")
        self.assertNotEqual(money.replace("Net asset value", "Name").replace("amount", "name"), plain,
                            "a money value is rendered through a formatter, not as the bare value")
        for kind, value, name in (("number", PartsDesk.amount, "amount"), ("percent", PartsDesk.amount, "amount"),
                                  ("datetime", PartsDesk.stamp, "stamp")):
            with self.subTest(kind=kind):
                self.assertIn(f"{name}_rx_state_", str(vocab.fact("X", value, kind=kind).build(CTX)), f"kind={kind} builds")
        with self.assertRaises(ValueError, msg="an unknown kind is refused"):
            vocab.fact("X", 1, kind="colour")

        guarded = str(vocab.text("Editors only", visible=ui.edit(Group)).build(CTX))
        self.assertIn("auth.Group:edit", guarded, "a permission in visible= is checked with the page's permission string")
        stateful = str(vocab.text("While open", visible=PartsDesk.open_now).build(CTX))
        self.assertIn("open_now_rx_state_", stateful, "a state value in visible= conditions the part")
        self.assertNotIn("open_now_rx_state_", str(vocab.text("While open").build(CTX)), "and only when given")

        for factory in (vocab.text, vocab.fact, vocab.status, vocab.message):
            with self.subTest(component=factory.__name__):
                names = set(inspect.signature(factory).parameters)
                self.assertFalse(names & {"class_name", "style", "props", "gap", "width", "padding"},
                                 f"ui.{factory.__name__} takes no styling argument")

    # -- 1.790 ---------------------------------------------------------
    def test_1_790_a_slot_names_what_it_refuses_and_what_to_write(self) -> None:
        """
        Scenario 1.790: the slot check.
        Given: a list slot that accepts facts.
        When:  it is given a raw component, another lex component, and nothing.
        Then:  the error names the page and the slot, says what it got, and says what to write
               instead; a single part and a list are both accepted; nothing gives an empty list.
        """
        ctx = CTX.at("facts")
        instead = "Use `ui.fact(label, value)`."
        with self.assertRaises(PageError) as raw:
            parts.take(ctx, [rx.text("x")], (vocab.Fact,), instead)
        self.assertEqual(str(raw.exception),
                         "Page `fund`, slot `facts`: got a raw component (`Text`).\nUse `ui.fact(label, value)`.",
                         "the message names page, slot, what it got and what to write")
        with self.assertRaises(PageError) as other:
            parts.take(ctx, [vocab.status("Open")], (vocab.Fact,), instead)
        self.assertIn("got `ui.status`", str(other.exception), "another lex component is named by its ui name")
        one = vocab.fact("A", 1)
        self.assertEqual((parts.take(ctx, one, (vocab.Fact,), instead), parts.take(ctx, [one], (vocab.Fact,), instead),
                          parts.take(ctx, None, (vocab.Fact,), instead)), ([one], [one], []),
                         "one part, a list and nothing are all accepted")
