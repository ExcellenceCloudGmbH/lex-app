"""Pages are read from content/; nav.py lists every page once and nothing else."""

from docs_site import content, nav


def test_nav_and_content_agree():
    assert nav.problems() == []


def test_a_page_has_a_title_and_anchored_headings():
    page = content.load("show-your-data/one-record")
    assert page.title == "Show one record"
    assert page.api == ("ui.record",)
    assert all(anchor.startswith(content.anchor(text)) for _, text, anchor in page.headings)


def test_titles_say_what_a_page_does_not_what_it_is_called_in_code():
    """Outside the API reference, no page is titled by an API name; those are its api: tags."""
    for slug in nav.ordered():
        if slug.startswith("reference/"):
            continue
        title = content.load(slug).title
        assert not title.startswith(("@", "ui.", "self.", "lex ")), f"{slug}: {title!r}"


def test_seven_chapters_each_one_list_of_pages_then_the_reference():
    assert [s.slug for s in nav.CHAPTERS] == ["start-here", "show-your-data", "let-people-act",
                                              "long-running-work", "who-can-do-what", "make-it-your-app",
                                              "test-and-ship"]
    assert nav.NAV[-1].slug == "reference" and nav.number(nav.NAV[-1]) is None
    for section in nav.NAV:
        assert len(section.groups) == 1, section.slug
        assert section.story.endswith("."), section.slug
    assert [nav.number(s) for s in nav.CHAPTERS] == list(range(1, 8))


def test_an_api_list_that_is_not_a_list_is_refused(tmp_path, monkeypatch):
    import pytest

    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "x.md").write_text('---\ntitle: "X"\napi: "ui.record"\n---\n\nText.\n')
    monkeypatch.setattr(content, "CONTENT", tmp_path)
    content.load.cache_clear()
    try:
        with pytest.raises(ValueError, match="api: is a list"):
            content.load("t/x")
    finally:
        content.load.cache_clear()


def test_anchor_is_stable_and_url_safe():
    assert content.anchor("State & actions: `@ui.job`") == "state-actions-ui-job"


def test_neighbours_follow_reading_order():
    order = nav.ordered()
    assert nav.neighbours(order[0])[0] is None
    assert nav.neighbours(order[1]) == (order[0], order[2])
    assert nav.neighbours(order[-1])[1] is None


def test_every_page_loads_with_a_title():
    for slug in content.all_slugs():
        assert content.load(slug).title, slug
