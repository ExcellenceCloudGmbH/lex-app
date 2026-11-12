"""Carries a marker lex_test_config.yaml does not declare."""

import pytest

pytestmark = pytest.mark.not_a_configured_group


def test_never_runs():
    assert True
