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
    store.tab("a").role = "viewer"
    now[0] = 11.0
    store.tab("b")                       # any access sweeps
    assert store.tab("a").role == "editor"
