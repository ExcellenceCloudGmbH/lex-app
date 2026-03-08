# Backward-compatibility shim — all configuration lives in pyproject.toml.
# This file is only kept so that legacy tooling (pip < 21.3, editable
# installs on older setuptools, etc.) can still fall back to it.
# The custom post-install hook that previously lived here is now available
# via the CLI:  lex setup  (or:  python -m lex setup)
from setuptools import setup

setup()
