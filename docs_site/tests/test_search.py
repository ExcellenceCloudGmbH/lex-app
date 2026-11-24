"""Search finds pages and sections by every word typed, titles first."""

from docs_site.search import build_index, find


def test_every_word_must_match_and_titles_come_first():
    index = build_index()
    hits = find(index, "ui.record")
    assert hits and hits[0].slug == "pages/record"
    assert find(index, "zzzz-nothing") == []


def test_a_section_hit_links_to_its_anchor():
    index = build_index()
    section_hits = [e for e in index if e.anchor]
    assert all(e.slug and e.heading for e in section_hits)
