"""Tests for release_notes.quackback — publishing the note as a help-center article.

Schema pinned against quackback's own openapi.json:
    POST  /help-center/articles       required: categoryId, title, content
    GET   /help-center/articles       query: search, limit
    PATCH /help-center/articles/{id}
    auth  Authorization: Bearer qb_...
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import quackback  # noqa: E402

BASE = "https://support.example.com/api/v1"
BODY = "## Main changes\n\n- **New sidebar.** A full-height side navigation.\n"


def _recorder(list_result=None, create_result=None):
    """A `request` that records calls and serves canned responses."""
    calls = []

    def request(url, *, token, method="GET", payload=None):
        calls.append({"url": url, "token": token, "method": method, "payload": payload})
        if method == "GET":
            return list_result if list_result is not None else {"data": []}
        if method == "POST":
            return create_result if create_result is not None else {"data": {"id": "art_new"}}
        return {}

    return request, calls


# ── Slug and metadata ─────────────────────────────────────────────────

@pytest.mark.parametrize("tag,slug", [
    ("v2.1.9", "lex-release-v2-1-9"),
    ("v2.2.0-rc1", "lex-release-v2-2-0-rc1"),
    ("2.1.9", "lex-release-2-1-9"),
])
def test_the_slug_is_derived_from_the_tag(tag, slug):
    # From the tag, not the title: a human may edit the title, and an edited
    # title must not silently create a second article for one release.
    assert quackback.slug_for(tag) == slug


def test_the_description_skips_structure_and_takes_the_first_real_sentence():
    body = ("<!-- marker -->\n## Main changes\n\n> A callout\n\n"
            "- **New sidebar.** A full-height side navigation.\n")
    assert quackback.description_for(body) == "New sidebar. A full-height side navigation."


def test_the_description_is_empty_when_there_is_no_prose():
    assert quackback.description_for("## Heading\n\n---\n") == ""


# ── Creating ──────────────────────────────────────────────────────────

def test_a_new_release_creates_an_article_with_the_required_fields():
    request, calls = _recorder()
    out = quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                            category_id="cat_123", request=request)
    post = [c for c in calls if c["method"] == "POST"][0]
    assert post["url"] == f"{BASE}/help-center/articles"
    # The three the schema marks required.
    assert post["payload"]["categoryId"] == "cat_123"
    assert post["payload"]["title"] == "LEX v2.1.9"
    assert post["payload"]["content"] == BODY
    assert post["payload"]["slug"] == "lex-release-v2-1-9"
    assert "created" in out


def test_the_api_key_is_sent_as_a_bearer_token():
    request, calls = _recorder()
    quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                      category_id="cat_123", request=request)
    assert all(c["token"] == "qb_test" for c in calls)


# ── Updating: republishing must not duplicate ─────────────────────────

def test_republishing_updates_the_existing_article():
    request, calls = _recorder(
        list_result={"data": [{"id": "art_existing", "slug": "lex-release-v2-1-9"}]}
    )
    out = quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                            category_id="cat_123", request=request)
    assert [c["method"] for c in calls] == ["GET", "PATCH"]
    assert calls[1]["url"] == f"{BASE}/help-center/articles/art_existing"
    assert "updated" in out
    # A help centre with two "v2.1.9" articles is worse than one briefly stale.
    assert not [c for c in calls if c["method"] == "POST"]


def test_a_fuzzy_search_hit_that_is_not_an_exact_slug_is_ignored():
    # `search` is fuzzy. Trusting it alone would let a near-match overwrite an
    # unrelated article.
    request, calls = _recorder(
        list_result={"data": [{"id": "art_other", "slug": "lex-release-v2-1-90"}]}
    )
    quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                      category_id="cat_123", request=request)
    assert [c["method"] for c in calls] == ["GET", "POST"]


# ── Never failing a release ───────────────────────────────────────────

@pytest.mark.parametrize("missing,reason", [
    ({"token": ""}, "QUACKBACK_API_KEY"),
    ({"base_url": ""}, "QUACKBACK_BASE_URL"),
    ({"category_id": ""}, "QUACKBACK_CATEGORY_ID"),
])
def test_missing_configuration_skips_rather_than_fails(missing, reason):
    kwargs = {"token": "qb_test", "base_url": BASE, "category_id": "cat_123"}
    kwargs.update(missing)
    request, calls = _recorder()
    out = quackback.publish("v2.1.9", BODY, request=request, **kwargs)
    assert out.startswith("skipped")
    assert reason in out
    assert calls == [], "nothing should be called without full configuration"


def test_an_http_error_is_reported_not_raised():
    def request(url, *, token, method="GET", payload=None):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    out = quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                            category_id="cat_123", request=request)
    # The note is already published on GitHub by now. A stale help centre is a
    # nuisance; a red release is an incident.
    assert out.startswith("failed: HTTP 403")


def test_any_other_error_is_reported_not_raised():
    def request(url, **kwargs):
        raise TimeoutError("the read timed out")

    out = quackback.publish("v2.1.9", BODY, token="qb_test", base_url=BASE,
                            category_id="cat_123", request=request)
    assert out.startswith("failed: TimeoutError")


def test_the_api_key_never_appears_in_a_returned_message():
    def request(url, **kwargs):
        raise RuntimeError("boom while sending qb_supersecretkey")

    out = quackback.publish("v2.1.9", BODY, token="qb_supersecretkey", base_url=BASE,
                            category_id="cat_123", request=request)
    # The message is printed into a workflow log. It must never carry the key,
    # even when an underlying error echoes it back.
    assert "qb_supersecretkey" not in out


def test_the_api_key_never_appears_in_an_http_error_message():
    class _Body:
        @staticmethod
        def read():
            return b"401 unauthorized for token qb_supersecretkey"

    def request(url, **kwargs):
        err = urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        err.read = _Body.read
        raise err

    out = quackback.publish("v2.1.9", BODY, token="qb_supersecretkey", base_url=BASE,
                            category_id="cat_123", request=request)
    assert "qb_supersecretkey" not in out
    assert "HTTP 401" in out
