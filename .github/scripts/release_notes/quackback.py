#!/usr/bin/env python3
"""Publish a release note to quackback as a help-center article.

Schema taken from quackback's own `apps/web/openapi.json`:

    POST  /help-center/articles          required: categoryId, title, content
                                         optional: slug, description, authorId
    GET   /help-center/articles          query: categoryId, status, search, cursor, limit
    PATCH /help-center/articles/{id}     any of the create fields
    auth  Authorization: Bearer qb_...

Publishing is idempotent by slug: a release republished — because the note was
edited, or the job re-ran — updates the existing article rather than adding a
second one. A help centre with two "v2.1.9" articles is worse than one that is
briefly out of date.

Nothing here may fail a release. The note is already published on GitHub by the
time this runs; a help centre that is one article behind is a nuisance, while a
red release is an incident. Every failure path returns a reason instead of
raising.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

TIMEOUT_SECONDS = 20

# Config lives in the environment. The key must never be written down here or
# passed on a command line, where it would reach process listings and logs.
ENV_TOKEN = "QUACKBACK_API_KEY"
ENV_BASE_URL = "QUACKBACK_BASE_URL"
ENV_CATEGORY = "QUACKBACK_CATEGORY_ID"


def slug_for(tag: str) -> str:
    """A stable slug for a release, so republishing updates one article.

    `v2.1.9` -> `lex-release-v2-1-9`. Derived from the tag rather than the
    title because a human may edit the title, and an edited title must not
    silently create a second article.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")
    return f"lex-release-{cleaned}"


def title_for(tag: str) -> str:
    return f"LEX {tag}"


def description_for(body: str, *, limit: int = 200) -> str:
    """A one-line summary: the first real sentence of the note.

    Skips markdown headings, blockquote callouts and HTML markers, which are
    structure rather than summary.
    """
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "<!--", "---", "|", "```")):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\[(.+?)\]\(.*?\)", r"\1", line)
        return line[:limit].rstrip()
    return ""


def _redact(message: str, token: str) -> str:
    """Remove the API key from anything that will be printed.

    Errors echo their input. A 401 body, a urllib message, a proxy error — any
    of them can carry the Authorization header back, and the return value of
    `publish` goes straight into a workflow log. Actions masks secrets it
    injected, but this string can also reach a job summary or an artifact, and
    a key is not worth trusting one layer of masking with.
    """
    if not token:
        return message
    return message.replace(token, "qb_***")


def _request(url: str, *, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def find_article(base_url: str, slug: str, *, token: str,
                 request: Callable[..., dict] = _request) -> str | None:
    """The id of the article with this slug, or None.

    `search` is a fuzzy query, so the slug is checked exactly against each
    result. Trusting the search alone would let a near-match overwrite an
    unrelated article.
    """
    payload = request(
        f"{base_url.rstrip('/')}/help-center/articles?search={slug}&limit=50",
        token=token,
    )
    for article in payload.get("data") or []:
        if article.get("slug") == slug:
            return article.get("id")
    return None


def publish(tag: str, body: str, *, token: str, base_url: str, category_id: str,
            request: Callable[..., dict] = _request) -> str:
    """Create or update the help-center article for `tag`. Returns a summary.

    Never raises: see the module docstring.
    """
    if not token:
        return f"skipped: {ENV_TOKEN} is not set"
    if not base_url:
        return f"skipped: {ENV_BASE_URL} is not set"
    if not category_id:
        return f"skipped: {ENV_CATEGORY} is not set"

    slug = slug_for(tag)
    fields = {
        "categoryId": category_id,
        "title": title_for(tag),
        "content": body,
        "slug": slug,
        "description": description_for(body),
    }

    try:
        existing = find_article(base_url, slug, token=token, request=request)
        if existing:
            request(
                f"{base_url.rstrip('/')}/help-center/articles/{existing}",
                token=token, method="PATCH", payload=fields,
            )
            return f"updated article {existing} ({slug})"
        created = request(
            f"{base_url.rstrip('/')}/help-center/articles",
            token=token, method="POST", payload=fields,
        )
        new_id = (created.get("data") or created).get("id", "?")
        return f"created article {new_id} ({slug})"
    except urllib.error.HTTPError as exc:
        # The status is the useful half; the body may carry an API key echo or
        # a stack trace, so only its first line is surfaced.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").splitlines()[0][:200]
        except Exception:  # noqa: BLE001 - a failure to read the failure
            pass
        return _redact(
            f"failed: HTTP {exc.code} from quackback{f' — {detail}' if detail else ''}",
            token,
        )
    except Exception as exc:  # noqa: BLE001 - a release must not go red for this
        return _redact(f"failed: {type(exc).__name__}: {exc}", token)


def publish_from_env(tag: str, body: str, **kwargs) -> str:
    """`publish`, with configuration read from the environment."""
    return publish(
        tag, body,
        token=os.environ.get(ENV_TOKEN, ""),
        base_url=os.environ.get(ENV_BASE_URL, ""),
        category_id=os.environ.get(ENV_CATEGORY, ""),
        **kwargs,
    )
