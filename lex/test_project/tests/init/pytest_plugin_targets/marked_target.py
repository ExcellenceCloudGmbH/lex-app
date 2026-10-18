"""Carries `init`, a group this repository's lex_test_config.yaml declares."""

import pytest

pytestmark = pytest.mark.init


def test_marked():
    assert True
