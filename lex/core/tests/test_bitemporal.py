"""Re-export shim — canonical source moved to lex.tests.integration.test_bitemporal.

This file remains so that existing test labels like
    python -m django test lex.core.tests.test_bitemporal
continue to work.  Edit the canonical copy, not this shim.
"""
from lex.tests.integration.test_bitemporal import BitemporalLogicTest  # noqa: F401
