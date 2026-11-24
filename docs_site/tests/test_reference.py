"""The API reference covers every name lex.ui exports, from its own docstrings."""

from docs_site import content, nav, reference


def test_every_export_is_in_one_group():
    assert reference.undocumented() == []
    names = [n for group in reference.GROUPS.values() for n in group]
    assert len(names) == len(set(names))


def test_a_reference_page_has_signatures_and_docstrings():
    text = reference.page_source("pages")
    assert text.startswith("---\ntitle: Pages and page kinds")
    assert "## ui.record" in text
    assert "ui.record(*, title" in text


def test_reference_slugs_load_and_are_in_nav():
    for group in reference.GROUPS:
        slug = f"reference/{group}"
        assert slug in content.all_slugs()
        assert slug in nav.ordered()
        assert content.load(slug).title == reference.TITLES[group]
    assert nav.problems() == []


def test_a_docstring_cannot_open_a_live_or_directive_block():
    hostile = "Intro.\n\n```python demo\nboom()\n```\n\n```md nope\nx\n```\n\n~~~python\ny\n~~~\n\n## Heading"
    out = reference._docstring(hostile)
    # the whole docstring sits inside one fence longer than any run inside it
    assert out.startswith("````markdown\n") and out.endswith("\n````")
    # a harmless docstring is left as readable Markdown
    assert reference._docstring("Plain `code` text.") == "Plain `code` text."
