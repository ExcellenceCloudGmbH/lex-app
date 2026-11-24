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


def test_the_header_has_a_colour_mode_toggle(compiled):
    """A sun/moon button switches Reflex's colour mode; its label says where it goes."""
    from docs_site import layout

    out = str(layout._header())
    assert '"aria-label":"Switch to dark mode"' in out and '"aria-label":"Switch to light mode"' in out
    assert "toggleColorMode" in out and "resolvedColorMode" in out


def test_the_header_is_light_page_coloured_through_tokens():
    """RFDS's chrome band and lex-app's header paint take the page's colour and grays, so the
    header is light in light mode and dark in dark mode."""
    from docs_site.layout import _STYLE

    assert "var(--color-background)" in _STYLE["--lex-header-bg"]
    assert _STYLE["--rfds-color-chrome-bg"] == "var(--color-background)"
    assert _STYLE["--rfds-color-chrome-fg"] == "var(--gray-12)"
    assert "var(--gray-a5)" in _STYLE["--lex-header-shadow"]


def test_no_hardcoded_colours_in_the_sites_python():
    """Colours are Radix/RFDS tokens only, so both colour modes read well."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "docs_site"
    files = sorted(root.glob("*.py")) + sorted((root / "render").glob("*.py"))
    found = []
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"(^|\s)#(?![0-9a-fA-F]{3,6}\b).*$", "", line)    # drop comments
            if re.search(r"#[0-9a-fA-F]{3,6}\b", code) or "rgb(" in code:
                found.append(f"{path.name}:{n}: {line.strip()}")
    assert files and not found, found


def test_the_app_module_sets_django_up_as_lex_apps_own_does():
    """A guarded @ui.action enters lex-app's acting-user context, which imports Django models. In a
    test, lex-app's pytest plugin has already set Django up; `reflex run` has not. So the app module
    must, before anything imports a model, as lex/reflex_app.py does. Checked in a fresh process
    without the plugin, where the bug lived."""
    import subprocess
    import sys
    from pathlib import Path

    site = Path(__file__).resolve().parents[1]
    probe = ("import docs_site.docs_site; from django.apps import apps; "
             "from lex.lex_app.reflex.ui.guard import acting_user; "
             "ctx = acting_user(type('U', (), {'username': 'demo'})()); ctx.__enter__(); ctx.__exit__(None, None, None); "
             "print('ready', apps.ready)")
    out = subprocess.run([sys.executable, "-c", probe], cwd=site, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ready True" in out.stdout
