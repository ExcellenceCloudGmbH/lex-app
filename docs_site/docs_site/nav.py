"""The site's navigation: seven chapters that tell one story, then the API reference.

The story is a fund team's dashboard, built up chapter by chapter; each chapter is one list of
pages in reading order. The landing journey, the sidebar, the chapter eyebrow, the breadcrumbs and
previous/next all come from here. A page in content/ that is not listed here, or a listed page with
no file, fails the build (problems()).
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
    #: the chapter's part of the story, in one sentence: the landing card, the first page's intro
    story: str
    #: one group: a chapter is one list of pages, and the sidebar draws no group label
    groups: tuple[Group, ...]


def _chapter(slug: str, title: str, story: str, *pages: str) -> Section:
    return Section(slug, title, story, (Group(title, tuple(f"{slug}/{p}" for p in pages)),))


NAV: tuple[Section, ...] = (
    _chapter("start-here", "Start here",
             "Build your first dashboard: a page that lists a fund team's open funds and lets them close one.",
             "first-dashboard", "run-locally", "from-a-model"),
    _chapter("show-your-data", "Show your data",
             "Give the team pages to look things up: one fund, a list of funds, tables they can filter and export.",
             "addresses", "one-record", "list", "tables", "on-open", "records"),
    _chapter("let-people-act", "Let people act",
             "Let the team change things: buttons, clicks, and forms that create and update records.",
             "what-code-knows", "buttons-and-links", "clicks", "forms", "steps-and-confirmations",
             "keep-tables-current"),
    _chapter("long-running-work", "Long-running work",
             "Some work takes minutes: show its progress, let people stop it, and keep the result.",
             "jobs", "tools", "calculations", "private-results"),
    _chapter("who-can-do-what", "Who can do what",
             "Everyone signs in with lex-app; decide what each of them may see and do.",
             "sign-in", "permissions", "grants"),
    _chapter("make-it-your-app", "Make it your app",
             "Give the dashboards your app's navigation and header, and put them inside lex-app.",
             "look", "navigation", "blocked-pages", "inside-lex-app", "your-own-reflex"),
    _chapter("test-and-ship", "Test and ship",
             "Test what you built, check it, and put it in production next to lex-app.",
             "testing", "check", "deploy"),
    Section("reference", "API reference", "Every name lex.ui exports, with its signature.", (
        Group("API reference", tuple(f"reference/{g}" for g in reference.GROUPS)),
    )),
)

REFERENCE = "reference"
#: the story's chapters, in order (the API reference is not one)
CHAPTERS: tuple[Section, ...] = tuple(s for s in NAV if s.slug != REFERENCE)


def pages_of(section: Section) -> tuple[str, ...]:
    return tuple(page for group in section.groups for page in group.pages)


def first(section: Section) -> str:
    return pages_of(section)[0]


def number(section: Section) -> int | None:
    """The chapter's number, 1 to 7; None for the API reference."""
    return CHAPTERS.index(section) + 1 if section in CHAPTERS else None


def ordered() -> list[str]:
    return [page for section in NAV for page in pages_of(section)]


def section_of(slug: str) -> Section:
    for section in NAV:
        if slug in pages_of(section):
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
    out += [f"{s} is not in its section's folder" for sec in NAV for s in pages_of(sec)
            if not s.startswith(sec.slug + "/")]
    out += [f"{sec.slug} has {len(sec.groups)} groups: a chapter is one list of pages"
            for sec in NAV if len(sec.groups) != 1]
    return out
