"""Tests for the one-shot test-plan migration splitter."""
from __future__ import annotations

import sys
from pathlib import Path

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from test_plan_split import (  # noqa: E402
    extract_facts,
    run,
    seed_allocation,
    split_clusters_md,
    split_sessions_md,
    split_writing_plan_md,
)

CLUSTERS_MD = """\
# Test Clusters

## Ordering: The User Journey

journey text.

## Testing Philosophy

golden rule text.

## 1. Init — Project Bootstrap

intro for cluster 1. Scenario 1.1 lives here.

### 1a. `lex setup` — scaffolding

body 1a.

## 2. CRUD via REST API

intro for cluster 2.
"""

WRITING_PLAN_MD = """\
# Test-Writing Plan — COMPLETE bucket (May 2026)

preamble.

## Conventions for this plan

conventions text.

## Cluster 1 — Init / Project Bootstrap (existing 1a–1n + new 1o)

### Batch 1o — Lazy imports ✅

- **Scenarios:** 1.110–1.124
- rows.

## Cluster 2 — CRUD via REST API (existing 2a–2e)

### Batch 2f — Model-entry mixins

- **Scenarios:** 2.30–2.41
- rows.

## 5. LATER bucket (deferred — keep in backlog)

later text.

## 6. Pending decisions blocking specific batches

decisions text.
"""

SESSIONS_MD = """\
# Session Log

header prose.

| Date | Session | What Was Done | Clusters Affected | Tests Added | Tests Passing |
|------|---------|---------------|-------------------|-------------|---------------|
| 2026-04-17 | 1 | Created test plan documentation | All | 0 | 0 |
| 2026-04-19 | 2 | Implemented Cluster 1 (17 tests). BUG-004 surfaced. | 1, 2 | 37 | 31 (6 expected failures) |
"""


class TestSplitClusters:
    def test_preamble_and_numbered_sections(self):
        preamble, sections = split_clusters_md(CLUSTERS_MD)
        assert "Testing Philosophy" in preamble
        assert sorted(sections) == [1, 2]
        title, body = sections[1]
        assert title == "Init — Project Bootstrap"
        assert "### 1a." in body


class TestSplitWritingPlan:
    def test_cluster_blocks_and_misc(self):
        misc, blocks = split_writing_plan_md(WRITING_PLAN_MD)
        assert sorted(blocks) == [1, 2]
        assert "Batch 1o" in blocks[1]
        assert "LATER bucket" in misc and "Pending decisions" in misc
        assert "Conventions for this plan" in misc


class TestSplitSessions:
    def test_rows_become_fragments(self):
        frags = split_sessions_md(SESSIONS_MD)
        assert len(frags) == 2
        assert frags[0]["date"] == "2026-04-17"
        assert frags[0]["session"] == 1
        assert frags[1]["clusters"] == "1, 2"
        assert "BUG-004" in frags[1]["prose"]


class TestSeedAllocation:
    def test_letters_and_max_from_batches(self):
        y = seed_allocation(1, "init", "Init — Project Bootstrap", WRITING_PLAN_MD)
        assert y["cluster"] == 1
        assert "o" in y["letters"]
        assert y["letters"]["o"]["status"] == "complete"   # ✅ marker
        assert y["max_scenario"] >= 124                     # from 1.110–1.124


class TestFacts:
    def test_fact_extraction_finds_ids_letters_bugs(self):
        facts = extract_facts(CLUSTERS_MD + WRITING_PLAN_MD + SESSIONS_MD)
        assert ("1", "1") in facts.scenario_ids       # Scenario 1.1
        assert ("1", "a") in facts.letters            # 1a heading
        assert "BUG-004" in facts.bugs
        assert "2026-04-19" in facts.dates


class TestRun:
    def _write_monoliths(self, plan_dir, sessions_md):
        (plan_dir / "progress").mkdir(parents=True)
        (plan_dir / "test-clusters.md").write_text(CLUSTERS_MD)
        (plan_dir / "test-writing-plan.md").write_text(WRITING_PLAN_MD)
        (plan_dir / "progress" / "session-log.md").write_text(sessions_md)
        (plan_dir / "progress" / "dashboard.md").write_text("# dash\n")

    def test_lossy_input_hard_fails_and_writes_nothing(self, tmp_path):
        # A malformed row (bad date cell) fails SESSION_ROW_RE, so its unique
        # facts (BUG-999, 2026-05-05 inside the prose) never reach the planned
        # tree — run() must return 1 and write NOTHING even with apply=True.
        lossy = SESSIONS_MD + "| 2026 | 3 | BUG-999 surfaced on 2026-05-05. | 1 | 1 | 1 |\n"
        plan = tmp_path / "plan"
        self._write_monoliths(plan, lossy)
        assert run(plan, apply=True) == 1
        assert not (plan / "clusters").exists()
        assert not (plan / "testing-philosophy.md").exists()
        assert not (plan / "progress" / "sessions").exists()

    def test_clean_input_applies(self, tmp_path):
        plan = tmp_path / "plan"
        self._write_monoliths(plan, SESSIONS_MD)
        assert run(plan, apply=True) == 0
        assert (plan / "clusters" / "01-init" / "allocation.yaml").is_file()
        assert (plan / "clusters" / "01-init" / "cluster.md").is_file()
        assert (plan / "testing-philosophy.md").is_file()
        frags = list((plan / "progress" / "sessions").glob("*.md"))
        assert len(frags) == 2
