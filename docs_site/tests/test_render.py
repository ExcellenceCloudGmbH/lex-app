"""Markdown becomes lex-look components: headings with anchors, code, callouts, tabs, tables."""

from reflex_docgen.markdown import parse_document

from docs_site.content import Page
from docs_site.render import render_page, toc


def _page(source: str) -> Page:
    doc = parse_document(source)
    return Page(slug="t/x", title="X", description="", source=source, document=doc, headings=[(2, "Setup steps", "setup-steps")])


def test_headings_text_code_and_links():
    built = str(render_page(_page("## Setup steps\n\nRun **this** and see [the record](/pages/record).\n\n"
                                  "```python\nprint(1)\n```\n")))
    assert 'id="setup-steps"' in built or '"setup-steps"' in built
    assert "Setup steps" in built and "this" in built and "/pages/record" in built
    assert "print(1)" in built


def test_alert_and_tabs():
    built = str(render_page(_page("```md alert warning\nCareful.\n```\n\n"
                                  "```md tabs\n## One\nFirst.\n## Two\nSecond.\n```\n")))
    assert "Careful." in built
    assert "One" in built and "Second." in built


def test_a_table():
    built = str(render_page(_page("| a | b |\n|---|---|\n| 1 | 2 |\n")))
    assert "RadixThemesTable.Root" in built and "RadixThemesTable.ColumnHeaderCell" in built
    for cell in ('"a"', '"b"', '"1"', '"2"'):
        assert cell in built


def test_toc_lists_the_headings():
    assert "#setup-steps" in str(toc(_page("## Setup steps\n")))


def test_a_repeated_heading_gets_a_numbered_anchor_in_the_toc_and_the_page():
    from docs_site.content import headings_of

    source = ("## Setup\n\nOne.\n\n## Usage\n\n### Setup\n\nTwo.\n\n"
              "```md alert info\n#### Setup\n```\n\n## Setup 2\n\nThree.\n")
    doc = parse_document(source)
    headings = headings_of(doc)
    assert [a for _, _, a in headings] == ["setup", "usage", "setup-2", "setup-2-2"]
    page = Page(slug="t/dup", title="D", description="", source=source, document=doc, headings=headings)
    built = str(render_page(page))
    for anchor_id in ("setup", "setup-2", "setup-3", "setup-2-2"):
        assert f'"{anchor_id}"' in built, anchor_id
    assert "#setup-2-2" in str(toc(page))
