"""Tests for release_notes.notes — prompt assembly, validation, fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import notes  # noqa: E402

GOOD = """\
## Main changes

- **New sidebar.** A full-height side navigation.

## Bug fixes

- **Timezone bug.** Times now display correctly.

**Upgrade note:** run database migrations on upgrade.
"""


def _digest(changes=None):
    return {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": changes if changes is not None else [
        {"sha": "1111111", "component": "backend", "type": "fix", "scope": "tz",
         "breaking": False, "subject": "correct the offset", "pr_number": 661},
    ]}


def test_prompt_contains_the_digest_and_the_style_exemplar():
    prompt = notes.build_prompt(_digest(), exemplar="EXEMPLAR-TEXT")
    assert "correct the offset" in prompt
    assert "EXEMPLAR-TEXT" in prompt
    assert "v2.1.7" in prompt


def test_validation_accepts_output_with_one_required_heading():
    # The spec asks for empty sections to be omitted, so one heading is enough.
    assert notes.validate(GOOD) is None


def test_validation_rejects_output_with_no_required_heading():
    assert notes.validate("Some prose with no headings at all.") is not None


def test_validation_rejects_a_heading_with_nothing_under_it():
    bad = "## Main changes\n\n## Bug fixes\n\n- **A fix.** Text.\n"
    assert notes.validate(bad) is not None


def test_fallback_carries_the_digest_and_the_failure_marker():
    out = notes.fallback(_digest(), reason="model call timed out")
    assert notes.FAILURE_MARKER in out
    assert "model call timed out" in out
    assert "correct the offset" in out


def test_empty_digest_short_circuits_without_calling_the_model():
    called = []

    def model(prompt: str) -> str:
        called.append(prompt)
        return GOOD

    out = notes.draft(_digest(changes=[]), exemplar="X", model=model)
    assert called == []
    assert "no user-facing changes" in out.lower()


def test_draft_returns_model_output_when_valid():
    out = notes.draft(_digest(), exemplar="X", model=lambda p: GOOD)
    assert "New sidebar" in out
    assert notes.FAILURE_MARKER not in out


def test_draft_falls_back_when_the_model_raises():
    def boom(prompt: str) -> str:
        raise RuntimeError("502 from the inference endpoint")

    out = notes.draft(_digest(), exemplar="X", model=boom)
    assert notes.FAILURE_MARKER in out
    assert "502" in out


def test_draft_falls_back_when_the_model_returns_junk():
    out = notes.draft(_digest(), exemplar="X", model=lambda p: "I cannot help with that.")
    assert notes.FAILURE_MARKER in out


def test_transport_posts_to_the_models_endpoint_and_returns_the_content():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured["url"] = url
        captured["headers"] = headers
        captured["json_body"] = json_body
        return {"choices": [{"message": {"content": "RESULT"}}]}

    got = notes.github_models("PROMPT", api_key="tok123", post=fake_post)
    assert got == "RESULT"
    assert captured["url"] == notes.MODELS_ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer tok123"
    assert captured["json_body"]["model"] == notes.MODEL
    assert captured["json_body"]["messages"][0]["content"] == "PROMPT"


def test_transport_raises_on_a_malformed_response():
    with pytest.raises(ValueError, match="unexpected response"):
        notes.github_models("P", api_key="t", post=lambda u, *, headers, json_body: {"oops": 1})


def test_anthropic_transport_posts_and_returns_the_text():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured.update(url=url, headers=headers, json_body=json_body)
        return {"content": [{"type": "text", "text": "RESULT"}]}

    got = notes.anthropic_messages("PROMPT", api_key="sk-test", post=fake_post)
    assert got == "RESULT"
    assert captured["url"] == notes.ANTHROPIC_ENDPOINT
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == notes.ANTHROPIC_VERSION
    assert captured["json_body"]["model"] == notes.ANTHROPIC_MODEL
    assert captured["json_body"]["messages"][0]["content"] == "PROMPT"


def test_anthropic_transport_raises_on_a_malformed_response():
    import pytest

    with pytest.raises(ValueError, match="unexpected response"):
        notes.anthropic_messages("P", api_key="k", post=lambda u, *, headers, json_body: {"oops": 1})


def test_anthropic_transport_joins_multiple_text_blocks():
    payload = {"content": [{"type": "text", "text": "one\n"}, {"type": "text", "text": "two"}]}
    got = notes.anthropic_messages("P", api_key="k", post=lambda u, *, headers, json_body: payload)
    assert got == "one\ntwo"


# ── Gemini ────────────────────────────────────────────────────────────

def test_gemini_posts_and_returns_the_text():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured.update(url=url, headers=headers, json_body=json_body)
        return {"candidates": [{"content": {"parts": [{"text": "RESULT"}]}}]}

    got = notes.gemini_generate("PROMPT", api_key="g-key", post=fake_post)
    assert got == "RESULT"
    # The key travels in a header, never in the query string.
    assert captured["headers"]["x-goog-api-key"] == "g-key"
    assert "g-key" not in captured["url"]
    assert captured["json_body"]["contents"][0]["parts"][0]["text"] == "PROMPT"


def test_gemini_joins_multiple_parts():
    payload = {"candidates": [{"content": {"parts": [{"text": "one\n"}, {"text": "two"}]}}]}
    got = notes.gemini_generate("P", api_key="k", post=lambda u, *, headers, json_body: payload)
    assert got == "one\ntwo"


def test_gemini_raises_on_a_malformed_response():
    import pytest

    with pytest.raises(ValueError, match="unexpected response"):
        notes.gemini_generate("P", api_key="k", post=lambda u, *, headers, json_body: {"nope": 1})


# ── OpenAI ────────────────────────────────────────────────────────────

def test_openai_posts_and_returns_the_text():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured.update(url=url, headers=headers, json_body=json_body)
        return {"choices": [{"message": {"content": "RESULT"}}]}

    got = notes.openai_chat("PROMPT", api_key="sk-o", post=fake_post)
    assert got == "RESULT"
    assert captured["url"] == notes.OPENAI_ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer sk-o"


def test_openai_raises_on_a_malformed_response():
    import pytest

    with pytest.raises(ValueError, match="unexpected response"):
        notes.openai_chat("P", api_key="k", post=lambda u, *, headers, json_body: {"nope": 1})


# ── Provider selection ────────────────────────────────────────────────

def test_every_advertised_provider_is_in_the_registry():
    assert set(notes.PROVIDERS) == {"anthropic", "gemini", "openai", "github-models"}
    for name in notes.PROVIDER_ORDER:
        assert name in notes.PROVIDERS


def test_explicit_choice_wins_even_when_others_have_keys():
    env = {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}
    chosen, _ = notes.resolve_provider("gemini", env)
    assert chosen == "gemini"


def test_explicit_choice_is_case_and_alias_tolerant():
    env = {"GEMINI_API_KEY": "g"}
    for spelling in ("Gemini", "  gemini  ", "google"):
        chosen, _ = notes.resolve_provider(spelling, env)
        assert chosen == "gemini"
    env = {"OPENAI_API_KEY": "o"}
    for spelling in ("gpt", "OpenAI"):
        chosen, _ = notes.resolve_provider(spelling, env)
        assert chosen == "openai"


def test_explicit_choice_without_its_key_is_an_error():
    import pytest

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        notes.resolve_provider("gemini", {"ANTHROPIC_API_KEY": "a"})


def test_unknown_provider_name_is_an_error_listing_the_valid_ones():
    import pytest

    with pytest.raises(ValueError, match="llama"):
        notes.resolve_provider("llama", {"ANTHROPIC_API_KEY": "a"})


def test_auto_follows_the_documented_order():
    env = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "GITHUB_TOKEN": "t"}
    assert notes.resolve_provider("auto", env)[0] == "gemini"
    env["ANTHROPIC_API_KEY"] = "a"
    assert notes.resolve_provider("auto", env)[0] == "anthropic"


def test_auto_with_no_keys_returns_none():
    assert notes.resolve_provider("auto", {}) is None
    assert notes.resolve_provider("", {}) is None


def test_the_resolved_callable_actually_reaches_its_transport():
    _, call = notes.resolve_provider("gemini", {"GEMINI_API_KEY": "g-key"})
    payload = {"candidates": [{"content": {"parts": [{"text": "VIA REGISTRY"}]}}]}
    assert call("P", post=lambda u, *, headers, json_body: payload) == "VIA REGISTRY"


# ── Prompt guardrails ─────────────────────────────────────────────────
#
# These pin the instructions that stop the drafter announcing our toolchain as
# a product feature. v2.1.7rc1 published "LEX can now connect to Gemini and
# OpenAI" from a commit about the release-note drafter. Deleting any of these
# lines re-opens that door, so the tests fail loudly if they go.

def test_prompt_states_what_lex_is():
    prompt = notes.build_prompt(_digest(), exemplar="X")
    assert "WHAT LEX IS" in prompt
    assert "never seen the source code" in prompt


def test_prompt_forbids_promoting_internal_work_to_features():
    prompt = notes.build_prompt(_digest(), exemplar="X")
    assert '"internal": true' in prompt
    assert "Leave them out." in prompt
    # The actual failure, kept in the prompt as a worked example. Collapse
    # whitespace first: the instructions are hard-wrapped, so the phrase spans
    # a line break in the source.
    flat = " ".join(prompt.split())
    assert "LEX can now connect to Gemini and OpenAI" in flat
    assert "that is our machinery, not the product" in flat


def test_prompt_permits_an_empty_release():
    prompt = notes.build_prompt(_digest(), exemplar="X")
    assert "No user-facing changes in this release." in prompt
    assert "Do not pad a thin release" in prompt


def test_prompt_requires_merging_duplicate_user_visible_changes():
    prompt = notes.build_prompt(_digest(), exemplar="X")
    assert "Merge entries describing the same user-visible change" in prompt


def test_the_internal_flag_reaches_the_prompt():
    d = {"tag": "v1", "previous_tag": None, "changes": [
        {"sha": "a", "component": "backend", "type": "feat", "scope": "release-notes",
         "breaking": False, "subject": "pluggable providers", "pr_number": 1, "internal": True},
    ]}
    assert '"internal": true' in notes.build_prompt(d, exemplar="X").lower()


# ── Model selection ───────────────────────────────────────────────────

def test_default_model_per_provider():
    assert notes.model_for("gemini", {}) == notes.GEMINI_MODEL
    assert notes.model_for("anthropic", {}) == notes.ANTHROPIC_MODEL


def test_lex_notes_model_overrides_the_default():
    assert notes.model_for("gemini", {"LEX_NOTES_MODEL": "gemini-3-ultra"}) == "gemini-3-ultra"


def test_the_resolved_callable_carries_the_overridden_model():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured.update(url=url)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    _, call = notes.resolve_provider(
        "gemini", {"GEMINI_API_KEY": "k", "LEX_NOTES_MODEL": "gemini-3-ultra"}
    )
    call("P", post=fake_post)
    assert "gemini-3-ultra" in captured["url"]


def test_prompt_tells_the_model_to_write_from_the_detail():
    flat = " ".join(notes.build_prompt(_digest(), exemplar="X").split())
    assert "Base your description on the detail, not the subject." in flat
    assert "Do not invent specifics to fill the gap" in flat


def test_prompt_includes_the_detail_text():
    d = {"tag": "v1", "previous_tag": None, "changes": [
        {"sha": "a", "component": "backend", "type": "fix", "scope": "auth",
         "breaking": False, "subject": "renew the token", "pr_number": 678,
         "internal": False, "detail": "Sessions died at the original deadline."},
    ]}
    assert "Sessions died at the original deadline." in notes.build_prompt(d, exemplar="X")


def test_an_oversized_digest_is_trimmed_into_budget():
    changes = [
        {"sha": f"{i}", "component": "backend", "type": "fix", "scope": "auth",
         "breaking": False, "subject": f"fix {i}", "pr_number": i,
         "internal": False, "detail": "y" * 9000}
        for i in range(20)
    ]
    prompt = notes.build_prompt(
        {"tag": "v1", "previous_tag": None, "changes": changes}, exemplar="X"
    )
    assert len(prompt.encode("utf-8")) <= notes.MAX_PROMPT_BYTES
    # Still describes every change, just more briefly.
    for i in range(20):
        assert f"fix {i}" in prompt


def test_fallback_body_is_visible_in_rendered_markdown():
    digest = {"tag": "v2.1.8", "previous_tag": "v2.1.7", "changes": [
        {"sha": "abc1234", "component": "backend", "type": "fix", "scope": None,
         "breaking": False, "subject": "a fix", "pr_number": 1, "internal": False},
    ]}
    body = notes.fallback(digest, reason="ValueError: boom")

    # The machine marker stays for tooling...
    assert notes.FAILURE_MARKER in body
    # ...but a human reading the rendered release must also see it.
    assert notes.FAILURE_NOTICE in body
    assert not notes.FAILURE_NOTICE.startswith("<!--")
    assert "boom" in body


def test_append_addendum_preserves_the_original_body():
    body = "## Main changes\n\n- **New sidebar.** More room for your data.\n"
    out = notes.append_addendum(body, "### Frontend changes\n\n- a fix\n")

    # Assert the exact joint, not a substring: `body.rstrip() in out` is true
    # whether or not the implementation rstrips, so it cannot pin the contract.
    assert out.startswith(body.rstrip() + "\n\n" + notes.ADDENDUM_MARKER + "\n\n")
    assert out.endswith("- a fix\n")
    assert "a fix" in out


def test_append_addendum_is_idempotent():
    body = "## Main changes\n\n- something\n"
    once = notes.append_addendum(body, "### Frontend changes\n\n- a fix\n")
    twice = notes.append_addendum(once, "### Frontend changes\n\n- a fix\n")

    assert once == twice
    assert twice.count(notes.ADDENDUM_MARKER) == 1


def test_append_addendum_never_drops_content_on_a_second_different_call():
    body = "## Main changes\n\n- something\n"
    once = notes.append_addendum(body, "### Frontend changes\n\n- first\n")
    # A later call with different text must not silently replace the first.
    twice = notes.append_addendum(once, "### Frontend changes\n\n- second\n")

    assert "first" in twice
    assert "second" not in twice          # refuses rather than overwrites
