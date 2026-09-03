#!/usr/bin/env python3
"""Post a release to Linear as a project update, so it appears in Pulse.

Pulse is a feed of **project and initiative updates** — not a surface you can
post to directly. The way a release reaches it is a project status update on
the LEX App project, which is what this module creates.

The format here is deliberately NOT the one published to quackback. That is a
help-centre article for customers, read once, in full, by someone looking for
it. This is an internal feed entry, skimmed by colleagues who did not go
looking: it leads with what shipped, keeps the headlines to one line each, and
puts anything requiring action where it cannot be missed.

Nothing here may fail a release. By the time it runs the note is already
public; a missing feed entry is a nuisance, a red release is an incident.

Uses Linear's GraphQL API rather than an MCP server, because a GitHub Actions
job has no MCP. That needs LINEAR_API_KEY — a Linear personal API key, sent
bare in the Authorization header (Linear does not use a Bearer prefix).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

ENDPOINT = "https://api.linear.app/graphql"
TIMEOUT_SECONDS = 20

ENV_TOKEN = "LINEAR_API_KEY"
ENV_PROJECT = "LINEAR_PROJECT_ID"

# Lets a re-run update its own entry instead of posting a second one to a feed
# colleagues read. Linear has no slug on a project update, so the marker is
# how the previous entry is recognised.
MARKER = "<!-- lex:release {tag} -->"

_HEADING_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(?P<text>.+)$")


def _plain(text: str) -> str:
    """Markdown emphasis and links reduced to their words."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.*?\)", r"\1", text)
    return text.strip()


def summarise(body: str, *, per_section: int = 4) -> dict[str, list[str]]:
    """The note's bullets, grouped by heading and trimmed to the lead sentence.

    A feed entry that reproduces the whole note is a wall of text nobody reads,
    so each bullet keeps its bolded lead — which the house style already uses
    as the summary — and drops the explanation after it.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        heading = _HEADING_RE.match(line)
        if heading:
            current = heading["name"]
            sections.setdefault(current, [])
            continue
        bullet = _BULLET_RE.match(line)
        if bullet and current and len(sections[current]) < per_section:
            lead = re.match(r"\*\*(?P<lead>.+?)\*\*", bullet["text"])
            sections[current].append(_plain(lead["lead"] if lead else bullet["text"]))
    return {name: items for name, items in sections.items() if items}


def upgrade_line(body: str, *, limit: int = 240) -> str | None:
    """The upgrade note's first real statement, bullet or prose.

    This is the half a colleague scanning a feed must not miss, and it is often
    written as prose rather than bullets — v2.1.4's is a whole section about a
    data-repair command. `summarise` only collects bullets, so it would drop
    exactly the entry that mattered most.
    """
    lines = body.splitlines()
    for index, raw in enumerate(lines):
        if not re.match(r"^\s*(##+\s*)?\*{0,2}Upgrade note", raw.strip(), re.I):
            continue
        for follow in lines[index:]:
            text = follow.strip()
            text = re.sub(r"^##+\s*", "", text)
            text = re.sub(r"^[-*]\s+", "", text)
            text = re.sub(r"^\*{0,2}Upgrade note:?\*{0,2}\s*", "", text, flags=re.I)
            text = _plain(text)
            if text and not text.startswith((">", "|", "```", "---")):
                # "**Upgrade note:** run migrations" leaves a lowercase start
                # once the label is stripped. It reads as a fragment otherwise.
                text = text[0].upper() + text[1:]
                return text[:limit].rstrip()
    return None


def render(tag: str, body: str, *, repo: str, frontend: str | None = None) -> str:
    """The feed entry. Short by design — the full note is one click away."""
    release_url = f"https://github.com/{repo}/releases/tag/{tag}"
    lines = [
        MARKER.format(tag=tag),
        "",
        f"**{tag} is out.** [Release notes]({release_url}) · "
        f"[CHANGELOG](https://github.com/{repo}/blob/lex-app-v2/CHANGELOG.md)",
        "",
    ]

    if frontend:
        lines += [f"Frontend: `lex-frontend {frontend}`", ""]
    else:
        # Said out loud rather than omitted. An absent line reads as "no
        # frontend change", which is a different thing from "not recorded".
        lines += ["Frontend version: not recorded for this release.", ""]

    sections = summarise(body)
    sections.pop("Upgrade note", None)   # handled separately, see upgrade_line
    attention = upgrade_line(body)

    # A release with nothing user-facing says so first. Leading with its
    # Internal section instead would bury the one thing a colleague scanning
    # the feed actually needs to know.
    if re.search(r"no user-facing changes", body, re.I):
        lines += ["No user-facing changes — internal work only.", ""]

    for name, items in sections.items():
        lines.append(f"**{name}**")
        lines += [f"- {item}" for item in items]
        lines.append("")

    if not sections:
        lines += ["No user-facing changes — internal work only.", ""]

    if attention:
        lines += ["**On upgrade**", attention, ""]

    # The one thing a reader must not miss, and the note itself says it.
    if re.search(r"run (database )?migrations", body, re.I):
        lines += ["⚠️ This release needs database migrations on upgrade.", ""]

    return "\n".join(lines).rstrip() + "\n"


def _graphql(query: str, variables: dict, *, token: str) -> dict:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        method="POST",
        headers={
            # Linear personal API keys go in bare — no Bearer prefix.
            "Authorization": token,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errors"):
        first = payload["errors"][0].get("message", "unknown GraphQL error")
        raise RuntimeError(first)
    return payload.get("data") or {}


_FIND = """
query($projectId: String!) {
  project(id: $projectId) {
    projectUpdates(first: 25) { nodes { id body } }
  }
}
"""

_CREATE = """
mutation($projectId: String!, $body: String!) {
  projectUpdateCreate(input: {projectId: $projectId, body: $body}) {
    success projectUpdate { id url }
  }
}
"""

_UPDATE = """
mutation($id: String!, $body: String!) {
  projectUpdateUpdate(id: $id, input: {body: $body}) {
    success projectUpdate { id url }
  }
}
"""


def find_existing(project_id: str, tag: str, *, token: str,
                  graphql: Callable[..., dict] = _graphql) -> str | None:
    """The id of a previous update for this tag, or None."""
    data = graphql(_FIND, {"projectId": project_id}, token=token)
    nodes = (((data.get("project") or {}).get("projectUpdates") or {}).get("nodes")) or []
    marker = MARKER.format(tag=tag)
    for node in nodes:
        if marker in (node.get("body") or ""):
            return node.get("id")
    return None


def _redact(message: str, token: str) -> str:
    """Keep the API key out of anything printed into a workflow log."""
    return message.replace(token, "lin_***") if token else message


def publish(tag: str, body: str, *, token: str, project_id: str, repo: str,
            frontend: str | None = None,
            graphql: Callable[..., dict] = _graphql) -> str:
    """Post or update the Pulse entry for `tag`. Returns a summary, never raises."""
    if not token:
        return f"skipped: {ENV_TOKEN} is not set"
    if not project_id:
        return f"skipped: {ENV_PROJECT} is not set"

    entry = render(tag, body, repo=repo, frontend=frontend)
    try:
        existing = find_existing(project_id, tag, token=token, graphql=graphql)
        if existing:
            graphql(_UPDATE, {"id": existing, "body": entry}, token=token)
            return f"updated the Pulse entry for {tag} ({existing})"
        data = graphql(_CREATE, {"projectId": project_id, "body": entry}, token=token)
        created = (data.get("projectUpdateCreate") or {}).get("projectUpdate") or {}
        return f"posted a Pulse entry for {tag} ({created.get('url') or created.get('id', '?')})"
    except urllib.error.HTTPError as exc:
        return _redact(f"failed: HTTP {exc.code} from Linear", token)
    except Exception as exc:  # noqa: BLE001 - a release must not go red for this
        return _redact(f"failed: {type(exc).__name__}: {exc}", token)


def publish_from_env(tag: str, body: str, *, repo: str, **kwargs) -> str:
    return publish(
        tag, body, repo=repo,
        token=os.environ.get(ENV_TOKEN, ""),
        project_id=os.environ.get(ENV_PROJECT, ""),
        **kwargs,
    )
