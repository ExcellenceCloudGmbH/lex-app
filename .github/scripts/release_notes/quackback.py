#!/usr/bin/env python3
"""Publish a release note to quackback — the Hub at hub.excellence-cloud.de.

The schema here was read off the running instance's own spec
(`GET /api/v1/openapi/json`), not guessed. Two resources can hold a release
note, and which one to use is a product decision rather than a technical one:

  * ``POST /changelog`` — purpose-built for release notes, and the only one of
    the two with a draft state. **Omitting ``published_at`` stores the entry
    unpublished**, which is the default here: a release note reaches customers
    when a human says so, not as a side effect of a workflow succeeding.
  * ``POST /help-center/articles`` — a help-centre document, which is what was
    originally asked for. It requires a category and has no draft state, so an
    article is live the moment it is created. That asymmetry is the reason the
    changelog is the default.

The note passed here is the human-approved release body, NOT the drafted one.
Read it from the release at publish time; the drafted text may have been
rewritten during review, and the whole point of reviewing it is that the
reviewed version is what ships.

The token is a quackback API key (``qb_…``). It is read from the environment by
the caller and never stored in this repository.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

# The same host the app already embeds for the feedback widget
# (lex/lex_app/streamlit/quackback.py), though the two surfaces are unrelated.
BASE_URL = "https://hub.excellence-cloud.de/api/v1"

# The Hub answers 403 to urllib's default `Python-urllib/3.x` User-Agent, and
# 403 is the same status a bad token produces -- so the symptom points straight
# at the credential while the credential is fine. Measured against the live
# instance: no UA 403s, any named UA is 200. Hence an explicit one.
USER_AGENT = "lex-app-release-notes/1.0"

CHANGELOG_PATH = "/changelog"
ARTICLES_PATH = "/help-center/articles"


class QuackbackError(RuntimeError):
    """A quackback request failed. Carries the status and the response body.

    The body matters: quackback returns field-level validation errors, and a
    bare "HTTP 400" turns a five-second fix into a guessing game.
    """


def _headers(token: str) -> dict:
    """Request headers. Separate so the User-Agent can be asserted in a test."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _post(url: str, payload: dict, *, token: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise QuackbackError(f"{exc.code} from {url}: {detail}") from exc


def publish(
    tag: str,
    body: str,
    *,
    token: str,
    published_at: str | None = None,
    product_name: str | None = None,
    category_name: str | None = None,
    base_url: str = BASE_URL,
    post: Callable[..., dict] = _post,
) -> dict:
    """Create the changelog entry for `tag`, as a draft unless told otherwise.

    `published_at` is an ISO-8601 instant. Leaving it None is not an oversight
    — it is how quackback stores a draft, and a draft is the safe default for
    something customer-visible. Pass the release's own published time to make
    the entry live in the same motion.

    Returns the created entry. The caller should log its id: a draft nobody
    knows about is the same as no draft.
    """
    if not token:
        raise QuackbackError("no quackback API token — set QUACKBACK_API_TOKEN")
    if not body.strip():
        raise QuackbackError(f"refusing to publish an empty release note for {tag}")

    payload: dict = {"title": tag, "content": body}
    # Sent only when set. quackback treats an absent `publishedAt` as "draft",
    # so passing an explicit None would be a different request.
    if published_at is not None:
        payload["publishedAt"] = published_at
    if product_name is not None:
        payload["productName"] = product_name
    if category_name is not None:
        payload["categoryName"] = category_name

    result = post(f"{base_url}{CHANGELOG_PATH}", payload, token=token)
    return result.get("data", result)


def publish_help_center_article(
    tag: str,
    body: str,
    *,
    token: str,
    category_id: str,
    description: str | None = None,
    slug: str | None = None,
    base_url: str = BASE_URL,
    post: Callable[..., dict] = _post,
) -> dict:
    """Create a help-centre article for `tag`.

    There is no draft state on this resource, so this call makes the note
    visible immediately. Use it only when that is what was intended.
    `category_id` is a quackback TypeID (`category_…`), from
    ``GET /help-center/categories``.
    """
    if not token:
        raise QuackbackError("no quackback API token — set QUACKBACK_API_TOKEN")
    if not category_id:
        raise QuackbackError("help-centre articles require a category id")

    payload: dict = {"categoryId": category_id, "title": tag, "content": body}
    if description is not None:
        payload["description"] = description
    if slug is not None:
        payload["slug"] = slug

    result = post(f"{base_url}{ARTICLES_PATH}", payload, token=token)
    return result.get("data", result)
