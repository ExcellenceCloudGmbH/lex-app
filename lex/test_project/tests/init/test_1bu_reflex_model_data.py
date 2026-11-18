"""Model data in the Reflex dashboards: through lex-app's own API, as the signed-in user.

Intent: a dashboard shows a model's records exactly as lex-app's grid shows
them to the same user -- the same model and row permissions, the same
field-level filtering -- and changes them exactly as the grid does, with the
same validation, hooks, audit log and calculations. lex-app gets that by
reading and writing through its own REST API with the user's access token
(``lex_api``), under RFDS's server-paged table (``lex_table``), its AG Grid
(``lex_grid``) and a record dashboard's state (``LexRecordState``). A
regression here shows records a user may not see, writes that skip lex-app's
rules, or tables whose filters, sort or paging quietly do nothing.
Cluster 1bu -- scenarios 1.707-1.716. Type: U (1.711: I).
Covers: lex/lex_app/reflex/data.py (LexApi, lex_api, api_base_url, wire_value,
lex_rows, column_kind, lex_columns, lex_grid_columns, filter_params,
scope_params, LexTableState, lex_table, LexGridState, lex_grid,
LexRecordState), lex/lex_app/reflex/__init__.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bu_reflex_model_data.py -v
"""

import asyncio
import datetime
import decimal
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase
from django.urls import resolve

from lex.lex_app.reflex import data

pytestmark = pytest.mark.init


class _Response:
    def __init__(self, status: int, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None and not text else b"x"
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Client:
    """Records each request and answers from a queue."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def request(self, method, url, *, params=None, json=None, headers=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(coro):
    return asyncio.run(coro)


class TestCluster01bu_TheApi(SimpleTestCase):
    """``LexApi``: lex-app's own endpoints, as one user."""

    # -- 1.707 ---------------------------------------------------------
    def test_1_707_calls_lex_apps_own_endpoints_with_the_users_token(self) -> None:
        """
        Scenario 1.707: every call is the request lex-app's frontend would make, as the user.
        Given: an API client for one user's token.
        When:  it lists (with a page, a size, an ordering and repeated filters),
               reads, creates, updates and deletes.
        Then:  each request carries ``Bearer <token>``; the paths are lex-app's
               model-entry routes and resolve to its views; the list carries
               ``page``/``perPage``/``ordering`` and the filters in order; a
               write's operation id names the model and the action; the update
               is a PATCH; and both list shapes come back as ``{count, results}``.
        """
        client = _Client(
            _Response(200, {"count": 3, "results": [{"id": 1}]}),
            _Response(200, [{"id": 1}, {"id": 2}]),
            _Response(200, {"id": 5}),
            _Response(201, {"id": 6}),
            _Response(200, {"id": 5, "name": "b"}),
            _Response(204),
        )
        api = data.LexApi("tok", "https://app.example.com/", client=client)
        page = _run(api.list(Group, page=2, per_page=50, ordering="-name",
                             params=[("name", "a"), ("name", "b"), ("id!", "3")]))
        self.assertEqual(page, {"count": 3, "results": [{"id": 1}]})
        self.assertEqual(_run(api.list("group")), {"count": 2, "results": [{"id": 1}, {"id": 2}]})
        _run(api.get(Group, 5))
        _run(api.create(Group, {"name": "a"}))
        _run(api.update(Group, 5, {"name": "b"}))
        self.assertIsNone(_run(api.delete(Group, 5)))

        methods = [c["method"] for c in client.calls]
        self.assertEqual(methods, ["GET", "GET", "GET", "POST", "PATCH", "DELETE"])
        for call in client.calls:
            self.assertEqual(call["headers"]["Authorization"], "Bearer tok")
            self.assertTrue(call["url"].startswith("https://app.example.com/api/model_entries/group/"))
        self.assertEqual(client.calls[0]["params"], [("page", "2"), ("perPage", "50"), ("ordering", "-name"),
                                                     ("name", "a"), ("name", "b"), ("id!", "3")])
        for call, action in zip(client.calls[2:], ["read", "create", "update", "delete"]):
            self.assertRegex(call["url"], rf"/group/group_{action}_[0-9a-f]{{12}}/one")
        self.assertEqual(client.calls[3]["json"], {"name": "a"})
        # The paths resolve to lex-app's own views (here for a model every project has).
        for path in ("/api/model_entries/auditlog/list", "/api/model_entries/auditlog/auditlog_read_0/one/5",
                     "/api/model_entries/auditlog/auditlog_create_0/one"):
            self.assertIn("model_container", resolve(path).kwargs, path)

    # -- 1.708 ---------------------------------------------------------
    def test_1_708_refusals_and_outages_are_errors_never_empty_data(self) -> None:
        """
        Scenario 1.708: a refused or failed call never looks like "no records".
        Given: answers 403, 404 (a record the user may not see), 500 without
               JSON, and an unreachable API; and no token at all.
        When:  calls are made.
        Then:  each raises ``LexApiError`` with the status (0 when unreachable)
               and the body as detail; 401/403/404 count as ``denied``; and an
               API cannot be made for nobody.
        """
        import httpx

        client = _Client(_Response(403, {"code": "permission_denied"}), _Response(404, {"detail": "x"}),
                         _Response(500, None, text="boom"), httpx.ConnectError("down"))
        api = data.LexApi("tok", "http://x", client=client)
        for status, denied in ((403, True), (404, True), (500, False), (0, False)):
            with self.subTest(status=status), self.assertRaises(data.LexApiError) as caught:
                _run(api.get(Group, 1))
            self.assertEqual(caught.exception.status, status)
            self.assertIs(caught.exception.denied, denied)
        with self.assertRaises(data.LexApiError) as caught:
            data.LexApi("")
        self.assertEqual(caught.exception.status, 401)

    # -- 1.709 ---------------------------------------------------------
    def test_1_709_lex_api_uses_the_sessions_token_and_the_configured_base(self) -> None:
        """
        Scenario 1.709: ``lex_api(state)`` is the API as the user signed in to the session.
        Given: a session with a token; ``LEX_API_URL``, ``DOMAIN_HOSTED`` or neither.
        When:  ``lex_api`` is called and the base URL resolved.
        Then:  it carries that token; the base is ``LEX_API_URL``, else the
               app's domain over https, else a local ``lex start``.
        """
        async def token(_state):
            return "user-token"

        with patch("lex.lex_app.reflex.auth.current_access_token", token):
            api = _run(data.lex_api(SimpleNamespace()))
        self.assertEqual(api._token, "user-token")
        cases = [({"LEX_API_URL": "http://lex-app:8000/", "DOMAIN_HOSTED": "a.example"}, "http://lex-app:8000"),
                 ({"LEX_API_URL": "", "DOMAIN_HOSTED": "a.example"}, "https://a.example"),
                 ({"LEX_API_URL": "", "DOMAIN_HOSTED": ""}, "http://localhost:8000")]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env):
                self.assertEqual(data.api_base_url(), expected)


class TestCluster01bu_WireAndColumns(SimpleTestCase):
    """Values as RFDS carries them, and columns from a model's fields."""

    # -- 1.710 ---------------------------------------------------------
    def test_1_710_values_travel_as_rfds_wire_values(self) -> None:
        """
        Scenario 1.710: what reaches the browser is JSON-native and exact.
        Given: Decimals, dates, datetimes, UUIDs, a related instance, NaN, nested values.
        When:  they are made wire values, and mappings made rows of chosen fields.
        Then:  Decimals become exact strings (no float rounding), dates ISO
               strings, a related instance its key, NaN nothing; mappings keep
               only the chosen fields.
        """
        moment = datetime.datetime(2026, 3, 1, 12, 30, tzinfo=datetime.timezone.utc)
        key = uuid.UUID(int=7)
        self.assertEqual(data.wire_value(decimal.Decimal("1234567890.123456789")), "1234567890.123456789")
        self.assertEqual(data.wire_value(datetime.date(2026, 3, 1)), "2026-03-01")
        self.assertEqual(data.wire_value(moment), "2026-03-01T12:30:00+00:00")
        self.assertEqual(data.wire_value(key), str(key))
        self.assertEqual(data.wire_value(SimpleNamespace(pk=42)), 42)
        self.assertIsNone(data.wire_value(float("nan")))
        self.assertEqual(data.wire_value({"a": [decimal.Decimal("1.50")]}), {"a": ["1.50"]})
        rows = data.lex_rows([{"a": decimal.Decimal("2"), "b": 1, "c": 2}], ["a", "b"])
        self.assertEqual(rows, [{"a": "2", "b": 1}])

    # -- 1.712 ---------------------------------------------------------
    def test_1_712_columns_follow_each_fields_type_and_skip_credentials(self) -> None:
        """
        Scenario 1.712: a model's default columns are its readable fields, each shown as its type.
        Given: Django's ``User`` (an id, text, booleans, datetimes, a password).
        When:  its RFDS table and grid columns are built, by default and for chosen fields.
        Then:  the id is an id, booleans booleans, datetimes instants, text
               text; ``password`` -- a field lex-app's API never publishes -- is
               not a column; headers are the verbose names; an override changes
               one column; an unknown field is refused.
        """
        columns = {c["field"]: c for c in data.lex_columns(User)}
        self.assertNotIn("password", columns, "the API never returns credential-shaped fields")
        self.assertEqual(columns["id"]["kind"], "id")
        self.assertEqual(columns["is_staff"]["kind"], "boolean")
        self.assertEqual(columns["date_joined"]["kind"], "instant")
        self.assertEqual(columns["username"]["kind"], "text")
        self.assertEqual(columns["date_joined"]["header"], "Date joined")
        chosen = data.lex_columns(User, ["username", "date_joined"], date_joined={"header": "Joined"})
        self.assertEqual([(c["field"], c["header"]) for c in chosen], [("username", "Username"), ("date_joined", "Joined")])
        grid = data.lex_grid_columns(User, ["id", "is_active"])
        self.assertEqual([c["field"] for c in grid], ["id", "is_active"])
        with self.assertRaises(ValueError):
            data.lex_columns(User, ["nope"])

    # -- 1.713 ---------------------------------------------------------
    def test_1_713_table_filters_become_lex_list_filters(self) -> None:
        """
        Scenario 1.713: every RFDS filter means the same in lex-app's list API.
        Given: one filter of each RFDS operator, a malformed field name, and fixed filters.
        When:  they are turned into list parameters.
        Then:  eq/ne/in/not_in/lt/lte/gt/gte/contains/starts_with/between/blank/
               not_blank become lex-app's lookups (``!`` to exclude, a repeated
               field for several values); the malformed name is dropped; fixed
               filters skip empty values and repeat lists.
        """
        params = data.filter_params({
            "a": {"op": "eq", "value": 1}, "b": {"op": "ne", "value": "x"},
            "c": {"op": "in", "values": ["1", "2"]}, "d": {"op": "not_in", "values": ["3"]},
            "e": {"op": "lt", "value": 5}, "f": {"op": "gte", "value": 6},
            "g": {"op": "contains", "value": "ab"}, "h": {"op": "starts_with", "value": "Ab"},
            "i": {"op": "between", "value": "2026-01-01", "value_to": "2026-12-31"},
            "j": {"op": "blank"}, "k": {"op": "not_blank"}, "x; drop": {"op": "eq", "value": 1},
        })
        self.assertEqual(params, [
            ("a", "1"), ("b!", "x"), ("c", "1"), ("c", "2"), ("d!", "3"), ("e__lt", "5"), ("f__gte", "6"),
            ("g__icontains", "ab"), ("h__istartswith", "Ab"), ("i__gte", "2026-01-01"), ("i__lte", "2026-12-31"),
            ("j__blank", "true"), ("k__blank", "false"),
        ])
        self.assertEqual(data.scope_params({"fund": 3, "status": ["a", "b"], "empty": "", "none": None}),
                         [("fund", "3"), ("status", "a"), ("status", "b")])


class TestCluster01bu_RowsFromAQueryset(TestCase):
    """``lex_rows`` over a real queryset."""

    # -- 1.711 ---------------------------------------------------------
    def test_1_711_lex_rows_reads_a_queryset_with_values(self) -> None:
        """
        Scenario 1.711: a queryset becomes chart rows of the chosen fields, related paths included.
        Given: two groups in the database.
        When:  ``lex_rows`` reads ``Group.objects.order_by("name")`` for id and name, and the instances.
        Then:  the rows hold exactly those fields as wire values, in the queryset's order.
        """
        first, second = Group.objects.create(name="Alpha"), Group.objects.create(name="Beta")
        self.assertEqual(data.lex_rows(Group.objects.order_by("name"), ["id", "name"]),
                         [{"id": first.pk, "name": "Alpha"}, {"id": second.pk, "name": "Beta"}])
        self.assertEqual(data.lex_rows(list(Group.objects.order_by("name"))),
                         [{"id": first.pk, "name": "Alpha"}, {"id": second.pk, "name": "Beta"}])


class TestCluster01bu_TablesGridsAndRecords(SimpleTestCase):
    """``lex_table``, ``lex_grid`` and ``LexRecordState`` over the API."""

    # -- 1.714 ---------------------------------------------------------
    def test_1_714_lex_table_pages_sorts_filters_and_searches_through_the_api(self) -> None:
        """
        Scenario 1.714: RFDS's table asks lex-app's list API for exactly its page.
        Given: ``lex_table(Group, filters={"name": <a state var>})``.
        When:  it is built, and its state fetches page 3 of 25 rows sorted by
               name descending then id, filtered and quick-searched.
        Then:  one state class per distinct table (built once, reused; a
               differently filtered table gets its own), opening at its page size;
               mounting passes the fixed filters (the var, read at mount);
               the API is asked for page 3 of 25 with that ordering, the fixed
               filters, the table filters and the search on the default text
               field; rows come back as wire rows with the total. Each table's
               state class pickles as itself -- Reflex's state managers pickle
               state, to disk in development and to Redis across workers.
        """
        import pickle

        pickled = data.table_state(Group, columns=data.lex_columns(Group), page_size=10)
        self.assertIs(pickle.loads(pickle.dumps(pickled)), pickled, "a table's state must pickle by name")
        import reflex as rx

        class Scope(rx.State):
            team: str = "Alpha"

        component = data.lex_table(Group, filters={"name": Scope.team}, page_size=25)
        state = data.table_state(Group, columns=data.lex_columns(Group), search_field="name", page_size=25,
                                 filters={"name": Scope.team})
        self.assertIs(state, data.table_state(Group, columns=data.lex_columns(Group), search_field="name",
                                              page_size=25, filters={"name": Scope.team}),
                      "one state class per distinct table")
        self.assertIsNot(state, data.table_state(Group, columns=data.lex_columns(Group), search_field="name",
                                                 page_size=25, filters={"name": "Beta"}),
                         "differently filtered tables never share rows")
        self.assertEqual(state.resource, "group")
        self.assertEqual(state.get_fields()["page_size"].default, 25, "the table opens at its page size")
        self.assertIn(10, data.table_state(Group, columns=data.lex_columns(Group), page_size=10).page_size_options)
        mount = str(component.event_triggers["on_mount"])
        self.assertIn(".open", mount)
        self.assertIn("team", mount, "the fixed filter is the state var, resolved when the table mounts")

        client = _Client(_Response(200, {"count": 61, "results": [{"id": 1, "name": "Alpha"}]}))

        async def api(_state):
            return data.LexApi("tok", "http://x", client=client)

        fake = type("Table", (), {"resource": "group", "search_field": "name"})()
        fake.scope = {"name": "Alpha"}
        with patch.object(data, "lex_api", api):
            rows, total = _run(state._fetch_page(
                fake, offset=50, limit=25,
                sort=[{"col_id": "name", "sort": "desc", "index": 0}, {"col_id": "id", "sort": "asc", "index": 1}],
                filters={"id": {"op": "gte", "value": 3}}, query="alp"))
        self.assertEqual((rows, total), ([{"id": 1, "name": "Alpha"}], 61))
        self.assertEqual(client.calls[0]["params"], [
            ("page", "3"), ("perPage", "25"), ("ordering", "-name,id"),
            ("name", "Alpha"), ("id__gte", "3"), ("name__icontains", "alp"),
        ])

    # -- 1.715 ---------------------------------------------------------
    def test_1_715_lex_grid_loads_rows_once_says_when_cut_and_hides_on_refusal(self) -> None:
        """
        Scenario 1.715: RFDS's AG Grid gets the model's rows from the API, within RFDS's state budget.
        Given: ``lex_grid(Group)`` and a model with more rows than the grid holds; then a refusal.
        When:  the grid mounts and loads.
        Then:  it asks for the first page of at most ``GRID_ROW_LIMIT`` rows with
               its fixed filters, holds them as wire rows, says it was cut, and
               bumps its dataset version; refused, it holds nothing and says so.
        """
        component = data.lex_grid(Group, filters={"name": "Alpha"})
        self.assertIn("lex-grid-group", str(component))
        self.assertIn("Alpha", str(component.event_triggers["on_mount"]))
        state = data.grid_state(Group, filters={"name": "Alpha"})
        self.assertIsNot(state, data.grid_state(Group, filters={"name": "Beta"}),
                         "differently filtered grids never share rows")
        self.assertIsNot(data.grid_state(Group, fields=["id"]), data.grid_state(Group, fields=["name"]))
        client = _Client(_Response(200, {"count": data.GRID_ROW_LIMIT + 5, "results": [{"id": 1}]}),
                         _Response(403, {"code": "permission_denied"}))

        async def api(_state):
            return data.LexApi("tok", "http://x", client=client)

        fake = type("Grid", (), {"resource": "group"})()
        fake.__dict__.update(scope={"name": "Alpha"}, rows=[], total_rows=0, truncated=False,
                             dataset_version=0, error="")
        with patch.object(data, "lex_api", api):
            _run(state._reload(fake))
            self.assertEqual(client.calls[0]["params"],
                             [("page", "1"), ("perPage", str(data.GRID_ROW_LIMIT)), ("name", "Alpha")])
            self.assertEqual((fake.rows, fake.truncated, fake.dataset_version, fake.error),
                             ([{"id": 1}], True, 1, ""))
            _run(state._reload(fake))
        self.assertEqual((fake.rows, fake.total_rows, fake.error), ([], 0, "denied"))

    # -- 1.716 ---------------------------------------------------------
    def test_1_716_a_record_dashboard_gets_only_records_the_api_would_show(self) -> None:
        """
        Scenario 1.716: ``LexRecordState.record()`` checks the API before handing out the instance.
        Given: a record dashboard for ``?model=group&pk=5``.
        When:  the API shows the record; refuses it (403); hides it (404);
               and the URL names no record.
        Then:  shown: the instance, its title and wire data, state "ok";
               refused: ``None`` and "denied"; hidden or absent: ``None`` and
               "missing"; lex-app failing: "unavailable", never "missing" --
               the instance is never read for a record the API would not show.
               And a dashboard written as the docs write it --
               ``class Dash(LexRecordState)`` with an ``@rx.event`` -- is a real
               state whose handler binds, not a mixin a page cannot mount.
        """
        import reflex as rx
        from reflex_base.event import EventHandler

        class Dash116(data.LexRecordState):
            @rx.event
            async def load(self):
                await self.record()

        self.assertFalse(Dash116._mixin, "a LexRecordState subclass must be a state, not another mixin")
        self.assertIsInstance(Dash116.load, EventHandler, "the subclass's handler must bind for on_mount")
        self.assertIn("record_state", Dash116.vars, "the record vars reach the subclass")

        group = SimpleNamespace(pk=5)
        reads = []

        async def current(_state, model):
            reads.append(model)
            return group

        async def run_orm(fn, *args):
            return "Alpha"

        def fake(pk="5"):
            return SimpleNamespace(pk=pk, router=SimpleNamespace(url=SimpleNamespace(
                query_parameters={"model": "group", "pk": pk})), record_title="", record_data={}, record_state="",
                _record_model=lambda: Group)

        for answer, expected_state, expect_instance in (
                (_Response(200, {"id": 5, "name": "Alpha", "rate": "1.50"}), "ok", True),
                (_Response(403, {"code": "permission_denied"}), "denied", False),
                (_Response(404, {"detail": "not found"}), "missing", False),
                (_Response(502, None, text="bad gateway"), "unavailable", False)):
            client = _Client(answer)

            async def api(_state, client=client):
                return data.LexApi("tok", "http://x", client=client)

            state = fake()
            reads.clear()
            with self.subTest(state=expected_state), patch.object(data, "lex_api", api), \
                    patch("lex.lex_app.reflex.dashboards.current_record", current), \
                    patch("lex.lex_app.reflex.django_orm.run_orm", run_orm):
                result = _run(data.LexRecordState.record.fn(state))
                self.assertEqual(state.record_state, expected_state)
                self.assertIs(result, group if expect_instance else None)
                self.assertEqual(reads, [Group] if expect_instance else [],
                                 "the instance is read only after the API showed the record")
        state = fake()
        self.assertEqual(state.record_title, "")
        with patch.object(data, "lex_api", api):
            no_pk = fake(pk="")
            self.assertIsNone(_run(data.LexRecordState.record.fn(no_pk)))
            self.assertEqual(no_pk.record_state, "missing")
