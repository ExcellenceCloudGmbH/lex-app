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


# ── Heading tolerance ─────────────────────────────────────────────────
#
# v2.1.9 drafted 14 usable lines and threw them away: `validate` matched
# "## Main changes" as a case- and level-sensitive substring, and the model
# had emitted a variant. The heading it chose is a formatting detail, not a
# reason to discard the note — so recognise the variants, and rewrite them to
# the canonical form so the published body stays consistent either way.

HEADING_VARIANTS = [
    ("wrong case",        "## Main Changes"),
    ("all caps",          "## MAIN CHANGES"),
    ("deeper level",      "### Main changes"),
    ("shallower level",   "# Main changes"),
    ("bold pseudo",       "**Main changes**"),
    ("trailing colon",    "## Main changes:"),
    ("extra spacing",     "##   Main   changes"),
]


@pytest.mark.parametrize("label,heading", HEADING_VARIANTS, ids=[c[0] for c in HEADING_VARIANTS])
def test_validation_accepts_recognisable_heading_variants(label, heading):
    body = f"{heading}\n\n- **A change.** It does something.\n"
    assert notes.validate(body) is None, f"{label} was rejected"


@pytest.mark.parametrize("label,heading", HEADING_VARIANTS, ids=[c[0] for c in HEADING_VARIANTS])
def test_normalize_rewrites_variants_to_the_canonical_heading(label, heading):
    body = f"{heading}\n\n- **A change.** It does something.\n"
    out = notes.normalize(body)
    assert "## Main changes" in out, f"{label} was not canonicalised"
    assert heading not in out.replace("## Main changes", ""), f"{label} left a stray heading"


def test_normalize_leaves_canonical_output_untouched():
    assert notes.normalize(GOOD) == GOOD


def test_normalize_does_not_touch_bold_runs_inside_list_items():
    # "- **New sidebar.** ..." is an entry, not a heading. Rewriting it would
    # corrupt every note we produce.
    body = "## Main changes\n\n- **Main changes.** A trap.\n"
    assert notes.normalize(body) == body


def test_validation_still_rejects_prose_with_no_heading_at_all():
    assert notes.validate("Some prose with no headings at all.") is not None


def test_validation_still_rejects_an_empty_section_when_the_heading_is_a_variant():
    bad = "### Main Changes\n\n### Bug Fixes\n\n- **A fix.** Text.\n"
    assert notes.validate(bad) is not None


# ── Retry on a shape failure ──────────────────────────────────────────


def test_draft_retries_once_when_the_first_response_is_malformed():
    responses = ["I cannot help with that.", GOOD]
    prompts = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    out = notes.draft(_digest(), exemplar="X", model=model)
    assert len(prompts) == 2, "should have re-asked"
    assert notes.FAILURE_MARKER not in out
    assert "New sidebar" in out


def test_the_retry_prompt_tells_the_model_what_was_wrong():
    prompts = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return "I cannot help with that."

    notes.draft(_digest(), exemplar="X", model=model)
    assert len(prompts) == 2
    assert "no recognised section heading" in prompts[1]
    assert "no recognised section heading" not in prompts[0]


def test_draft_falls_back_after_two_malformed_responses():
    calls = []

    def model(prompt: str) -> str:
        calls.append(prompt)
        return "still not a release note"

    out = notes.draft(_digest(), exemplar="X", model=calls and model or model)
    assert notes.FAILURE_MARKER in out


def test_draft_does_not_retry_a_transport_error():
    # A 410 from a retired endpoint will not recover, and a second call costs
    # real money. Only shape failures are worth re-asking.
    calls = []

    def boom(prompt: str) -> str:
        calls.append(prompt)
        raise RuntimeError("410 github_models_retirement_brownout")

    out = notes.draft(_digest(), exemplar="X", model=boom)
    assert len(calls) == 1, "a transport error must not be retried"
    assert notes.FAILURE_MARKER in out


def test_build_prompt_accepts_a_retry_reason_and_omits_it_by_default():
    plain = notes.build_prompt(_digest(), exemplar="X")
    assert "PREVIOUS ATTEMPT" not in plain.upper()
    retried = notes.build_prompt(_digest(), exemplar="X", retry_reason="empty section: ## Bug fixes")
    assert "empty section: ## Bug fixes" in retried


def test_the_retry_suffix_is_counted_against_the_prompt_byte_budget():
    # A retry must never be the thing that pushes a prompt over the limit.
    fat = [{"sha": f"{i:07d}", "component": "backend", "type": "fix", "scope": "x",
            "breaking": False, "subject": f"change {i}", "pr_number": i,
            "detail": "y" * 4000} for i in range(60)]
    retried = notes.build_prompt(_digest(changes=fat), exemplar="X",
                                 retry_reason="no recognised section heading")
    assert len(retried.encode("utf-8")) <= notes.MAX_PROMPT_BYTES
    assert "no recognised section heading" in retried


# ── Prompt context: facts, the interface, and rollbacks ───────────────
#
# Everything below is context the 2.1.x backfill needed and the prompt could
# not previously see. Each case is a note that came out wrong, or could not be
# written at all, without it.

def test_the_prompt_carries_the_release_facts_when_given():
    prompt = notes.build_prompt(_digest(), exemplar="X", facts_block="- FACT ONE\n- FACT TWO")
    assert "FACT ONE" in prompt and "FACT TWO" in prompt


def test_the_prompt_omits_the_facts_section_when_there_are_none():
    assert "RELEASE FACTS" not in notes.build_prompt(_digest(), exemplar="X")


def test_the_prompt_says_the_interface_did_not_change_when_it_did_not():
    d = _digest(); d["frontend_recorded"] = True; d["frontend_commits"] = 0
    prompt = notes.build_prompt(d, exemplar="X")
    assert "did not change" in prompt


def test_the_prompt_refuses_to_claim_an_unchanged_interface_when_unknown():
    # A gap must never be reported to a customer as "nothing changed" — that is
    # the exact ambiguity the provenance work exists to remove.
    d = _digest(); d["frontend_recorded"] = False
    prompt = notes.build_prompt(d, exemplar="X")
    assert "could not be determined" in prompt
    assert "did not change" not in prompt


def test_the_prompt_reports_how_many_interface_changes_there_are():
    d = _digest(); d["frontend_recorded"] = True; d["frontend_commits"] = 87
    assert "87" in notes.build_prompt(d, exemplar="X")


@pytest.mark.parametrize("rule", [
    "rolled back",          # a release that removes what an earlier one shipped
    "Upgrade note",         # the closing section
    "reported by a customer",  # provenance worth leading with
])
def test_the_prompt_teaches_the_rules_the_backfill_needed(rule):
    # Whitespace-normalised: a rule must not pass or fail on where it wraps.
    prompt = " ".join(notes.build_prompt(_digest(), exemplar="X").lower().split())
    assert rule.lower() in prompt, f"prompt does not mention: {rule}"


def test_draft_short_circuits_when_every_change_is_internal():
    # v2.1.9 had 34 commits, all release tooling. Asking a model to write a
    # customer note from that invites it to promote our machinery into a
    # feature, which is the failure INTERNAL_SCOPES already exists to prevent.
    called = []
    d = _digest(changes=[
        {"sha": "1111111", "component": "backend", "type": "feat", "scope": "release-notes",
         "breaking": False, "subject": "backfill a span of tags", "pr_number": None,
         "internal": True, "detail": ""},
    ])
    out = notes.draft(d, exemplar="X", model=lambda p: called.append(p) or GOOD)
    assert called == [], "the model must not be called for a wholly internal release"
    assert "no user-facing changes" in out.lower()
    assert notes.FAILURE_MARKER not in out


def test_a_release_with_one_shippable_change_still_calls_the_model():
    d = _digest(changes=[
        {"sha": "1111111", "component": "backend", "type": "feat", "scope": "release-notes",
         "breaking": False, "subject": "internal thing", "pr_number": None,
         "internal": True, "detail": ""},
        {"sha": "2222222", "component": "backend", "type": "fix", "scope": "grid",
         "breaking": False, "subject": "a real user fix", "pr_number": None,
         "internal": False, "detail": "The grid dropped rows."},
    ])
    called = []
    notes.draft(d, exemplar="X", model=lambda p: called.append(p) or GOOD)
    assert len(called) == 1


def test_build_prompt_falls_back_to_the_facts_the_digest_carries():
    # draft() and build_prompt() must agree: a caller that builds a prompt
    # directly should not silently lose the computed facts.
    d = _digest(); d["facts"] = "- A COMPUTED FACT"
    assert "A COMPUTED FACT" in notes.build_prompt(d, exemplar="X")


def test_an_explicit_facts_block_still_overrides_the_digest():
    d = _digest(); d["facts"] = "- FROM DIGEST"
    out = notes.build_prompt(d, exemplar="X", facts_block="- EXPLICIT")
    assert "EXPLICIT" in out and "FROM DIGEST" not in out
