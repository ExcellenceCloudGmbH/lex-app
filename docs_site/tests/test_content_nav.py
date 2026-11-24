"""Pages are read from content/; nav.py lists every page once and nothing else."""

from docs_site import content, nav


def test_nav_and_content_agree():
    assert nav.problems() == []


def test_a_page_has_a_title_and_anchored_headings():
    page = content.load("pages/record")
    assert page.title == "ui.record"
    assert all(anchor == content.anchor(text) for _, text, anchor in page.headings)


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
