"""The site's navigation: sections, their groups, and the pages in reading order.

The landing cards, the sidebar, the breadcrumbs and previous/next all come from here. A page in
content/ that is not listed here, or a listed page with no file, fails the build (problems()).
"""

from dataclasses import dataclass

from docs_site import reference


@dataclass(frozen=True)
class Group:
    title: str
    pages: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    blurb: str
    groups: tuple[Group, ...]


NAV: tuple[Section, ...] = (
    Section("get-started", "Get started", "Your first dashboard on lex-app, from nothing to a page that works.", (
        Group("Get started", ("get-started/first-dashboard", "get-started/lex-reflex-add",
                              "get-started/running-locally")),
    )),
    Section("pages", "Pages", "Where a page lives, and the page kinds that decide how it looks.", (
        Group("Declaring pages", ("pages/ui-page",)),
        Group("Page kinds", ("pages/record", "pages/list", "pages/forms")),
        Group("Parts", ("pages/tables", "pages/buttons-and-links")),
    )),
    Section("state-and-actions", "State & actions", "Plain Python handlers; lex-app does the plumbing.", (
        Group("State", ("state-and-actions/state",)),
        Group("Handlers", ("state-and-actions/action", "state-and-actions/job", "state-and-actions/load",
                              "state-and-actions/refresh")),
    )),
    Section("data-and-permissions", "Data & permissions", "Reading and writing records as the signed-in user.", (
        Group("Data", ("data-and-permissions/api",)),
        Group("Permissions", ("data-and-permissions/can", "data-and-permissions/grants")),
    )),
    Section("look-and-chrome", "Look & chrome", "lex-app's design and frame, and dashboards inside lex-app.", (
        Group("Look & chrome", ("look-and-chrome/look", "look-and-chrome/embedding",
                                  "look-and-chrome/raw-reflex")),
    )),
    Section("ship-it", "Ship it", "Sign-in, checks and deployment.", (
        Group("Ship it", ("ship-it/sign-in", "ship-it/testing", "ship-it/check", "ship-it/deploying")),
    )),
    Section("reference", "API reference", "Every name lex.ui exports, with its signature.", (
        Group("API reference", tuple(f"reference/{g}" for g in reference.GROUPS)),
    )),
)


def ordered() -> list[str]:
    return [page for section in NAV for group in section.groups for page in group.pages]


def section_of(slug: str) -> Section:
    for section in NAV:
        if any(slug in group.pages for group in section.groups):
            return section
    raise KeyError(slug)


def neighbours(slug: str) -> tuple[str | None, str | None]:
    order = ordered()
    i = order.index(slug)
    return (order[i - 1] if i > 0 else None, order[i + 1] if i + 1 < len(order) else None)


def problems() -> list[str]:
    from docs_site import content

    listed = ordered()
    found = {slug for slug in content.all_slugs() if slug != "index" and not slug.startswith("reference/")}
    out = [f"listed twice: {s}" for s in sorted({s for s in listed if listed.count(s) > 1})]
    out += [f"listed in nav.py, no file: content/{s}.md" for s in listed
             if s not in found and not s.startswith("reference/")]
    out += [f"in content/, not in nav.py: {s}" for s in sorted(found - set(listed))]
    out += [f"{s} is not in its section's folder" for sec in NAV for g in sec.groups for s in g.pages
            if not s.startswith(sec.slug + "/")]
    return out
