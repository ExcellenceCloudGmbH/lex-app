"""Re-export shim — canonical source moved to lex.tests.integration.test_event_scheduling.

Also re-exports SchedTestModel so that any code doing
    from lex.core.tests.test_event_scheduling import SchedTestModel
continues to work.
"""
from lex.tests.integration.test_event_scheduling import *  # noqa: F401,F403
from lex.tests.integration.test_event_scheduling import SchedTestModel  # noqa: F401,F811
