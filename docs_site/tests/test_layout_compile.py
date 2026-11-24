"""The docs app compiles, with the routes the site promises."""


def test_the_packages_rfds_vendor_js_imports_are_installed():
    """A lex.ui table's search box is RFDS's clearable text input, which imports ``rfdsClearInput``
    from ``rfds_form.js``; that file imports ``react-number-format`` at module level, and only RFDS's
    form and number inputs declare the package. Without it the dev server fails to resolve the import."""
    from rfds._vendor._npm import spec

    from rxconfig import config

    assert spec("react-number-format") in config.frontend_packages


def test_the_app_compiles_with_a_home_page(compiled):
    assert "index" in compiled.pages


from docs_site import nav  # noqa: E402


def test_every_page_in_nav_is_a_route(compiled):
    routes = {r.lstrip("/") for r in compiled.pages}
    for slug in nav.ordered():
        assert slug in routes, slug


def _page(slug: str) -> str:
    from docs_site import layout

    return str(layout._content_page(slug)())


def test_sidebar_is_a_drawer_on_small_screens_and_a_column_from_1024px():
    """Below 1024 px the sidebar is hidden off-screen until RFDS's header menu button marks the shell
    open; from 1024 px it is a sticky column of the grid. "On this page" shows from 1280 px only."""
    from docs_site.layout import SIDEBAR_FROM, TOC_FROM, _STYLE

    assert _STYLE["& .docs-sidebar"]["visibility"] == "hidden"
    assert _STYLE["&[data-rfds-nav-open='true'] .docs-sidebar"]["visibility"] == "visible"
    wide = _STYLE[f"@media (min-width: {SIDEBAR_FROM})"]
    assert wide["& .docs-sidebar"]["position"] == "sticky" and wide["& .docs-sidebar"]["visibility"] == "visible"
    assert wide["& .rfds-header-menu-slot"]["display"] == "none"
    assert _STYLE["& .docs-toc"]["display"] == "none"
    assert _STYLE[f"@media (min-width: {TOC_FROM})"]["& .docs-toc"]["display"] == "block"
    assert (SIDEBAR_FROM, TOC_FROM) == ("1024px", "1280px")


def test_the_frame_has_its_landmarks_and_the_drawer_wiring(compiled):
    out = _page(nav.ordered()[0])
    assert '"aria-label":"Docs"' in out and '"aria-label":"On this page"' in out
    assert '"aria-label":"Search"' in out
    # the menu button (rfds.header) opens [data-rfds-shell=sidebar] inside [data-rfds-shell=root]
    assert '"data-rfds-shell":"root"' in out and '"data-rfds-has-nav":"true"' in out
    assert '"data-rfds-shell":"sidebar"' in out and 'id:"rfds-side-nav"' in out
    assert "rfds-header-menu" in out and "lex-app" in out


def test_the_current_page_is_highlighted_and_only_its_section_open(compiled):
    import re

    slug = "pages/record"
    out = _page(slug)
    current = re.findall(r'"aria-current":"page",asChild:true,className:"docs-nav-link".{0,400}?to:"([^"]+)"', out)
    assert current == ["/pages/record"], current
    opened = re.findall(r'open:true\},jsx\("summary",\{className:"docs-nav-summary"\},jsx\("span",\{\},"([^"]+)"', out)
    assert opened == ["Pages"], opened
    assert out.count("open:false") == len(nav.NAV) - 1


def test_a_page_has_breadcrumbs_actions_and_previous_next_cards(compiled):
    out = _page("pages/record")
    assert 'rfdsI18n.t("breadcrumbs.label", "Breadcrumb"' in out
    assert 'className:"rfds-breadcrumbs-current"},"ui.record"' in out
    assert "Copy page" in out and "View Markdown" in out and "/md/pages/record.md" in out
    assert '"Previous"' in out and '"Next"' in out and "docs-pager-card" in out
    # the first page has no "Previous" card, only an empty cell keeping "Next" on the right
    first = _page(nav.ordered()[0])
    assert '"Previous"' not in first and "docs-pager-empty" in first


def test_the_landing_page_has_a_hero_and_a_card_per_section(compiled):
    from docs_site import layout

    out = str(layout._landing())
    assert out.count("docs-section-card\"") + out.count('className:"docs-section-card"') >= len(nav.NAV)
    for section in nav.NAV:
        assert section.title.replace("&", "&") in out
    assert "docs-hero" in out and "docs-article-wide" in out
