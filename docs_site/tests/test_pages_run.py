"""Every page renders: its setup runs, every demo builds, every internal link resolves."""

import re

import pytest

from docs_site import content, nav
from docs_site.layout import route_of
from docs_site.render import render_page

SLUGS = ["index", *nav.ordered()]
ROUTES = {route_of(s) for s in SLUGS}
ANCHORS = {route_of(s): {a for _, _, a in content.load(s).headings} for s in SLUGS}


@pytest.mark.parametrize("slug", SLUGS)
def test_the_page_renders(slug):
    render_page(content.load(slug))


@pytest.mark.parametrize("slug", SLUGS)
def test_internal_links_resolve(slug):
    for target in re.findall(r"\]\((/[^)\s]*)\)", content.load(slug).source):
        path, _, frag = target.partition("#")
        if path.startswith("/md/") or path in ("/llms.txt", "/llms-full.txt"):
            continue
        assert path in ROUTES, f"{slug}: link to {target}"
        if frag:
            assert frag in ANCHORS[path], f"{slug}: no #{frag} on {path}"
    # a link to a heading of the same page: the heading must be on this page (a split page moves them)
    own = {a for _, _, a in content.load(slug).headings}
    for frag in re.findall(r"\]\(#([^)\s]+)\)", content.load(slug).source):
        assert frag in own, f"{slug}: no #{frag} on this page"


def test_the_first_dashboard_closes_a_fund():
    import sys

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store
    from lex.lex_app.reflex.ui import testing

    render_page(content.load("start-here/first-dashboard"))
    funds = sys.modules["docs_site.pages.start_here_first_dashboard"].Funds
    api = SandboxApi(Store().tab("x"))
    testing.run(funds, "close", grants=[("docs.Fund", "edit")], api=api, pk=2)
    assert api.get(Fund, 2)["status"] == "closed"


def test_a_viewer_cannot_close_a_fund():
    import sys

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store
    from lex.lex_app.reflex.ui import testing

    render_page(content.load("start-here/first-dashboard"))
    funds = sys.modules["docs_site.pages.start_here_first_dashboard"].Funds
    api = SandboxApi(Store().tab("x"))
    result = testing.run(funds, "close", grants=[("docs.Fund", "read")], api=api, pk=2)
    assert result.denied
    assert api.get(Fund, 2)["status"] == "investing"


@pytest.mark.parametrize("slug", SLUGS)
def test_the_code_a_reader_sees_is_the_code_that_runs(slug):
    """A setup fence that defines a class is followed by a plain copy of it, with the reader's import."""
    fences = re.findall(r"```python( exec)?\n(.*?)```", content.load(slug).source, flags=re.S)
    for index, (is_exec, code) in enumerate(fences):
        if is_exec and "class " in code:
            assert index + 1 < len(fences) and not fences[index + 1][0], f"{slug}: setup with no shown copy"
            shown = fences[index + 1][1]
            assert shown == code.replace("docs_site.sandbox.models", "myproject.models"), \
                f"{slug}: the shown copy differs from the setup that runs"


def test_all_reads_past_the_first_page():
    """The sandbox's self.api.all is lex.ui's own: page after page, ties broken on the id."""
    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store

    calls = []

    class Spy(SandboxApi):
        def list(self, model, **kwargs):
            calls.append((kwargs.get("ordering"), kwargs.get("per_page")))
            return super().list(model, **kwargs)

    api = Spy(Store().tab("all"))
    assert [r["id"] for r in api.all(Fund)] == [1, 2, 3, 4, 5, 6]
    api.all(Fund, ordering="-vintage")
    assert calls == [("id", 1000), ("-vintage,id", 1000)]
    for n in range(1100):
        api.create(Fund, {"name": f"Fund {n}", "status": "investing"})
    assert len(api.all(Fund)) == 1106
    assert len(api.all(Fund, filters={"status": "investing"})) == 1103


def test_all_refuses_more_than_it_holds(monkeypatch):
    import pytest

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store
    from lex import ui
    from lex.lex_app.reflex.ui import context

    monkeypatch.setattr(context, "ALL_LIMIT", 5)
    with pytest.raises(ui.Refuse, match="There are 6 funds here, more than can be shown at once"):
        SandboxApi(Store().tab("all")).all(Fund)


def test_no_page_copies_a_paging_helper():
    """Every record is self.api.all; a page that pages by hand shows the reader the old way."""
    for path in sorted(content.CONTENT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "def every(" not in text and "every(self.api" not in text, path.name
