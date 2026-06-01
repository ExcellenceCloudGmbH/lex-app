"""Cluster 1r tests for fast-health query-tolerant path matching.

Intent
------
Health probes frequently append query strings for source attribution.
The fast-health matcher must still recognize canonical health endpoints
when that suffix is present, without broadening to non-health paths.

Cluster 1r — scenarios 1.151–1.153. Type: U.
Covers: lex/lex_app/fast_health.py.
Run: python -m pytest lex/test_project/tests/init/test_1r_fast_health_query_matching.py -v
"""

from __future__ import annotations

import pytest

from lex.lex_app.fast_health import is_fast_health_path, match_health_request_path

pytestmark = pytest.mark.init


def test_1_151_match_health_request_path_accepts_query_suffixes() -> None:
    """Scenario 1.151: query-bearing health probe paths still match."""
    assert match_health_request_path("/health?source=k8s"), (
        "Expected '/health?source=k8s' to be recognized as a valid fast-health endpoint"
    )
    assert match_health_request_path("/api/health/?check=ready"), (
        "Expected '/api/health/?check=ready' to be recognized as a valid fast-health endpoint"
    )


def test_1_152_match_health_request_path_rejects_non_health_paths_with_query() -> None:
    """Scenario 1.152: non-health paths remain rejected even with query strings."""
    assert not match_health_request_path("/users?source=k8s"), (
        "Expected non-health path '/users?source=k8s' to remain rejected"
    )


def test_1_153_strict_and_query_tolerant_matchers_remain_distinct() -> None:
    """Scenario 1.153: strict matcher stays strict; tolerant matcher handles queries."""
    assert not is_fast_health_path("/health?source=k8s"), (
        "Expected strict matcher to reject query-bearing paths so existing strict behavior is preserved"
    )
    assert match_health_request_path("/health?source=k8s"), (
        "Expected query-tolerant matcher to accept the same probe path"
    )
