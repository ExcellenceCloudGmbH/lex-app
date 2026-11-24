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


def test_the_first_dashboard_closes_a_fund():
    import sys

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store
    from lex.lex_app.reflex.ui import testing

    render_page(content.load("get-started/first-dashboard"))
    funds = sys.modules["docs_site.pages.get_started_first_dashboard"].Funds
    api = SandboxApi(Store().tab("x"))
    testing.run(funds, "close", grants=[("docs.Fund", "edit")], api=api, pk=2)
    assert api.get(Fund, 2)["status"] == "closed"


def test_a_viewer_cannot_close_a_fund():
    import sys

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store
    from lex.lex_app.reflex.ui import testing

    render_page(content.load("get-started/first-dashboard"))
    funds = sys.modules["docs_site.pages.get_started_first_dashboard"].Funds
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


def test_every_reads_past_the_first_page():
    import sys

    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store

    render_page(content.load("pages/tables"))
    every = sys.modules["docs_site.pages.pages_tables"].every
    api = SandboxApi(Store().tab("x"))
    for n in range(250):
        api.create(Fund, {"name": f"Fund {n}", "status": "investing"})
    assert api.list(Fund).count == 256 and len(api.list(Fund)) == 25
    assert len(every(api, Fund)) == 256
    assert len(every(api, Fund, filters={"status": "investing"})) == 253


EVERY = '''def every(api, model, **options):
    """Every matching record: self.api.list returns one page of them at a time."""
    ordering = options.pop("ordering", None)
    ordering = f"{ordering},id" if ordering else "id"  # a stable order: no row skipped or repeated
    rows, page = [], 1
    while True:
        found = api.list(model, page=page, per_page=100, ordering=ordering, **options)
        rows.extend(found)
        if not found or len(rows) >= found.count:
            return rows
        page += 1
'''


def test_every_copy_of_the_every_helper_is_the_same():
    copies = []
    for path in sorted(content.CONTENT.rglob("*.md")):
        for match in re.finditer(r"^def every\(.*?(?=^\S|^```)", path.read_text(encoding="utf-8"),
                                 re.M | re.S):
            copies.append((path.name, match.group(0).rstrip("\n") + "\n"))
    assert len(copies) >= 11
    for name, text in copies:
        assert text == EVERY, f"{name}: its every() differs from the canonical one"


def test_every_breaks_ties_on_the_id():
    from docs_site.sandbox.api import SandboxApi
    from docs_site.sandbox.models import Fund
    from docs_site.sandbox.store import Store

    calls = []

    class Spy(SandboxApi):
        def list(self, model, **kwargs):
            calls.append(kwargs.get("ordering"))
            return super().list(model, **kwargs)

    namespace: dict = {}
    exec(EVERY, namespace)
    api = Spy(Store().tab("every"))
    assert [r["id"] for r in namespace["every"](api, Fund)] == [1, 2, 3, 4, 5, 6]
    namespace["every"](api, Fund, ordering="-vintage")
    assert calls == ["id", "-vintage,id"]
