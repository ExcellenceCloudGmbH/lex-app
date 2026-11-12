"""Raises a UserWarning from a test.

Whether pytest reports it is decided by the warning policy the lex settings install
when they are imported -- `LEX_SUPPRESS_WARNINGS`, on unless set to False.
"""

import warnings

import pytest

pytestmark = pytest.mark.init


def test_raises_a_user_warning():
    warnings.warn("lex-1aw-warning-probe", UserWarning)
