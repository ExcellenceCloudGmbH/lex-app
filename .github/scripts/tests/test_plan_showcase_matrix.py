"""Tests for plan_showcase_matrix.build_matrix (selector grammar).

The matrix planner shares its selector grammar with
``run_showcase_suite.parse_selectors`` — these tests pin the grammar:
default set, single key, ``key:suffix``, mixed CSV, ordering, and
unknown-key handling (dropped, matching the runner).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from plan_showcase_matrix import build_matrix  # noqa: E402
from showcase_clusters import CLUSTERS  # noqa: E402


def _keys(matrix):
    return [e["key"] for e in matrix]


def test_default_set_is_all_clusters_in_declaration_order() -> None:
    for selector in (None, "", "   "):
        matrix = build_matrix(selector)
        assert _keys(matrix) == [c.key for c in CLUSTERS]
        assert all(e["test_suffix"] is None for e in matrix)


def test_default_set_includes_gate_selftest() -> None:
    # Blank input = every registered cluster, including the always-fail
    # gate_selftest. Excluding it is the *caller's* job (the default
    # selector strings in the workflow leave it out) — not the planner's.
    assert "gate_selftest" in _keys(build_matrix(None))


def test_single_key() -> None:
    matrix = build_matrix("init")
    assert matrix == [{"key": "init", "test_suffix": None}]


def test_key_with_test_suffix() -> None:
    matrix = build_matrix(
        "init:test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init"
    )
    assert matrix == [{
        "key": "init",
        "test_suffix": "test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init",
    }]


def test_mixed_csv_preserves_declaration_order_not_input_order() -> None:
    # Input lists queries before init, but CLUSTERS declares init first.
    matrix = build_matrix("queries,init:test_x.C.test_y,crud_api")
    assert _keys(matrix) == ["init", "crud_api", "queries"]
    by_key = {e["key"]: e["test_suffix"] for e in matrix}
    assert by_key["init"] == "test_x.C.test_y"
    assert by_key["crud_api"] is None
    assert by_key["queries"] is None


def test_whitespace_and_blank_entries_are_ignored() -> None:
    matrix = build_matrix(" init , , crud_api ")
    assert _keys(matrix) == ["init", "crud_api"]


def test_empty_suffix_after_colon_is_none() -> None:
    matrix = build_matrix("init:")
    assert matrix == [{"key": "init", "test_suffix": None}]


def test_unknown_key_is_dropped(capsys) -> None:
    matrix = build_matrix("init,does_not_exist,crud_api")
    assert _keys(matrix) == ["init", "crud_api"]
    err = capsys.readouterr().err
    assert "does_not_exist" in err
    assert "::warning::" in err


def test_only_unknown_keys_yields_empty_matrix() -> None:
    assert build_matrix("nope,also_nope") == []


def test_duplicate_key_last_suffix_wins() -> None:
    # parse_selectors is a dict build; a repeated key keeps the last value.
    matrix = build_matrix("init,init:test_x.C.test_y")
    assert matrix == [{"key": "init", "test_suffix": "test_x.C.test_y"}]
