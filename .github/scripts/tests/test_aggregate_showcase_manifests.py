"""Tests for aggregate_showcase_manifests.

Covers:
  * merge order follows CLUSTERS declaration order regardless of the
    order partial files are discovered;
  * overall recomputation (counts, clusters_total/passing, outcome),
    including a failing cluster → overall outcome failure;
  * coverage_pct overwrite on every row + overall;
  * missing-partial handling → hard error naming the missing cluster
    (a lost partial must NEVER silently pass the gate).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import aggregate_showcase_manifests as agg  # noqa: E402
from showcase_clusters import CLUSTERS  # noqa: E402


def _row(key, *, outcome="success", passed=1, failed=0, errors=0,
         skipped=0, xfailed=0, wall_s=1.0):
    ran = passed + failed + errors + skipped + xfailed
    return {
        "key": key,
        "label": f"{key} label",
        "test_suffix": None,
        "ran": ran,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "wall_s": wall_s,
        "outcome": outcome,
        "tests": [],
        "coverage_pct": None,
    }


def _write_partial(dir_path: Path, name: str, rows: list[dict]) -> None:
    manifest = {"generated_at": 0, "overall": {}, "clusters": rows}
    (dir_path / name).write_text(json.dumps(manifest), encoding="utf-8")


# ── merge ordering ─────────────────────────────────────────────────
def test_merge_order_follows_cluster_declaration_order(tmp_path) -> None:
    # Write partials whose filenames sort in REVERSE cluster order.
    _write_partial(tmp_path, "manifest.partial.z.json", [_row("crud_api")])
    _write_partial(tmp_path, "manifest.partial.a.json", [_row("init")])
    rows = agg._load_partials(tmp_path)
    manifest = agg.aggregate(rows, coverage_pct=None)
    keys = [c["key"] for c in manifest["clusters"]]
    # init is declared before crud_api in CLUSTERS regardless of file order.
    assert keys == ["init", "crud_api"]


def test_recursive_partial_discovery(tmp_path) -> None:
    # download-artifact makes one subdir per artifact.
    sub1 = tmp_path / "manifest-partial-init"
    sub2 = tmp_path / "manifest-partial-crud_api"
    sub1.mkdir()
    sub2.mkdir()
    _write_partial(sub1, "manifest.partial.json", [_row("init")])
    _write_partial(sub2, "manifest.partial.json", [_row("crud_api")])
    rows = agg._load_partials(tmp_path)
    assert set(rows) == {"init", "crud_api"}


# ── overall recomputation ──────────────────────────────────────────
def test_overall_sums_counts_all_passing() -> None:
    rows = {
        "init": _row("init", passed=3, skipped=1, wall_s=2.0),
        "crud_api": _row("crud_api", passed=5, xfailed=1, wall_s=4.5),
    }
    manifest = agg.aggregate(rows, coverage_pct=87.5)
    ov = manifest["overall"]
    assert ov["passed"] == 8
    assert ov["skipped"] == 1
    assert ov["xfailed"] == 1
    assert ov["ran"] == rows["init"]["ran"] + rows["crud_api"]["ran"]
    assert ov["wall_s"] == 6.5
    assert ov["clusters_total"] == 2
    assert ov["clusters_passing"] == 2
    assert ov["outcome"] == "success"
    assert ov["coverage_pct"] == 87.5


def test_one_failing_cluster_makes_overall_failure() -> None:
    rows = {
        "init": _row("init", passed=3),
        "crud_api": _row("crud_api", outcome="failure", passed=2, failed=1),
    }
    manifest = agg.aggregate(rows, coverage_pct=None)
    ov = manifest["overall"]
    assert ov["failed"] == 1
    assert ov["clusters_passing"] == 1
    assert ov["clusters_total"] == 2
    assert ov["outcome"] == "failure"


def test_errored_cluster_also_makes_overall_failure() -> None:
    rows = {
        "init": _row("init", outcome="failure", errors=1, passed=0),
    }
    manifest = agg.aggregate(rows, coverage_pct=None)
    assert manifest["overall"]["outcome"] == "failure"
    assert manifest["overall"]["clusters_passing"] == 0


# ── coverage_pct overwrite ─────────────────────────────────────────
def test_coverage_pct_overwrites_every_row_and_overall() -> None:
    rows = {
        "init": _row("init"),
        "crud_api": _row("crud_api"),
    }
    manifest = agg.aggregate(rows, coverage_pct=42.0)
    assert manifest["overall"]["coverage_pct"] == 42.0
    for row in manifest["clusters"]:
        assert row["coverage_pct"] == 42.0


# ── manifest shape ─────────────────────────────────────────────────
def test_manifest_has_expected_top_level_keys() -> None:
    manifest = agg.aggregate({"init": _row("init")}, coverage_pct=None)
    assert set(manifest) == {"generated_at", "overall", "clusters"}
    assert isinstance(manifest["generated_at"], int)


# ── missing-partial → hard error via main() ────────────────────────
def test_missing_expected_partial_is_hard_error(tmp_path, capsys) -> None:
    _write_partial(tmp_path, "manifest.partial.init.json", [_row("init")])
    out = tmp_path / "manifest.json"
    rc = agg.main([
        "--partials", str(tmp_path),
        "--out", str(out),
        "--expected", "init,crud_api",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "crud_api" in err
    assert "::error::" in err
    # No manifest written on a hard error → gate can't accidentally pass.
    assert not out.exists()


def test_all_expected_present_succeeds(tmp_path) -> None:
    _write_partial(tmp_path, "manifest.partial.init.json", [_row("init")])
    _write_partial(tmp_path, "manifest.partial.crud.json", [_row("crud_api")])
    out = tmp_path / "manifest.json"
    rc = agg.main([
        "--partials", str(tmp_path),
        "--out", str(out),
        "--expected", "init,crud_api",
    ])
    assert rc == 0
    written = json.loads(out.read_text())
    assert [c["key"] for c in written["clusters"]] == ["init", "crud_api"]


def test_no_partials_at_all_is_hard_error(tmp_path, capsys) -> None:
    out = tmp_path / "manifest.json"
    rc = agg.main(["--partials", str(tmp_path), "--out", str(out)])
    assert rc == 1
    assert "No partial manifests" in capsys.readouterr().err
    assert not out.exists()


def test_declaration_order_is_stable_property() -> None:
    # Sanity: init precedes crud_api in the source of truth.
    keys = [c.key for c in CLUSTERS]
    assert keys.index("init") < keys.index("crud_api")
