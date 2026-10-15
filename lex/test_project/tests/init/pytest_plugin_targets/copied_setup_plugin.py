"""Stands in for a project conftest.py that copied `lex pytest`'s setup.

The known copies guard on _TestState.saved_data and stand down when it exists.
test_1aw_pytest_plugin.py loads this through PYTEST_PLUGINS, which pytest registers after
entry-point plugins, as it registers conftests; and like a conftest's, its
pytest_configure is an ordinary hook implementation.
"""

_state = {}


def pytest_configure(config):
    from django.test.runner import DiscoverRunner
    from django.test.utils import _TestState, setup_test_environment

    if hasattr(_TestState, "saved_data"):
        print("COPIED-SETUP: stood down")
        return
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False, keepdb=True)
    _state["runner"], _state["old"] = runner, runner.setup_databases(aliases={"default"})
    print("COPIED-SETUP: set up")


def pytest_unconfigure(config):
    from django.test.utils import teardown_test_environment

    if "runner" in _state:
        _state["runner"].teardown_databases(_state["old"])
        teardown_test_environment()
