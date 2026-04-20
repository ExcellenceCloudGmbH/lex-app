"""
Project configuration for the test project.

This file mirrors the shape of a real customer's ``lex_config.py`` (see
``project_example/lex_config.py``). It is used:

1. As a **fixture** for Cluster 1 tests (Init — Project Bootstrap), which
   verify that ``lex setup`` and ``lex Init`` read these values correctly.
2. As the **source of truth** for the test project's seed data and default
   Keycloak groups.
"""

# Path (relative to project root) to the seed JSON loaded by ``lex Init``.
INITIAL_DATA = "test_project/tests/fixtures/test_seed.json"

# Default Keycloak groups registered during ``lex Init``.
PROJECT_GROUPS = ["lex_test_project"]
