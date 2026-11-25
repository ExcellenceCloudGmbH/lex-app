"""Each page as Markdown, and the indexes an AI tool reads."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from docs_site import llms, nav


def test_a_page_as_markdown_has_title_and_no_front_matter():
    text = llms.page_markdown("show-your-data/one-record")
    assert text.startswith("# Show one record\n")
    assert "title:" not in text.split("\n\n", 2)[1]
    assert "In the API: `ui.record`" in text and "api:" not in text


def test_llms_txt_lists_every_chapter_in_order_and_page():
    text = llms.llms_txt()
    places = []
    for section in nav.NAV:
        n = nav.number(section)
        heading = f"## {n}. {section.title}" if n else f"## {section.title}"
        assert heading in text and section.story in text
        places.append(text.index(heading))
    assert places == sorted(places)
    for slug in nav.ordered():
        assert f"/md/{slug}.md)" in text


def test_routes_serve_markdown():
    client = TestClient(Starlette(routes=llms.routes()))
    page = client.get("/md/show-your-data/one-record.md")
    assert page.status_code == 200 and page.text.startswith("# Show one record")
    assert client.get("/md/nope.md").status_code == 404
    assert client.get("/md/../../etc/passwd.md").status_code == 404
    assert client.get("/llms.txt").status_code == 200
    assert "# Show one record" in client.get("/llms-full.txt").text
