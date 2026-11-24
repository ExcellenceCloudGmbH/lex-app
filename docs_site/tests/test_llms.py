"""Each page as Markdown, and the indexes an AI tool reads."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from docs_site import llms, nav


def test_a_page_as_markdown_has_title_and_no_front_matter():
    text = llms.page_markdown("pages/record")
    assert text.startswith("# ui.record\n")
    assert "title:" not in text.split("\n\n", 2)[1]


def test_llms_txt_lists_every_section_and_page():
    text = llms.llms_txt()
    for section in nav.NAV:
        assert f"## {section.title}" in text
    for slug in nav.ordered():
        assert f"/md/{slug}.md)" in text


def test_routes_serve_markdown():
    client = TestClient(Starlette(routes=llms.routes()))
    page = client.get("/md/pages/record.md")
    assert page.status_code == 200 and page.text.startswith("# ui.record")
    assert client.get("/md/nope.md").status_code == 404
    assert client.get("/md/../../etc/passwd.md").status_code == 404
    assert client.get("/llms.txt").status_code == 200
    assert "# ui.record" in client.get("/llms-full.txt").text
