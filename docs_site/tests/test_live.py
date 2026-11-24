"""Live fences: setup code, demos of components, page kinds and parts, one module per page."""

import sys

import pytest
import reflex as rx

from docs_site.render.live import PageModule, to_component

_STATE = '''
from lex import ui
from docs_site.sandbox.models import Fund

class Funds(ui.State):
    @ui.rows
    def rows(self):
        return self.api.list(Fund, filters={"status": "investing"})
'''


def test_states_belong_to_their_page_module():
    a, b = PageModule("t/a"), PageModule("t/b")
    a.run(_STATE)
    b.run(_STATE)
    assert a.namespace["Funds"] is not b.namespace["Funds"]
    assert sys.modules["docs_site.pages.t_a"].Funds is a.namespace["Funds"]


def test_a_demo_value_is_its_last_expression():
    page = PageModule("t/c")
    assert page.value("x = 2\nx * 21") == 42
    with pytest.raises(ValueError, match="last line"):
        page.value("y = 1")


def test_parts_and_kinds_become_components():
    page = PageModule("t/d")
    page.run(_STATE)
    table = page.value('ui.table(Funds.rows, columns=["name", "vintage"])')
    assert "rfds-data-table" in str(to_component(table, "funds"))
    record = page.value('ui.record(title="Northbridge Growth II", facts=[ui.fact("Vintage", "2022")], '
                        'sections=[ui.section("Strategy", ui.text("Growth equity"))])')
    assert "Northbridge Growth II" in str(to_component(record, "funds"))
    assert isinstance(to_component(rx.text("hi"), "x"), rx.Component)
    with pytest.raises(TypeError, match="t/d"):
        to_component(object(), "t/d")


def _page(slug, src):
    from reflex_docgen.markdown import parse_document

    from docs_site.content import Page

    return Page(slug=slug, title="E", description="", source=src, document=parse_document(src), headings=[])


def test_a_demo_fence_shows_code_and_result():
    from docs_site.render import render_page

    src = "```python exec\n" + _STATE + "```\n\n```python demo\nui.table(Funds.rows, columns=[\"name\"])\n```\n"
    built = str(render_page(_page("t/e", src)))
    assert "ui.table(Funds.rows" in built and "rfds-data-table" in built


def test_a_page_module_is_reused_for_the_same_slug():
    assert PageModule("t/f").module is PageModule("t/f").module
    assert PageModule("t/f").module is sys.modules["docs_site.pages.t_f"]


def test_rendering_a_page_twice_runs_its_code_once():
    from docs_site.render import render_page

    src = ("```python exec\nimport docs_site\ndocs_site.__dict__.setdefault('_t_g_runs', []).append(1)\n"
           + _STATE + "```\n\n```python demo\nui.table(Funds.rows, columns=[\"name\"])\n```\n")
    page = _page("t/g", src)
    import docs_site

    first = render_page(page)
    second = render_page(page)
    assert second is first
    assert docs_site._t_g_runs == [1]
