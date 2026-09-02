#!/usr/bin/env python3
"""Draft the business-facing release note from a digest.

A changelog generator must never block a release, so every failure path here
produces a usable body rather than an exception: the raw digest plus a marker
a human can act on.
"""

from __future__ import annotations

import functools
import json
import re
import urllib.request
from typing import Callable

FAILURE_MARKER = "<!-- lex:notes-draft-failed -->"

# The marker above is an HTML comment, so it renders as nothing. A release
# whose note failed to draft must say so where a human will actually see it.
FAILURE_NOTICE = (
    "> ⚠️ **Automatic release-note drafting failed — this body needs a human rewrite.**"
)

# Marks a frontend addendum added after publication. Its presence makes the
# append idempotent, which matters because the alternative — rewriting the body
# — would destroy prose a human reviewed and edited.
ADDENDUM_MARKER = "<!-- lex:frontend-addendum -->"


def append_addendum(body: str, addendum: str, *, marker: str = ADDENDUM_MARKER) -> str:
    """Append `addendum` to a published release body, exactly once.

    Returns `body` unchanged when an addendum is already present. Refusing is
    deliberate: a second, different addendum means someone is trying to correct
    a correction, and doing that automatically would silently discard the first.
    """
    if marker in body:
        return body
    return f"{body.rstrip()}\n\n{marker}\n\n{addendum.strip()}\n"


# Used only when docs/releases/RELEASE_NOTES_2.1.3_github.md is unavailable.
# The real file is richer and should win; this exists so a missing style
# reference degrades the prose instead of killing the release note.
FALLBACK_EXEMPLAR = """\
## Main changes

- **New sidebar.** A full-height side navigation with a consolidated header bar.
  More room for your data, and models are easier to find.
- **Number formatting per column.** Choose how a numeric column displays — a plain
  number, a currency, or a percentage — and how many decimals to show.

## Optimizations

- **Live-updating tables.** Open grids refresh themselves when the underlying data
  changes — no manual **Refresh**.

## Bug fixes

- **Timezone bug.** Timestamps could appear shifted by a couple of hours. Times now
  display correctly in your local timezone.

**Upgrade note:** run database migrations on upgrade. No configuration changes needed.
"""

_RETRY_SUFFIX = """

Your PREVIOUS ATTEMPT at this note was rejected: {reason}.
Use only the headings listed above, spelled exactly as shown, and put at least
one entry under every heading you write. Omit a heading you have nothing for
rather than leaving it empty.
"""

REQUIRED_HEADINGS = ("## Main changes", "## Optimizations", "## Bug fixes")

# A heading is a formatting detail; the model choosing "## Main Changes" or
# "### Main changes" is not a reason to discard an otherwise good note. v2.1.9
# drafted 14 usable lines and threw them all away over exactly that. So:
# recognise the variants, and rewrite them to the canonical spelling so the
# published body stays consistent whichever one arrives.
_CANONICAL = {h.lstrip("# ").casefold(): h for h in REQUIRED_HEADINGS}

# ATX headings at any level. A fully-bold line counts too — models reach for
# it when asked for a heading — but `[^*]+?` and the anchored `\*\*` keep it to
# lines that are ONE bold run, so "**Upgrade note:** run migrations" and
# "- **Main changes.** a trap" are left alone.
_ATX_RE = re.compile(r"^[ \t]{0,3}\#{1,6}[ \t]*(?P<text>.+?)[ \t]*:?[ \t]*$")
_BOLD_RE = re.compile(r"^[ \t]{0,3}\*\*(?P<text>[^*]+?)\*\*[ \t]*:?[ \t]*$")


def _heading_key(line: str) -> str | None:
    """Canonical lookup key for `line`, or None if it is not a heading."""
    match = _ATX_RE.match(line) or _BOLD_RE.match(line)
    if match is None:
        return None
    return " ".join(match.group("text").split()).rstrip(":").casefold()


def normalize(text: str) -> str:
    """Rewrite recognised heading variants to their canonical spelling."""
    out = []
    for line in text.splitlines(keepends=True):
        canonical = _CANONICAL.get(_heading_key(line))
        if canonical is None:
            out.append(line)
        else:
            out.append(canonical + line[len(line.rstrip("\r\n")):])
    return "".join(out)

# ── Transports ────────────────────────────────────────────────────────
#
# Anthropic is the default. GitHub Models was the original choice because it
# needed no new secret, but it is being retired: as of 2026-08-05 its endpoint
# answers 410 with `github_models_retirement_brownout`. It is kept as a
# fallback for whatever remains of its life, and the CLI prefers Anthropic
# whenever ANTHROPIC_API_KEY is set.

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = "claude-sonnet-5"
ANTHROPIC_MAX_TOKENS = 4096

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
GEMINI_MODEL = "gemini-2.5-pro"

OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o"

MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
# A live external dependency: GitHub's model catalogue changes, and a retired
# id becomes a hard error here. `draft()` degrades to the fallback body rather
# than failing the gate, so the symptom is stub notes, not a red release.
MODEL = "openai/gpt-4o"

_INSTRUCTIONS = """\
You are writing the customer-facing release note for LEX.

WHAT LEX IS
LEX is a platform for building business applications. Customers use a deployed
LEX instance through a web UI: browsing and editing records in data grids,
uploading files, running calculations over that data, and reading calculation
logs and audit history. Your readers are those business users and the people
who support them. They have never seen the source code and do not know how LEX
is built, tested or released.

THE MOST IMPORTANT RULE
Describe only what changed **for someone using LEX**. The digest also contains
work on the toolchain that builds and ships LEX — CI pipelines, test plans,
release automation, developer setup. Entries with "internal": true are that
kind of work. Leave them out. Entries without the flag may still be internal;
judge by whether a user of the LEX web UI could notice.

Never describe a change to our own tooling as a LEX capability. A previous
release turned "the release-note drafter can now call Gemini" into "LEX can now
connect to Gemini and OpenAI, allowing you to integrate with a wider range of
large language models" — a feature that does not exist, announced to customers.
If you find yourself writing about models, pipelines, CI, or repositories, stop:
that is our machinery, not the product.

IF NOTHING IS USER-FACING
Say so, in full:

    No user-facing changes in this release. This release contains internal
    improvements to how LEX is built and released.

    **Upgrade note:** no action is needed on upgrade.

That is a correct and complete answer. Do not pad a thin release by promoting
internal work into features. A short honest note is worth more than a long
invented one.

WHAT YOU ARE GIVEN
Each entry has a "subject" — one line, written for reviewers — and a "detail",
which is the author's own explanation from the pull request. **Base your
description on the detail, not the subject.** The subject says what was
changed; the detail usually says what was wrong, what a user experienced, and
what they will experience now. That is the release note.

The detail is written for engineers. Take the user-visible facts from it and
leave the internals behind: read past the root-cause analysis and file names to
the symptom and the outcome. If the detail describes something a user never
sees, that entry probably does not belong in the note at all.

Where an entry has no detail, say only what the subject supports. Do not
invent specifics to fill the gap — a thin bullet is better than a confident
wrong one.

FORMAT
Use only these headings, in this order, and only when you have entries for them:
"## Main changes" (new capability), "## Optimizations" (existing things now
faster, lighter or more reliable), "## Bug fixes" (something was wrong and is
now right). Omit any heading you have nothing for. Never write a heading with
nothing under it.

Each entry is one bullet: a bold summary phrase, then one or two plain
sentences saying what a user will notice.

    - **New sidebar.** A full-height side navigation with a consolidated header
      bar. More room for your data, and models are easier to find.

WRITING
- Group by what the change means to a user, never by component or repository.
  Never write "backend", "frontend", or a repository name.
- Merge entries describing the same user-visible change into one bullet. Two
  fixes to embedded authentication are one bullet, not two.
- Be concrete. "Date columns show the date only, with the full timestamp on
  hover" — not "an improved date experience".
- Cut filler. No "seamless", "robust", "enhanced experience", "allowing you to",
  "a wider range of". If removing a phrase loses no information, remove it.
- Every statement must trace to a digest entry. Do not infer capabilities that
  are not there, and do not soften or inflate what an entry says.
- No class names, file paths, commit hashes, PR numbers or internal jargon.
- End with a line starting "**Upgrade note:**" — any action needed on upgrade,
  or that none is.

Match the tone of this previous release note:

<exemplar>
{exemplar}
</exemplar>

Changes in {tag}:

<digest>
{digest}
</digest>

Return only the markdown release note. No preamble, no explanation.
"""


# Matches the budget the Copilot test-bot prompt uses in
# .github/scripts/copilot_assemble_prompt.py. Well inside every provider's
# context window; the cap exists to stop an unusually large release quietly
# turning into an expensive or truncated request.
MAX_PROMPT_BYTES = 60_000


def build_prompt(digest: dict, *, exemplar: str, retry_reason: str | None = None) -> str:
    """Assemble the model prompt from the digest and a style exemplar.

    Entries carry the author's PR body in "detail". That is the material the
    note is actually written from — a subject line alone makes the model invent
    specifics, which is how "renew the embedded Streamlit token" became a claim
    about automatic session renewal that nobody had verified.
    """
    changes = digest["changes"]
    # The retry suffix counts against the budget. Adding it afterwards would
    # let a retry of an already-near-budget prompt quietly overflow, which is
    # the one case where a retry must not make things worse.
    suffix = "" if retry_reason is None else _RETRY_SUFFIX.format(reason=retry_reason)
    budget = MAX_PROMPT_BYTES - len(suffix.encode("utf-8"))

    prompt = _INSTRUCTIONS.format(
        exemplar=exemplar, tag=digest["tag"],
        digest=json.dumps(changes, indent=2),
    )

    if len(prompt.encode("utf-8")) > budget:
        # Over budget: shorten details, longest first, so one enormous PR body
        # cannot crowd out every other change. Internal entries already have none.
        trimmed = [dict(c) for c in changes]
        for limit in (2000, 1000, 400, 0):
            for entry in trimmed:
                if len(entry.get("detail", "")) > limit:
                    entry["detail"] = entry["detail"][:limit].rstrip() + "…[truncated]" if limit else ""
            prompt = _INSTRUCTIONS.format(
                exemplar=exemplar, tag=digest["tag"],
                digest=json.dumps(trimmed, indent=2),
            )
            if len(prompt.encode("utf-8")) <= budget:
                break

    return prompt + suffix


def validate(text: str) -> str | None:
    """Return a reason string if `text` is unusable, else None."""
    if not text or not text.strip():
        return "empty response"

    lines = text.splitlines()
    found = [
        (i, _CANONICAL[key])
        for i, line in enumerate(lines)
        if (key := _heading_key(line)) in _CANONICAL
    ]
    if not found:
        return "no recognised section heading"

    # A heading followed immediately by another heading, or by nothing, means
    # the model emitted an empty section. Bounding each section by the NEXT
    # recognised heading is what makes this work for variants too.
    for n, (index, canonical) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(lines)
        if not "\n".join(lines[index + 1:end]).strip():
            return f"empty section: {canonical}"

    return None


def fallback(digest: dict, *, reason: str) -> str:
    """A usable body for when drafting fails. Never raises."""
    lines = [
        FAILURE_MARKER,
        "",
        FAILURE_NOTICE,
        "",
        f"Automatic release-note drafting failed ({reason}).",
        "The changes below are the raw digest — please rewrite this section",
        "before promoting, or promote as-is and edit the release afterwards.",
        "",
        "## Main changes",
        "",
    ]
    for change in digest["changes"]:
        pr = f" (#{change['pr_number']})" if change.get("pr_number") else ""
        lines.append(f"- {change['subject']}{pr}")
    return "\n".join(lines) + "\n"


def draft(digest: dict, *, exemplar: str, model: Callable[[str], str]) -> str:
    """Draft the note, degrading to a fallback body on any failure."""
    if not digest["changes"]:
        return (
            "No user-facing changes in this release.\n\n"
            "**Upgrade note:** no action needed.\n"
        )

    # A malformed response is re-asked once, carrying the reason: v2.1.9 threw
    # away 14 usable lines over a heading variant, and one re-ask costs far
    # less than a hand-written note. A transport error is NOT retried — a
    # retired endpoint will not recover, and the second call still bills.
    problem = None
    for _attempt in (1, 2):
        prompt = build_prompt(digest, exemplar=exemplar, retry_reason=problem)
        try:
            text = model(prompt)
        except Exception as exc:
            return fallback(digest, reason=f"{type(exc).__name__}: {exc}")

        text = normalize(text)
        problem = validate(text)
        if problem is None:
            return text
    return fallback(digest, reason=problem)


def _post(url: str, *, headers: dict, json_body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(json_body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def github_models(
    prompt: str, *, api_key: str, model: str = MODEL,
    post: Callable[..., dict] = _post,
) -> str:
    """Call GitHub Models with the job's GITHUB_TOKEN. No new secret needed.

    The calling workflow job must declare `permissions: models: read`.
    """
    payload = post(
        MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/vnd.github+json"},
        json_body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    )
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected response from GitHub Models: {payload!r}") from exc


def anthropic_messages(
    prompt: str, *, api_key: str, model: str = ANTHROPIC_MODEL,
    post: Callable[..., dict] = _post,
) -> str:
    """Draft via the Anthropic Messages API. Requires ANTHROPIC_API_KEY."""
    payload = post(
        ANTHROPIC_ENDPOINT,
        headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
        json_body={
            "model": model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    try:
        blocks = [b["text"] for b in payload["content"] if b.get("type") == "text"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unexpected response from Anthropic: {payload!r}") from exc
    if not blocks:
        raise ValueError(f"unexpected response from Anthropic, no text: {payload!r}")
    return "".join(blocks)


def _chat_completion_text(payload: dict, who: str) -> str:
    """Pull the text out of an OpenAI-shaped chat completion."""
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected response from {who}: {payload!r}") from exc


def openai_chat(
    prompt: str, *, api_key: str, model: str = OPENAI_MODEL,
    post: Callable[..., dict] = _post,
) -> str:
    """Draft via the OpenAI chat-completions API. Requires OPENAI_API_KEY."""
    payload = post(
        OPENAI_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    )
    return _chat_completion_text(payload, "OpenAI")


def gemini_generate(
    prompt: str, *, api_key: str, model: str = GEMINI_MODEL,
    post: Callable[..., dict] = _post,
) -> str:
    """Draft via the Gemini generateContent API. Requires GEMINI_API_KEY.

    The key goes in the `x-goog-api-key` header rather than the `?key=` query
    parameter Google's quickstarts use. Both authenticate; only one keeps the
    credential out of request logs, proxy logs and error messages.
    """
    payload = post(
        GEMINI_ENDPOINT.format(model=model),
        headers={"x-goog-api-key": api_key},
        json_body={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        },
    )
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        blocks = [part["text"] for part in parts if "text" in part]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected response from Gemini: {payload!r}") from exc
    if not blocks:
        raise ValueError(f"unexpected response from Gemini, no text: {payload!r}")
    return "".join(blocks)


# ── Provider registry ─────────────────────────────────────────────────
#
# Which provider drafts the note is configuration, not code. Set
# LEX_NOTES_PROVIDER to one of the names below, or leave it unset for "auto",
# which picks the first provider whose key is present in PROVIDER_ORDER.

PROVIDERS: dict[str, tuple[str, Callable[..., str]]] = {
    "anthropic": ("ANTHROPIC_API_KEY", anthropic_messages),
    "gemini": ("GEMINI_API_KEY", gemini_generate),
    "openai": ("OPENAI_API_KEY", openai_chat),
    "github-models": ("GITHUB_TOKEN", github_models),
}

# Auto-selection order. GitHub Models is last: it is being retired and now
# answers 410 during its brownout, so it is only ever a last resort.
PROVIDER_ORDER = ("anthropic", "gemini", "openai", "github-models")

_DEFAULT_MODELS = {
    "anthropic": ANTHROPIC_MODEL,
    "gemini": GEMINI_MODEL,
    "openai": OPENAI_MODEL,
    "github-models": MODEL,
}

# What people actually type.
_ALIASES = {
    "google": "gemini",
    "gemini": "gemini",
    "gpt": "openai",
    "openai": "openai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "github": "github-models",
    "github-models": "github-models",
    "githubmodels": "github-models",
}


def model_for(provider: str, env: dict) -> str:
    """The model to use, honouring a LEX_NOTES_MODEL override."""
    override = (env.get("LEX_NOTES_MODEL") or "").strip()
    if override:
        return override
    return _DEFAULT_MODELS[provider]


def resolve_provider(
    choice: str | None, env: dict
) -> tuple[str, Callable[..., str]] | None:
    """Resolve a provider name and env into (name, callable).

    The callable takes the prompt and an optional `post`, with the key already
    bound. Returns None when nothing is configured — the caller turns that into
    a fallback body rather than a crash. Raises ValueError when an explicit
    choice is unusable, because silently drafting with a different provider
    than the one asked for is worse than failing loudly.
    """
    name = (choice or "").strip().lower()

    if name and name != "auto":
        resolved = _ALIASES.get(name)
        if resolved is None:
            raise ValueError(
                f"unknown notes provider {choice!r}. Valid: "
                + ", ".join(sorted(set(_ALIASES.values())))
                + ", auto"
            )
        env_key, call = PROVIDERS[resolved]
        api_key = env.get(env_key, "")
        if not api_key:
            raise ValueError(
                f"notes provider {resolved!r} was requested but {env_key} is not set"
            )
        return resolved, functools.partial(call, api_key=api_key, model=model_for(resolved, env))

    for candidate in PROVIDER_ORDER:
        env_key, call = PROVIDERS[candidate]
        api_key = env.get(env_key, "")
        if api_key:
            return candidate, functools.partial(call, api_key=api_key, model=model_for(candidate, env))

    return None
