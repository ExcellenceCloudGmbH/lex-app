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
    count: int = 0
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
        with self.assertRaises(PageError) as no_words:
            vocab.text(3).build(CTX)
        self.assertEqual(str(no_words.exception),
                         "Page `fund`: `ui.text` got `3` where its words belong.\n"
                         "Write the sentence the user reads, in English: `ui.text(\"3 open requests\")`. "
                         "A state value is shown as it is.", "something that is not a sentence is named, with what to write")
        with self.assertRaises(PageError, msg="an empty label is not a sentence either"):
            vocab.fact("", 1).build(CTX)

    # -- 1.789 ---------------------------------------------------------
    def test_1_789_a_value_says_what_it_is_and_who_sees_it(self) -> None:
        """
        Scenario 1.789: kind= and visible=.
        Given: facts of each kind, and parts with visible= a permission and a state value.
        When:  they are built.
        Then:  each kind goes through RFDS's own formatter for it; a whole number is shown
               without decimals and any other with two; money takes a currency; an unknown
               kind, and a currency without kind="money", are refused when the fact is
               declared. visible= takes a permission (RFDS's check, with the string a page's
               requires= gives), a list of them (all must hold), ui.signed_in (always shown),
               or a state value; anything else, and an empty list, is refused with what to
               write. No component of the vocabulary takes a styling argument.
        """
        money = str(vocab.fact("Net asset value", PartsDesk.amount, kind="money", currency="EUR").build(CTX))
        self.assertIn('rfdsI18n.t("Net asset value"', money, "the label is a sentence")
        self.assertIn('"data-slot":"rfds-money"', money, "money goes through RFDS's money formatter")
        self.assertIn("EUR", money, "in the currency given")
        for kind, value, mark in (("number", PartsDesk.amount, "rfds-number"), ("percent", PartsDesk.amount, "rfds-percent"),
                                  ("datetime", PartsDesk.stamp, "rfds-timestamp"), ("money", PartsDesk.amount, "rfds-money")):
            with self.subTest(kind=kind):
                self.assertIn(f'"data-slot":"{mark}"', str(vocab.fact("X", value, kind=kind).build(CTX)),
                              f"kind={kind} uses RFDS's formatter for it")
        self.assertNotIn("data-slot\":\"rfds-number", str(vocab.fact("Name", PartsDesk.name).build(CTX)),
                         "text is shown as it is")
        self.assertEqual((vocab._whole(3), vocab._whole(PartsDesk.count), vocab._whole(2.5), vocab._whole(PartsDesk.amount),
                          vocab._whole(True)), (True, True, False, False, False),
                         "a whole number is an int or a state value typed int")
        whole, decimal = (str(vocab.shape(value, "number")) for value in (PartsDesk.count, PartsDesk.amount))
        self.assertNotEqual(whole.replace("count", "amount"), decimal, "whole numbers and decimals are formatted differently")
        with self.assertRaises(ValueError, msg="an unknown kind is refused"):
            vocab.fact("X", 1, kind="colour")
        with self.assertRaises(ValueError, msg="a currency goes with money only"):
            vocab.fact("X", 1, kind="number", currency="EUR")

        guarded = str(vocab.text("Editors only", visible=ui.edit(Group)).build(CTX))
        self.assertIn("auth.Group:edit", guarded, "a permission in visible= is checked with the page's permission string")
        stateful = str(vocab.text("While open", visible=PartsDesk.open_now).build(CTX))
        self.assertIn("open_now_rx_state_", stateful, "a state value in visible= conditions the part")
        plain_text = str(vocab.text("While open").build(CTX))
        self.assertNotIn("open_now_rx_state_", plain_text, "and only when given")
        both = str(vocab.text("While open", visible=[ui.read(Group), ui.edit(Group)]).build(CTX))
        self.assertIn("auth.Group:read", both, "a list of permissions: the first must hold")
        self.assertIn("auth.Group:edit", both, "and the second")
        self.assertEqual(str(vocab.text("While open", visible=ui.signed_in).build(CTX)), plain_text,
                         "every user of the dashboards is signed in: always shown")
        with self.assertRaises(PageError) as wrong:
            vocab.text("While open", visible="editors").build(CTX)
        self.assertEqual(str(wrong.exception),
                         "Page `fund`: `visible=` got `'editors'`.\nGive a permission -- `visible=ui.edit(Model)` -- "
                         "or a state value: `visible=State.is_open`.", "anything else is refused with what to write")
        with self.assertRaises(PageError) as empty:
            vocab.text("While open", visible=[]).build(CTX)
        self.assertEqual(str(empty.exception),
                         "Page `fund`: `visible=` got an empty list.\nList at least one permission: "
                         "`visible=[ui.read(Model)]`.", "an empty list is refused, not shown to everyone")

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
        with self.assertRaises(PageError) as nothing:
            parts.take(ctx, [None], (vocab.Fact,), instead)
        self.assertIn("got nothing (`None`).", str(nothing.exception), "a None inside the list is named as nothing")
        self.assertTrue(parts.called("x" * 200).endswith("...`"), "a long value is cut before its closing mark")
        one = vocab.fact("A", 1)
        self.assertEqual((parts.take(ctx, one, (vocab.Fact,), instead), parts.take(ctx, [one], (vocab.Fact,), instead),
                          parts.take(ctx, None, (vocab.Fact,), instead)), ([one], [one], []),
                         "one part, a list and nothing are all accepted")
