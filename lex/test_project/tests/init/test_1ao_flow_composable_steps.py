"""Intent: a flow with an ORDER can be written down, and a flow that could never
do what it says is refused at the line that wrote it.

``Flow`` was a lookup table keyed by ``"<resource>/<operation>"``, resolved
independently every time that operation happened. Three things an author wants
had no expression at all in that shape, and each failed silently rather than
loudly.

Order: a chain visiting the same resource and operation twice collided on one
key, so ``create t1 -> create t2 -> create t1`` quietly became two rules instead
of three steps. Loops: "keep opening create forms until I stop" could only be
approximated by ``STAY``, which repeats one step because it is a sentinel
meaning "do not navigate" -- not a position that did not move. References: a
step could not say "edit the record step four made"; ``{id}`` existed and always
meant the record just saved.

The sequence form adds all three. The mapping form stays exactly as it was --
it addresses operations directly, which is the escape hatch -- and the two
cannot be mixed, because a flow holding both has no single answer to "what
happens after this save".

The wire format carries a version for the same reason every rule here is
checked at author time: lex-app and the React bundle ship as separate artefacts
and can be a version apart, so a sequence reaching a frontend that predates it
must do NOTHING rather than something wrong.

Cluster 01-init, batch 1ao, scenarios 1.342-1.347.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ao_flow_composable_steps.py
"""

import json
import urllib.parse

import pytest

from lex.lex_app.streamlit import STAY, Flow, FlowError, ref

pytestmark = pytest.mark.init


def _steps(flow):
    """The step list of a serialised sequence."""
    return flow.to_wire()["steps"]


def _end(flow):
    """The terminal of a serialised sequence."""
    return flow.to_wire()["end"]


class TestCluster1ao_TheSequence:
    """An ordered program, where there used to be an unordered table."""

    def test_01_342_a_sequence_serialises_in_the_order_it_was_written(self):
        """Scenario 1.342: order is the thing the mapping could not hold.

        The author's own example, end to end. What makes it a sequence rather
        than a table is that ``table4`` appears twice -- once created, once
        edited -- which under a mapping is two keys resolved independently and
        here is two positions.
        """
        flow = (
            Flow()
            .create("table1")
            .create("table2")
            .update("table3", id=3)
            .create("table4")
            .update("table4")
            .table("table4")
        )
        wire = flow.to_wire()

        assert wire["v"] == 2, "a sequence is versioned; a mapping is not"
        assert [(s["op"], s["res"]) for s in wire["steps"]] == [
            ("create", "table1"),
            ("create", "table2"),
            ("update", "table3"),
            ("create", "table4"),
            ("update", "table4"),
        ]
        assert wire["end"] == {"kind": "table", "res": "table4"}

    def test_01_342_the_same_resource_twice_stays_two_steps(self):
        """Scenario 1.342 (second half): the collision that motivated this.

        Under the mapping, ``create t1`` twice is one key written twice -- the
        second silently replaces the first and the flow is a step shorter than
        it reads. Positions cannot collide.
        """
        flow = Flow().create("t1").create("t2").create("t1").loop()

        assert len(_steps(flow)) == 3
        assert [s["res"] for s in _steps(flow)] == ["t1", "t2", "t1"]

    def test_01_342_a_sequence_is_truthy_even_though_its_mapping_is_empty(self):
        """Scenario 1.342 (third half): the falsy-flow trap.

        ``Flow`` subclasses ``dict``, so a flow built entirely from steps has an
        empty mapping and inherits ``__bool__`` as False. ``lex_view`` guards
        the parameter with ``if flow:`` -- left inherited, every sequence would
        be dropped on the floor there, silently, which is the one failure mode
        this whole module exists to keep out of the browser.
        """
        flow = Flow().create("investor").loop_last()

        assert bool(flow) is True
        assert flow.is_program is True
        assert bool(Flow()) is False, "an empty flow is still empty"


class TestCluster1ao_Ids:
    """Which record a step opens, in the three forms that can mean."""

    def test_01_343_an_absent_id_means_the_record_the_previous_step_made(self):
        """Scenario 1.343: the common case carries no id at all.

        ``create table4`` then ``update table4`` is the author's
        ``table4_created_id_update_form``. The id is not written because there
        is only one record it could be, and writing it would mean inventing a
        name for something used once on the next line.
        """
        flow = Flow().create("table4").update("table4")

        assert "id" not in _steps(flow)[1], "absent, not null -- absence is the signal"

    def test_01_343_a_literal_id_is_carried_as_written(self):
        """Scenario 1.343 (second half): an id known when writing the flow.

        The author's ``Table3_id3_update_form``. Ints and strings both, because
        a primary key is whichever the model says.
        """
        assert _steps(Flow().update("table3", id=3))[0]["id"] == 3
        assert _steps(Flow().update("table3", id="abc"))[0]["id"] == "abc"

    def test_01_343_a_named_step_can_be_reached_from_further_down(self):
        """Scenario 1.343 (third half): reaching past the previous step.

        "Create the investor, create its vehicle, then come back and finish the
        investor" is the flow a linear chain cannot otherwise express -- by the
        time the third step runs, the implicit id belongs to the vehicle.
        """
        flow = (
            Flow()
            .create("investor", as_="inv")
            .create("vehicle")
            .update("investor", id=ref("inv"))
        )

        assert _steps(flow)[0]["as"] == "inv"
        assert _steps(flow)[2]["id"] == {"ref": "inv"}, "a ref is an object, not a string"

    def test_01_343_a_reference_cannot_point_forwards(self):
        """Scenario 1.343 (fourth half): refs resolve backwards only.

        Forwards would mean the browser holding an id it has not produced.
        Refused where it is written rather than warned about in a console the
        author is not looking at.
        """
        with pytest.raises(FlowError, match="points? backwards|not been written"):
            Flow().create("a").update("b", id=ref("later"))


class TestCluster1ao_Endings:
    """Exactly one, and it is always decidable."""

    @pytest.mark.parametrize(
        "build,expected",
        [
            (lambda: Flow().create("a").table("b"), {"kind": "table", "res": "b"}),
            (lambda: Flow().create("a").loop(), {"kind": "loop"}),
            (lambda: Flow().create("a").loop_last(), {"kind": "loop_last"}),
            (lambda: Flow().create("a").end_goto("/x?y=1"), {"kind": "goto", "path": "/x?y=1"}),
        ],
    )
    def test_01_344_each_ending_serialises_as_its_own_kind(self, build, expected):
        """Scenario 1.344: four endings, told apart by ``kind``."""
        assert _end(build()) == expected

    def test_01_344_an_ending_defaults_to_the_last_resource(self):
        """Scenario 1.344 (second half): omitting the ending is legal.

        And it means the table of whatever the flow last touched -- which is
        what the frontend already falls back to today
        (``withEmbedParams('/' + resource)``). An un-terminated sequence
        therefore behaves the way flows behave now, rather than behaving like
        nothing.
        """
        assert _end(Flow().create("a").create("b")) == {"kind": "table", "res": "b"}
        assert _end(Flow().create("a").show()) == {"kind": "show", "res": "a"}

    def test_01_344_a_loop_clears_nothing_it_was_not_given(self):
        """Scenario 1.344 (third half): the two loops are different shapes.

        ``loop`` restarts the chain, so it needs the chain; ``loop_last``
        repeats one step. Both refuse a flow with no steps, because an ending
        with nothing to end is a flow that does nothing at all.
        """
        assert _end(Flow().create("a").create("b").loop()) == {"kind": "loop"}
        with pytest.raises(FlowError, match="nothing"):
            Flow().loop()


class TestCluster1ao_RefusedWhenWritten:
    """Every rule that could never fire, refused at the line that wrote it."""

    @pytest.mark.parametrize(
        "label,build",
        [
            ("implicit id as the first step", lambda: Flow().update("a")),
            ("implicit id after a goto", lambda: Flow().create("a").goto("/x").update("b")),
            ("duplicate as_", lambda: Flow().create("a", as_="x").create("b", as_="x")),
            ("a step after the ending", lambda: Flow().create("a").table().create("b")),
            ("a second ending", lambda: Flow().create("a").table().loop()),
            ("an ending with no steps", lambda: Flow().table("a")),
            ("a blank resource", lambda: Flow().create("   ")),
            ("an id that is neither", lambda: Flow().create("a").update("b", id=1.5)),
        ],
    )
    def test_01_345_a_flow_that_could_never_work_is_refused(self, label, build):
        """Scenario 1.345: the rule this module already lived by, extended.

        "Validated when written, not when they fail to fire." Each of these
        produces, if allowed through, a flow that is serialised, shipped, and
        then does nothing in a browser with nothing anywhere to explain why.
        """
        with pytest.raises(FlowError):
            build()

    def test_01_345_the_two_forms_cannot_be_mixed(self):
        """Scenario 1.345 (second half): refused from both directions.

        A mapping rule fires whenever its operation happens; a step fires at its
        position. A flow holding both has two answers to "what happens after
        this save" and would silently take one. Both doors are shut, because
        shutting one only moves the problem to the other.
        """
        with pytest.raises(FlowError, match="built as a mapping"):
            Flow({"a/create": "/b"}).create("c")

        with pytest.raises(FlowError, match="built as a sequence"):
            Flow().create("a").after_create("b", "/c")

    def test_01_345_a_mapping_key_handed_to_a_step_says_so(self):
        """Scenario 1.345 (third half): the migration typo, named.

        ``Flow().create("investor/create")`` is what someone writes on the day
        they move a mapping flow to the sequence form. A resource never contains
        a slash, so this is unambiguous -- and left through it would build a
        step against a resource that does not exist and fail in a browser.
        """
        with pytest.raises(FlowError, match="looks like a mapping key"):
            Flow().create("investor/create")

        with pytest.raises(FlowError, match="looks like a mapping key"):
            Flow().update("investor/update", id=1)

    def test_01_345_a_delete_rule_is_still_refused(self):
        """Scenario 1.345 (fourth half): the older rule survives the rewrite.

        The app emits ``lex:record_deleted`` and never navigates on it, so a
        delete rule is accepted, serialised, shipped and ignored. Re-checked
        here because the class it lives on was rewritten around it.
        """
        with pytest.raises(FlowError, match="no delete redirect"):
            Flow().after_create("a", "/b")["a/delete"] = "/c"


class TestCluster1ao_TheMappingIsUntouched:
    """The escape hatch pays nothing for the feature it did not ask for."""

    def test_01_346_a_mapping_flow_serialises_exactly_as_it_did(self):
        """Scenario 1.346: byte for byte, no version, no steps.

        The wire format is where a rewrite leaks. A mapping flow's URL must not
        change by a character, or every deployed dashboard using one becomes an
        untested path on the day this ships.
        """
        flow = Flow().after_create("investor", "/cashflow/{id}/edit").after_save("cashflow", STAY)

        assert flow.to_wire() == dict(flow), "the mapping serialises AS the mapping"
        assert flow.is_program is False
        assert "v" not in flow.to_wire()
        assert flow.to_wire() == {
            "investor/create": "/cashflow/{id}/edit",
            "cashflow/create": "self",
            "cashflow/update": "self",
        }

    def test_01_346_dict_update_still_means_dict_update(self):
        """Scenario 1.346 (second half): the shadowed method still works.

        ``Flow.update`` is the step builder AND ``dict.update``. They can never
        be live at once -- a flow with mapping rules refuses steps -- so the
        signature is dispatched on what it was handed.
        """
        flow = Flow({"a/create": "/b"})
        flow.update({"c/create": "/d"})

        assert flow["c/create"] == "/d"
        assert flow.to_wire() == {"a/create": "/b", "c/create": "/d"}


class TestCluster1ao_TheCursorShips:
    """What lex_view puts on the URL, which is the whole contract."""

    def _url(self, monkeypatch, flow):
        from lex.lex_app.streamlit import embed as embed_mod

        captured = {}
        monkeypatch.setattr(
            embed_mod,
            "render_lex_view_component",
            lambda **kwargs: captured.update(kwargs) or None,
        )
        embed_mod.lex_view("investor", flow=flow, on_create=True)
        return urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)

    def test_01_347_a_sequence_ships_with_its_cursor_at_zero(self, monkeypatch):
        """Scenario 1.347: the cursor's PRESENCE is what says a flow is live.

        ``lex_flow`` is snapshotted on the frontend and survives the router
        clearing the query string; the cursor deliberately is not. So when a
        user leaves the flow's path the program is still there and the cursor is
        gone, and absent must mean "over".

        That only works if the start is written down. Letting the frontend
        default a missing cursor to 0 would make leaving the flow indexable
        from the beginning -- the user walked through the whole chain again with
        nothing to explain why.
        """
        params = self._url(monkeypatch, Flow().create("investor").create("vehicle"))

        assert params["lex_step"] == ["0"]
        assert json.loads(params["lex_flow"][0])["v"] == 2

    def test_01_347_a_mapping_ships_no_cursor(self, monkeypatch):
        """Scenario 1.347 (second half): nothing to point at.

        A mapping has no order, so a cursor over it would be meaningless -- and
        emitting one would tell the frontend a sequence is live when none is.
        """
        params = self._url(monkeypatch, Flow().after_create("investor", "/vehicle/create"))

        assert "lex_step" not in params
        assert params["lex_flow"] == ['{"investor/create":"/vehicle/create"}']

    def test_01_347_a_plain_dict_is_still_accepted(self, monkeypatch):
        """Scenario 1.347 (third half): ``flow=`` never required a ``Flow``.

        The documented signature is ``Optional[Dict[str, str]]`` and callers
        pass literals. A plain dict is a mapping by definition: no version, no
        cursor, no validation it never had.
        """
        params = self._url(monkeypatch, {"investor/create": "/vehicle/create"})

        assert "lex_step" not in params
        assert params["lex_flow"] == ['{"investor/create":"/vehicle/create"}']
