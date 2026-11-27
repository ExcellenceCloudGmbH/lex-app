"""The site as Markdown, for readers working with an AI tool.

``/md/<page>.md`` is one page; ``/llms.txt`` lists every page by chapter, in the story's order; ``/llms-full.txt`` is
every page in one file. Served by the backend, from the same files the pages are drawn from.
"""

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from docs_site import nav
from docs_site.content import all_slugs, load

_FRONT = "---"


def _body(source: str) -> str:
    if source.startswith(_FRONT):
        end = source.find("\n" + _FRONT, len(_FRONT))
        if end != -1:
            return source[end + len(_FRONT) + 1:].lstrip("\n")
    return source


def page_markdown(slug: str) -> str:
    page = load(slug)
    head = f"# {page.title}\n\n" + (f"{page.description}\n\n" if page.description else "")
    if page.api:
        head += "In the API: " + ", ".join(f"`{name}`" for name in page.api) + "\n\n"
    return head + _body(page.source)


def llms_txt() -> str:
    index = load("index")
    lines = [f"# {index.title}", "", f"> {index.description}", ""]
    for section in nav.NAV:
        n = nav.number(section)
        lines += [f"## {section.title}" if n is None else f"## {n}. {section.title}", "", section.story, ""]
        for slug in nav.pages_of(section):
            page = load(slug)
            lines.append(f"- [{page.title}](/md/{slug}.md)" + (f": {page.description}" if page.description else ""))
        lines.append("")
    lines.append("Every page in one file: [/llms-full.txt](/llms-full.txt)")
    return "\n".join(lines) + "\n"


def llms_full() -> str:
    return "\n\n---\n\n".join(page_markdown(slug) for slug in ["index", *nav.ordered()])


async def _page(request: Request) -> Response:
    slug = request.path_params["slug"]
    if slug not in set(all_slugs()):
        return PlainTextResponse("No such page.", status_code=404)
    return Response(page_markdown(slug), media_type="text/markdown; charset=utf-8")


async def _llms(request: Request) -> Response:
    return PlainTextResponse(llms_txt())


async def _full(request: Request) -> Response:
    return PlainTextResponse(llms_full())


def routes() -> list[Route]:
    return [Route("/md/{slug:path}.md", _page), Route("/llms.txt", _llms), Route("/llms-full.txt", _full)]
