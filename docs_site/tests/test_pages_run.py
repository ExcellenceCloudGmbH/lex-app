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
