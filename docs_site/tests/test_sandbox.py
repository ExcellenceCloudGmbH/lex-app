"""The sandbox: sample models, a copy of the data per tab, idle copies dropped."""

from docs_site.sandbox import data, models
from docs_site.sandbox.store import Store


def test_models_answer_what_lex_ui_reads():
    from lex.lex_app.reflex.auth import _resource_name
    from lex.lex_app.reflex.data import model_id

    assert _resource_name(models.Fund) == "docs.Fund"
    assert model_id(models.Commitment) == "commitment"
    assert set(models.MODELS) == {"Fund", "Investor", "Commitment"}


def test_seed_rows_have_ids_and_every_field():
    rows = data.seed()
    for name, model in models.MODELS.items():
        assert rows[name], name
        for row in rows[name]:
            assert set(row) == set(model.fields), (name, row)
        ids = [row["id"] for row in rows[name]]
        assert len(ids) == len(set(ids))


def test_each_tab_has_its_own_copy():
    store = Store()
    store.tab("a").rows["Fund"][0]["name"] = "Changed"
    assert store.tab("b").rows["Fund"][0]["name"] != "Changed"
    assert store.tab("a").rows["Fund"][0]["name"] == "Changed"


def test_reset_restores_the_seed():
    store = Store()
    store.tab("a").rows["Fund"].clear()
    store.reset("a")
    assert store.tab("a").rows["Fund"] == data.seed()["Fund"]


def test_an_idle_tab_is_dropped():
    now = [0.0]
    store = Store(clock=lambda: now[0], idle_seconds=10)
    store.tab("a").rows["Fund"].clear()
    now[0] = 11.0
    store.tab("b")                       # any access sweeps
    assert store.tab("a").rows["Fund"] == data.seed()["Fund"]


import pytest

from docs_site.sandbox.api import SandboxApi
from docs_site.sandbox.models import Commitment, Fund
from lex.lex_app.reflex.data import LexApiError


def _api():
    return SandboxApi(Store().tab("t"))


def test_list_filters_searches_orders_and_pages():
    api = _api()
    rows = api.list(Fund, filters={"status": "investing"}, ordering="-size", per_page=2)
    assert [r["name"] for r in rows] == ["Harbour Infrastructure", "Northbridge Growth II"]
    assert rows.count == 3
    assert [r["name"] for r in api.list(Fund, search="birch")] == ["Birch Secondaries"]
    assert [r["id"] for r in api.list(Commitment, filters={"amount__gte": 60_000_000})] == [3, 5, 6]
    assert [r["id"] for r in api.list(Fund, filters={"id__in": [1, 5]})] == [1, 5]
    assert set(api.list(Fund, fields=["id", "name"])[0]) == {"id", "name"}


def test_create_update_delete_change_only_this_tab():
    store = Store()
    api = SandboxApi(store.tab("t"))
    made = api.create(Fund, {"name": "New", "vintage": 2024, "status": "fundraising",
                             "size": 1, "currency": "EUR"})
    assert made["id"] == 7
    assert api.update(Fund, 7, {"status": "investing"})["status"] == "investing"
    api.delete(Fund, 7)
    with pytest.raises(LexApiError) as missing:
        api.get(Fund, 7)
    assert missing.value.status == 404
    assert len(SandboxApi(store.tab("other")).list(Fund)) == 6


def test_an_unknown_model_is_refused():
    class Elsewhere:
        pass

    with pytest.raises(LexApiError):
        _api().list(Elsewhere)


import asyncio
from types import SimpleNamespace

from docs_site import sandbox
from docs_site.sandbox.store import STORE


def _fake_state(tab: str, role: str | None = None):
    """A state as the resolver sees it; with ``role``, its tab's RoleState holds that role."""
    router = SimpleNamespace(session=SimpleNamespace(client_token=tab), url="/show-your-data/one-record", page=SimpleNamespace(params={}))
    state = SimpleNamespace(router=router)
    if role is not None:
        from docs_site.sandbox.role import RoleState

        async def get_state(state_cls):
            assert state_cls is RoleState
            return SimpleNamespace(role=role)

        state.get_state = get_state
    return state


def test_the_resolver_gives_the_tab_its_data_and_role():
    STORE.reset("tab-r")
    session = asyncio.run(sandbox.resolve(_fake_state("tab-r", "viewer")))
    grants = asyncio.run(session.permissions())
    assert session.tab == "tab-r"
    assert session.user.username == "demo"
    assert {"rsname": "docs.Fund", "scopes": ["read"]} in grants
    assert all(g["scopes"] == ["read"] for g in grants)
    assert session.api.list(Fund).count == 6


def test_a_lex_ui_handler_reads_the_sandbox():
    from lex import ui
    from lex.lex_app.reflex.ui import testing

    class OpenFunds(ui.State):
        names: list[str] = []

        @ui.load
        def open(self):
            self.names = [r["name"] for r in self.api.list(Fund, filters={"status": "investing"})]

    result = testing.run(OpenFunds, "open", api=SandboxApi(Store().tab("h")))
    assert result.fields["names"] == ["Northbridge Growth II", "Harbour Infrastructure",
                                      "Alder Credit Opportunities"]


def _grants(tab: str, role: str | None):
    session = asyncio.run(sandbox.resolve(_fake_state(tab, role)))
    return asyncio.run(session.permissions())


def test_a_viewer_cannot_edit_and_an_editor_can():
    from lex.lex_app.reflex.ui import guard

    need = (guard.edit(Fund),)
    assert guard.granted((guard.read(Fund),), _grants("tab-v", "viewer")) is True
    assert guard.granted(need, _grants("tab-v", "viewer")) is False
    assert guard.granted(need, _grants("tab-e", "editor")) is True
    assert guard.granted((guard.delete(Fund),), _grants("tab-e", "editor")) is True


def test_a_state_without_get_state_is_an_editor():
    from lex.lex_app.reflex.ui import guard

    assert guard.granted((guard.edit(Fund),), _grants("tab-n", None)) is True


def test_the_role_is_read_from_a_real_state_tree():
    """Reflex's get_state on a state instance finds the tab's RoleState (the in-memory tree)."""
    import reflex as rx

    from docs_site.sandbox.role import RoleState

    root = rx.State(_reflex_internal_init=True)
    root.get_substate(RoleState.get_full_name().split(".")[1:]).role = "viewer"
    assert asyncio.run(sandbox.role_of(root)) == "viewer"


def _set_role(tab: str, value):
    from docs_site.sandbox.role import RoleState

    handler = RoleState.set_role
    fn = getattr(handler, "fn", handler)
    stub = SimpleNamespace(router=SimpleNamespace(session=SimpleNamespace(client_token=tab)), role="editor")
    fn(stub, value)
    return stub


def test_set_role_sets_the_role_and_ignores_bad_values():
    assert _set_role("tab-s", "viewer").role == "viewer"
    assert _set_role("tab-s", "admin").role == "editor"     # refused: the stub keeps its own
    assert _set_role("tab-s", ["viewer"]).role == "viewer"


def test_the_switch_builds():
    from docs_site.sandbox import role

    assert type(role.switch()).__name__ == "SegmentedControlRoot"
