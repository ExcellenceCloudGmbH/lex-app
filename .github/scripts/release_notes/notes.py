#!/usr/bin/env python3
"""Draft the business-facing release note from a digest.

A changelog generator must never block a release, so every failure path here
produces a usable body rather than an exception: the raw digest plus a marker
a human can act on.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

FAILURE_MARKER = "<!-- lex:notes-draft-failed -->"

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

REQUIRED_HEADINGS = ("## Main changes", "## Optimizations", "## Bug fixes")

MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
# A live external dependency: GitHub's model catalogue changes, and a retired
# id becomes a hard error here. `draft()` degrades to the fallback body rather
# than failing the gate, so the symptom is stub notes, not a red release.
MODEL = "openai/gpt-4o"

_INSTRUCTIONS = """\
You are writing the release note for a business application called LEX.

Audience: two readers at once. A business user who has never seen the codebase
and wants to know what changed for them, and a technical user who wants to know
what actually changed. One document must satisfy both.

Rules:
- Use only these headings, in this order: "## Main changes", "## Optimizations",
  "## Bug fixes". Omit any heading you have nothing to put under it. Never emit
  a heading with no entries.
- Each entry is a bullet starting with a bold summary phrase, then one or two
  plain sentences. Example: "- **New sidebar.** A full-height side navigation."
- Group entries by what the change means to a user, NOT by which repository or
  component it came from. Never mention "backend", "frontend", or repository
  names.
- Do not invent changes. Every entry must trace to an item in the digest.
- Do not mention internal class names, file paths, or commit hashes.
- End with a line starting "**Upgrade note:**" describing any action needed on
  upgrade, or stating that none is needed.

Match the tone and shape of this previous release note exactly:

<exemplar>
{exemplar}
</exemplar>

Here is the digest of what changed in {tag}:

<digest>
{digest}
</digest>

Return only the markdown release note. No preamble, no explanation.
"""


def build_prompt(digest: dict, *, exemplar: str) -> str:
    """Assemble the model prompt from the digest and a style exemplar."""
    return _INSTRUCTIONS.format(
        exemplar=exemplar,
        tag=digest["tag"],
        digest=json.dumps(digest["changes"], indent=2),
    )


def validate(text: str) -> str | None:
    """Return a reason string if `text` is unusable, else None."""
    if not text or not text.strip():
        return "empty response"

    present = [h for h in REQUIRED_HEADINGS if h in text]
    if not present:
        return "no recognised section heading"

    # A heading followed immediately by another heading, or by nothing, means
    # the model emitted an empty section.
    for heading in present:
        body = text.split(heading, 1)[1]
        for other in REQUIRED_HEADINGS:
            if other != heading and other in body:
                body = body.split(other, 1)[0]
        if not body.strip():
            return f"empty section: {heading}"

    return None


def fallback(digest: dict, *, reason: str) -> str:
    """A usable body for when drafting fails. Never raises."""
    lines = [
        FAILURE_MARKER,
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

    prompt = build_prompt(digest, exemplar=exemplar)
    try:
        text = model(prompt)
    except Exception as exc:
        return fallback(digest, reason=f"{type(exc).__name__}: {exc}")

    problem = validate(text)
    if problem is not None:
        return fallback(digest, reason=problem)
    return text


def _post(url: str, *, headers: dict, json_body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(json_body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def github_models(prompt: str, *, token: str, post: Callable[..., dict] = _post) -> str:
    """Call GitHub Models with the job's GITHUB_TOKEN. No new secret needed.

    The calling workflow job must declare `permissions: models: read`.
    """
    payload = post(
        MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json_body={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    )
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected response from GitHub Models: {payload!r}") from exc
