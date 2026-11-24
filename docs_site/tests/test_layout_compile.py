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
    opened = re.findall(r'open:true\},jsx\("summary",\{className:"docs-nav-summary"\}.{0,400}?'
                        r'className:"docs-nav-title"\},"([^"]+)"', out)
    assert opened == ["Pages"], opened
    assert out.count("open:false") == len(nav.NAV) - 1
    # each section's button carries its icon and its page count; the chevron turns when open
    assert out.count('className:"docs-nav-icon"') == len(nav.NAV)
    assert 'className:"docs-nav-count"},"7"' in out
    from docs_site.layout import _STYLE

    assert _STYLE["& .docs-nav-section[open] > .docs-nav-summary .docs-nav-chevron"]["transform"] == "rotate(90deg)"
    current_style = _STYLE["& .docs-nav-link[aria-current='page']"]
    assert current_style["border_left_color"] == "var(--docs-accent)" and current_style["background"] == "var(--docs-pill-bg)"


def test_a_page_has_breadcrumbs_actions_and_previous_next_cards(compiled):
    out = _page("pages/record")
    assert 'rfdsI18n.t("breadcrumbs.label", "Breadcrumb"' in out
    assert 'className:"rfds-breadcrumbs-current"},"ui.record"' in out
    assert "Copy page" in out and '"Markdown"' in out and "/md/pages/record.md" in out
    # the two page actions are one joined group, as the design draws them
    assert '"aria-label":"Page actions"' in out and 'role:"group"' in out
    assert '"aria-label":"View this page as Markdown"' in out
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


def test_the_header_has_the_section_tabs_and_the_current_one_marked(compiled):
    """Guide / Components / API reference: Components is the Pages section (lex.ui's page kinds and
    parts), API reference the reference section, Guide the landing; the tab of the page's section is marked."""
    import re

    from docs_site import layout

    def tabs(slug: str) -> list[tuple[str, str, bool]]:
        out = str(layout._header(slug))
        return [(label, href, bool(cur)) for cur, href, label in re.findall(
            r'RadixThemesLink,\{("aria-current":"true",)?asChild:true,className:"docs-tab".{0,200}?to:"([^"]+)"\},"([^"]+)"',
            out)]

    reference = next(sec for sec in nav.NAV if sec.slug == "reference").groups[0].pages[0]
    assert tabs("pages/record") == [("Guide", "/", False), ("Components", "/pages/ui-page", True),
                                    ("API reference", "/" + reference, False)]
    assert [cur for _, _, cur in tabs("get-started/first-dashboard")] == [True, False, False]
    assert [cur for _, _, cur in tabs(nav.ordered()[-1])] == [False, False, True]
    assert [cur for _, _, cur in tabs("index")] == [True, False, False]
    out = str(layout._header("index"))
    assert '"aria-label":"Sections"' in out and '"dashboards"' in out and "docs-mark" in out
    # the search box keeps its name and shows its shortcut; the shortcut is registered on every page
    assert '"aria-label":"Search"' in out and '"Ctrl K"' in out and 'id:"docs-search-input"' in out
    assert "palette.open" in _page("pages/record") or "mod+K" in _page("pages/record")


def test_the_header_is_page_coloured_through_the_sites_tokens():
    """RFDS's chrome band and lex-app's header paint take the site's tokens, so the header is light
    (translucent, blurred) in light mode and dark in dark mode; it is 60px high."""
    from docs_site.layout import _STYLE

    assert _STYLE["--lex-header-bg"] == "var(--docs-header)"
    assert _STYLE["--rfds-color-chrome-bg"] == "var(--docs-bg)"
    assert _STYLE["--rfds-color-chrome-fg"] == "var(--docs-ink)"
    assert "var(--docs-border)" in _STYLE["--lex-header-shadow"]
    assert _STYLE["--rfds-size-shell-header"] == "60px"
    assert "blur(" in _STYLE["& .rfds-header"]["backdrop_filter"]


def test_every_colour_token_has_a_light_and_a_dark_value_and_follows_the_colour_mode():
    """One token set per mode, in theme.py; the frame carries the light set and, under Reflex's
    ``.dark`` class, the dark one. Every var(--docs-x) the frame uses is a token that exists."""
    import re

    from docs_site import theme
    from docs_site.layout import _STYLE

    assert set(theme.LIGHT) == set(theme.DARK)
    assert _STYLE["--docs-bg"] == theme.LIGHT["bg"]
    assert _STYLE[".dark &"]["--docs-bg"] == theme.DARK["bg"]
    used = set(re.findall(r"var\(--docs-([a-z-]+)\)", repr(_STYLE) + repr(theme.CODE_THEME)))
    assert used and used <= set(theme.LIGHT) | {"mono", "sidebar-w", "toc-w"}, used - set(theme.LIGHT)


def test_callout_tones_meet_contrast_in_both_modes():
    """Each callout's text on its background is at least 4.5:1, light and dark (WCAG 1.4.3)."""
    from docs_site import theme

    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for palette in (theme.LIGHT, theme.DARK):
        for tone in theme.TONES:
            assert ratio(palette[f"{tone}-ink"], palette[f"{tone}-bg"]) >= 4.5, tone
        assert ratio(palette["muted"], palette["bg"]) >= 4.5
        assert ratio(palette["link"], palette["bg"]) >= 4.5
        assert ratio(palette["pill-ink"], palette["pill-bg"]) >= 4.5


def test_colours_are_written_only_in_the_token_module():
    """Every colour value lives in docs_site/theme.py (one set per mode); the rest of the site's
    Python says var(--docs-...) or uses Radix/RFDS tokens, so both colour modes read well."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "docs_site"
    files = sorted(root.glob("*.py")) + sorted((root / "render").glob("*.py")) + sorted((root / "sandbox").glob("*.py"))
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")
    found, in_theme = [], 0
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"(^|\s)#(?![0-9a-fA-F]{3,8}\b).*$", "", line)    # drop comments
            if colour.search(code):
                if path.name == "theme.py":
                    in_theme += 1
                else:
                    found.append(f"{path.name}:{n}: {line.strip()}")
    assert files and not found, found
    assert in_theme > 20        # the palettes are there


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
