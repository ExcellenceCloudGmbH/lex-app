"""The API reference covers every name lex.ui exports, from its own docstrings."""

import pytest
from reflex_docgen.markdown import parse_document

from docs_site import content, nav, reference
from docs_site.content import Page
from docs_site.render import render_page


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


HOSTILE = (
    "## Heading\n\n> ```python exec\n> raise RuntimeError('ran')\n> ```\n\n"
    "- a\n  - b\n\n    ```python exec\n    raise RuntimeError('ran')\n    ```\n\n"
    "10. item\n\n    ```python exec\n    raise RuntimeError('ran')\n    ```\n\n"
    "```python exec\nraise RuntimeError('ran')\n```\n\n```md nonsense\nnot a directive\n```\n\n"
    "```text\nplain\n```\n"
)


def _page(slug: str, source: str = HOSTILE) -> Page:
    return Page(slug=slug, title="X", description="", source=source, document=parse_document(source),
                headings=[])


def test_a_docstring_cannot_run_code_or_raise_in_a_reference_page():
    built = str(render_page(_page("reference/hostile")))
    assert "ran" in built and "not a directive" in built and "plain" in built


def test_the_same_blocks_run_on_a_normal_page():
    with pytest.raises(Exception):
        render_page(_page("t/hostile-live", "```python exec\nraise RuntimeError('ran')\n```\n"))


def test_an_explicit_text_fence_renders():
    assert "plain" in str(render_page(_page("t/text-fence", "```text\nplain\n```\n")))
