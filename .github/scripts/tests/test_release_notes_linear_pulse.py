"""Tests for release_notes.linear_pulse — the release as a Linear Pulse entry.

Pulse is a feed of PROJECT and INITIATIVE updates, not a surface you post to
directly, so a release reaches it as a project status update.

The format is deliberately not quackback's. That is a help-centre article read
once, in full, by someone who went looking. This is a feed entry skimmed by
colleagues who did not — so it leads with what shipped, one line per change,
and anything requiring action is impossible to miss.
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import linear_pulse as pulse  # noqa: E402

REPO = "ExcellenceCloudGmbH/lex-app"
PROJECT = "e58c4588-908a-4788-a97d-8305b2e3fb3e"

NOTE = """## Main changes

- **New sidebar.** A full-height side navigation with a consolidated header bar.
- **Settings panel.** Each user can tune how a grid looks and behaves.

## Bug fixes

- **Timezone shifts.** Times display correctly in your local timezone.

---

**Upgrade note:** run database migrations on upgrade. This adds one nullable column.
"""


def _graphql(nodes=None, created_url="https://linear.app/x/update/1"):
    calls = []

    def graphql(query, variables, *, token):
        # "query($projectId: String!)" — the operation keyword only.
        kind = query.strip().split("(")[0].split()[0]
        calls.append({"query": kind, "variables": variables})
        if "projectUpdates" in query:
            return {"project": {"projectUpdates": {"nodes": nodes or []}}}
        if "projectUpdateCreate" in query:
            return {"projectUpdateCreate": {"success": True,
                                            "projectUpdate": {"id": "u1", "url": created_url}}}
        return {"projectUpdateUpdate": {"success": True, "projectUpdate": {"id": "u1"}}}

    return graphql, calls


# ── The format is its own thing ───────────────────────────────────────

def test_the_entry_leads_with_the_version_and_links_out():
    out = pulse.render("v2.1.9", NOTE, repo=REPO)
    assert "**v2.1.9 is out.**" in out
    assert f"https://github.com/{REPO}/releases/tag/v2.1.9" in out
    assert "CHANGELOG" in out


def test_each_change_is_reduced_to_its_lead_sentence():
    out = pulse.render("v2.1.9", NOTE, repo=REPO)
    # The house style already writes a bolded lead as the summary; the feed
    # keeps that and drops the explanation, so the entry stays skimmable.
    assert "- New sidebar." in out
    assert "A full-height side navigation" not in out


def test_the_upgrade_note_survives_even_when_it_is_prose():
    # v2.1.4's upgrade note is a whole prose section about a repair command.
    # Collecting only bullets would drop the entry that mattered most.
    out = pulse.render("v2.1.9", NOTE, repo=REPO)
    assert "**On upgrade**" in out
    assert "Run database migrations on upgrade." in out


def test_a_release_needing_migrations_says_so_unmissably():
    assert "⚠️" in pulse.render("v2.1.9", NOTE, repo=REPO)


def test_a_release_needing_nothing_carries_no_warning():
    note = "## Bug fixes\n\n- **A fix.** Text.\n\n**Upgrade note:** no action needed.\n"
    assert "⚠️" not in pulse.render("v2.1.9", note, repo=REPO)


def test_the_upgrade_line_reads_as_a_sentence_not_a_fragment():
    # Stripping "**Upgrade note:**" leaves a lowercase start.
    out = pulse.render("v2.1.9", NOTE, repo=REPO)
    assert "Run database migrations" in out
    assert "run database migrations on upgrade. This adds" not in out


def test_a_wholly_internal_release_says_so_first():
    note = ("No user-facing changes in this release.\n\n## Internal\n\n"
            "- **Tooling.** Something.\n")
    out = pulse.render("v2.1.9", note, repo=REPO)
    # Leading with the Internal section would bury the only thing a colleague
    # scanning the feed needs to know.
    body = out.split("not recorded for this release.")[-1]
    assert body.index("No user-facing changes") < body.index("**Internal**")


@pytest.mark.parametrize("frontend,expected", [
    ("1.10.0", "lex-frontend 1.10.0"),
    (None, "not recorded"),
])
def test_the_frontend_version_is_stated_either_way(frontend, expected):
    # Omitting the line would read as "no frontend change", which is a
    # different thing from "not recorded".
    assert expected in pulse.render("v2.1.9", NOTE, repo=REPO, frontend=frontend)


# ── Posting ───────────────────────────────────────────────────────────

def test_a_new_release_creates_a_project_update():
    graphql, calls = _graphql()
    out = pulse.publish("v2.1.9", NOTE, token="lin_t", project_id=PROJECT,
                        repo=REPO, graphql=graphql)
    assert [c["query"] for c in calls] == ["query", "mutation"]
    assert calls[1]["variables"]["projectId"] == PROJECT
    assert "posted" in out


def test_a_rerun_updates_its_own_entry_rather_than_posting_twice():
    graphql, calls = _graphql(
        nodes=[{"id": "u_old", "body": pulse.MARKER.format(tag="v2.1.9") + "\nold"}]
    )
    out = pulse.publish("v2.1.9", NOTE, token="lin_t", project_id=PROJECT,
                        repo=REPO, graphql=graphql)
    # Colleagues read this feed. Two entries for one release is noise.
    assert calls[1]["variables"]["id"] == "u_old"
    assert "updated" in out


def test_an_entry_for_a_different_release_is_left_alone():
    graphql, calls = _graphql(
        nodes=[{"id": "u_old", "body": pulse.MARKER.format(tag="v2.1.8")}]
    )
    pulse.publish("v2.1.9", NOTE, token="lin_t", project_id=PROJECT,
                  repo=REPO, graphql=graphql)
    assert "projectId" in calls[1]["variables"], "should have created, not updated"


# ── Never failing a release ───────────────────────────────────────────

@pytest.mark.parametrize("kwargs,reason", [
    ({"token": "", "project_id": PROJECT}, "LINEAR_API_KEY"),
    ({"token": "lin_t", "project_id": ""}, "LINEAR_PROJECT_ID"),
])
def test_missing_configuration_skips_rather_than_fails(kwargs, reason):
    graphql, calls = _graphql()
    out = pulse.publish("v2.1.9", NOTE, repo=REPO, graphql=graphql, **kwargs)
    assert out.startswith("skipped") and reason in out
    assert calls == []


def test_a_graphql_error_is_reported_not_raised():
    def graphql(query, variables, *, token):
        raise RuntimeError("Entity not found: Project")

    out = pulse.publish("v2.1.9", NOTE, token="lin_t", project_id=PROJECT,
                        repo=REPO, graphql=graphql)
    assert out.startswith("failed: RuntimeError")


def test_an_http_error_is_reported_not_raised():
    def graphql(query, variables, *, token):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    out = pulse.publish("v2.1.9", NOTE, token="lin_t", project_id=PROJECT,
                        repo=REPO, graphql=graphql)
    assert out.startswith("failed: HTTP 401")


def test_the_api_key_never_appears_in_a_returned_message():
    def graphql(query, variables, *, token):
        raise RuntimeError("rejected key lin_supersecret")

    out = pulse.publish("v2.1.9", NOTE, token="lin_supersecret", project_id=PROJECT,
                        repo=REPO, graphql=graphql)
    assert "lin_supersecret" not in out
