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


def test_sidebar_and_contents_hide_on_small_screens(compiled):
    from docs_site import layout

    out = str(layout._content_page(nav.ordered()[0])())
    assert '"aria-label":"Docs"' in out and '"aria-label":"On this page"' in out
    # each landmark is hidden from 0px and only shown at its breakpoint (md: 1024px, lg: 1280px)
    assert out.count('@media screen and (min-width: 0px)"] : ({ ["display"] : "none" })') == 2
    assert '(min-width: 1024px)"] : ({ ["display"] : "block" })' in out
    assert '(min-width: 1280px)"] : ({ ["display"] : "block" })' in out
