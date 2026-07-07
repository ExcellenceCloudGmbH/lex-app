"""Tests for the test-plan aggregates tool (build / check / validate).

Fixture strategy: build a miniature sharded test-plan + test tree in tmp_path,
then run the real functions against it. No repo files are touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from test_plan_aggregates import (  # noqa: E402
    AllocationError,
    build_dashboard,
    check_dashboard,
    load_clusters,
    validate_allocations,
)


def make_plan(tmp_path: Path) -> tuple[Path, Path]:
    """Two clusters (7 owns two test dirs) + a matching test tree."""
    plan = tmp_path / "test-plan"
    tests = tmp_path / "tests"
    c1 = plan / "clusters" / "01-init"
    c7 = plan / "clusters" / "07-calculations"
    c1.mkdir(parents=True)
    c7.mkdir(parents=True)
    (plan / "progress").mkdir()
    c1.joinpath("allocation.yaml").write_text(
        "cluster: 1\n"
        "slug: init\n"
        "title: Init — Project Bootstrap\n"
        "max_scenario: 20\n"
        "letters:\n"
        "  a:\n"
        "    title: lex setup scaffolding\n"
        "    scenarios: 1.1-1.7\n"
        "    status: complete\n"
        "    tests: {pass: 7, skip: 0, xfail: 0}\n"
        "    note: ''\n"
    )
    c7.joinpath("allocation.yaml").write_text(
        "cluster: 7\n"
        "slug: calculations\n"
        "title: Calculation State Machine\n"
        "test_dirs: [calculations, calculation_logging]\n"
        "max_scenario: 12\n"
        "letters:\n"
        "  a:\n"
        "    title: Atomic happy path\n"
        "    scenarios: 7.1-7.12\n"
        "    status: complete\n"
        "    tests: {pass: 11, skip: 0, xfail: 1}\n"
        "    note: BUG-001\n"
    )
    for d in ("init", "calculations", "calculation_logging", "fixtures"):
        (tests / d).mkdir(parents=True)
    (tests / "init" / "test_1a_setup.py").write_text(
        '"""Scenario 1.1: scaffolding.\nScenario 1.7: idempotent."""\n'
    )
    (tests / "calculations" / "test_7a_atomic.py").write_text(
        '"""Scenario 7.1: IN_PROGRESS write.\nScenario 7.12: error capture."""\n'
    )
    return plan, tests


class TestLoad:
    def test_loads_clusters_sorted_by_number(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        clusters = load_clusters(plan)
        assert [c.number for c in clusters] == [1, 7]
        assert clusters[0].slug == "init"
        assert clusters[0].test_dirs == ["init"]          # defaulted from slug
        assert clusters[1].test_dirs == ["calculations", "calculation_logging"]

    def test_dir_name_must_match_yaml(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        bad = plan / "clusters" / "02-crud_api"
        bad.mkdir()
        bad.joinpath("allocation.yaml").write_text(
            "cluster: 3\nslug: crud_api\ntitle: X\nmax_scenario: 1\nletters: {}\n"
        )
        with pytest.raises(AllocationError, match="02-crud_api"):
            load_clusters(plan)

    def test_bad_status_rejected(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        y = plan / "clusters" / "01-init" / "allocation.yaml"
        y.write_text(y.read_text().replace("status: complete", "status: done"))
        with pytest.raises(AllocationError, match="status"):
            load_clusters(plan)


class TestBuildCheck:
    def test_build_writes_dashboard_with_generated_header(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        text = (plan / "progress" / "dashboard.md").read_text()
        assert "GENERATED" in text
        assert "Calculation State Machine" in text
        assert "BUG-001" in text
        assert "| 7a |" in text

    def test_check_passes_when_fresh_fails_when_stale(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        assert check_dashboard(plan) == []
        dash = plan / "progress" / "dashboard.md"
        dash.write_text(dash.read_text() + "\nmanual edit\n")
        assert check_dashboard(plan) != []

    def test_build_is_deterministic(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        first = (plan / "progress" / "dashboard.md").read_text()
        build_dashboard(plan)
        assert (plan / "progress" / "dashboard.md").read_text() == first
