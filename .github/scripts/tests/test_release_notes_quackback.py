"""Tests for release_notes.quackback — the Hub publish interface.

No network. The transport is injected, which is also how the draft contract is
asserted: whether an entry is a draft is decided by the ABSENCE of a key in the
payload, so the payload itself is the only place that can be checked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import quackback  # noqa: E402

BODY = "## Bug fixes\n\n- **A thing.** It works now.\n"


def _recorder(result=None):
    """A transport that records the call and returns a canned response."""
    calls = []

    def post(url, payload, *, token, timeout=30):
        calls.append({"url": url, "payload": payload, "token": token})
        return result if result is not None else {"data": {"id": "changelog_1"}}

    return post, calls


def test_a_note_is_a_draft_unless_a_publish_time_is_given():
    # quackback reads an absent `publishedAt` as "draft". Sending an explicit
    # null would be a different request, so the key must not appear at all.
    post, calls = _recorder()
    quackback.publish("v2.2.0", BODY, token="qb_test", post=post)

    assert "publishedAt" not in calls[0]["payload"]
    assert calls[0]["payload"]["title"] == "v2.2.0"
    assert calls[0]["payload"]["content"] == BODY
    assert calls[0]["url"].endswith("/changelog")
    assert calls[0]["token"] == "qb_test"


def test_a_publish_time_makes_the_entry_live():
    post, calls = _recorder()
    quackback.publish(
        "v2.2.0", BODY, token="qb_test", published_at="2026-09-08T05:51:43Z", post=post
    )
    assert calls[0]["payload"]["publishedAt"] == "2026-09-08T05:51:43Z"


def test_optional_fields_are_omitted_rather_than_sent_as_null():
    post, calls = _recorder()
    quackback.publish("v2.2.0", BODY, token="qb_test", post=post)
    assert set(calls[0]["payload"]) == {"title", "content"}


def test_the_entry_is_unwrapped_from_the_data_envelope():
    # The API answers {"data": {...}}; callers want the entry, and above all
    # its id — a draft whose id was swallowed is a draft nobody can find.
    post, _ = _recorder({"data": {"id": "changelog_abc", "publishedAt": None}})
    entry = quackback.publish("v2.2.0", BODY, token="qb_test", post=post)
    assert entry["id"] == "changelog_abc"


def test_a_missing_token_fails_before_any_request():
    post, calls = _recorder()
    with pytest.raises(quackback.QuackbackError, match="QUACKBACK_API_TOKEN"):
        quackback.publish("v2.2.0", BODY, token="", post=post)
    assert calls == []


def test_an_empty_note_is_refused():
    # Publishing a blank release note is worse than publishing nothing: it
    # looks like the release had no changes worth describing.
    post, calls = _recorder()
    with pytest.raises(quackback.QuackbackError, match="empty release note"):
        quackback.publish("v2.2.0", "   \n", token="qb_test", post=post)
    assert calls == []


def test_a_help_centre_article_requires_a_category():
    post, calls = _recorder()
    with pytest.raises(quackback.QuackbackError, match="category"):
        quackback.publish_help_center_article(
            "v2.2.0", BODY, token="qb_test", category_id="", post=post
        )
    assert calls == []


def test_a_help_centre_article_posts_to_the_articles_resource():
    post, calls = _recorder({"data": {"id": "article_1"}})
    entry = quackback.publish_help_center_article(
        "v2.2.0", BODY, token="qb_test",
        category_id="category_01kvwdt8exe069w36qdfzr545q", post=post,
    )
    assert calls[0]["url"].endswith("/help-center/articles")
    assert calls[0]["payload"]["categoryId"] == "category_01kvwdt8exe069w36qdfzr545q"
    assert entry["id"] == "article_1"


def test_requests_carry_an_explicit_user_agent():
    """The Hub 403s urllib's default User-Agent.

    403 is also what a bad token returns, so dropping this header produces a
    failure that reads as "the credential is wrong" while the credential is
    fine. Measured against the live instance: no UA is 403, any named UA is
    200.
    """
    headers = quackback._headers("qb_test")
    assert headers["User-Agent"] == quackback.USER_AGENT
    assert "urllib" not in headers["User-Agent"].lower()
    assert headers["Authorization"] == "Bearer qb_test"
    assert headers["Content-Type"] == "application/json"
