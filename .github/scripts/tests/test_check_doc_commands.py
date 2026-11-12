"""The docs command gate knows every command a reader can actually run -- and only those.

Two ways it was wrong, both found while documenting lex-app 2.5's long-lived sessions:

* `lex oidc_check`, `lex oidc_cleanup`, `lex oidc_rotate_keys`, `lex oidc_urls` and
  `lex clearsessions` work -- the CLI forwards every Django command, and these come with
  django-oauth2-codeflow and `django.contrib.sessions`, both installed with lex -- but the gate
  enumerates commands from `lex/` and Django's core list only, so the pages that tell operators
  to run them (the internal Keycloak URL, the daily maintenance job) failed it.
* `lex/authentication/management/commands/_keycloak_env.py` is a helper the Keycloak commands
  share. Django never loads an `_`-prefixed module as a command, so there is no
  `lex _keycloak_env`, yet the coverage half of the gate demanded documentation for it.

The library commands count as existing, but they are not lex's to document: they stay out of the
coverage half, which asks only about commands lex ships.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
LIBRARY_COMMANDS = {"oidc_check", "oidc_cleanup", "oidc_rotate_keys", "oidc_urls", "clearsessions"}


def _checker():
    spec = importlib.util.spec_from_file_location("check_doc_commands", SCRIPTS / "check_doc_commands.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_doc_commands"] = module
    spec.loader.exec_module(module)
    return module


def _repo(root: Path) -> Path:
    """A lex checkout in miniature: the CLI with one command, and a commands package holding a
    real command next to the helper the Keycloak commands share."""
    (root / "lex" / "bin").mkdir(parents=True)
    (root / "lex" / "bin" / "lex.py").write_text('@lex.command(name="start")\ndef start():\n    pass\n')
    commands = root / "lex" / "authentication" / "management" / "commands"
    commands.mkdir(parents=True)
    for name in ("__init__.py", "_keycloak_env.py", "register_keycloak_resources.py"):
        (commands / name).write_text("")
    return root


def _pointed_at(root: Path, *, behind: bool = False):
    """The checker reading the miniature checkout instead of this one."""
    checker = _checker()
    checker.REPO = root
    checker.CLI_SOURCE = root / "lex" / "bin" / "lex.py"
    checker.warn_if_behind = lambda: behind
    return checker


def _page(tmp_path: Path, text: str) -> Path:
    page = tmp_path / "docs" / "page.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(text)
    return page


def test_the_commands_the_cli_forwards_from_installed_libraries_exist():
    """`lex oidc_check` & co. are commands: the CLI forwards them like Django's own."""
    assert LIBRARY_COMMANDS <= _checker().known_commands()


def test_a_page_telling_operators_to_run_them_passes(tmp_path):
    """The pages that need them -- the internal Keycloak URL, the daily maintenance job."""
    checker = _pointed_at(_repo(tmp_path))
    checker.coverage = lambda *args, **kwargs: 0  # only the "does it exist" half is under test
    page = _page(tmp_path, (
        "Check the internal URL with `lex oidc_check`; print the URLs with `lex oidc_urls`.\n"
        "Rotate keys: `lex oidc_rotate_keys`. Daily: `lex oidc_cleanup` and `lex clearsessions`.\n"
    ))
    assert checker.main(["check_doc_commands", str(page)]) == 0


def test_a_command_that_does_not_exist_still_fails(tmp_path, capsys):
    """Accepting the library's commands opened nothing else: a typo is still an error, and a
    wrong case still gets the hint."""
    checker = _pointed_at(_repo(tmp_path))
    page = _page(tmp_path, "Run `lex oidc_chek`.\nOr `lex OIDC_CHECK`.\n")
    assert checker.main(["check_doc_commands", str(page)]) == 1
    errors = capsys.readouterr().err
    assert "`lex oidc_chek` is not a command." in errors
    assert "Did you mean `lex oidc_check`?" in errors


def test_library_commands_are_not_lexs_to_document(tmp_path):
    """The coverage half asks only about commands lex ships: a docs tree that never names the
    library's commands is complete."""
    checker = _pointed_at(_repo(tmp_path))
    page = _page(tmp_path, "Run `lex start`, then `lex register_keycloak_resources`.\n")
    assert checker.coverage([page], checker.known_commands()) == 0


def test_a_helper_module_in_a_commands_package_is_not_a_command(tmp_path):
    """Django does not load `_`-prefixed modules as commands, so neither does the gate."""
    checker = _pointed_at(_repo(tmp_path))
    found = {path.stem for path in checker.management_commands(tmp_path / "lex")}
    assert found == {"register_keycloak_resources"}
    assert "_keycloak_env" not in checker.known_commands()


def test_coverage_does_not_demand_docs_for_a_helper_module(tmp_path, capsys):
    """The report that had no fix: documentation demanded for `lex _keycloak_env`."""
    checker = _pointed_at(_repo(tmp_path))
    documented = _page(tmp_path, "Run `lex start`, then `lex register_keycloak_resources`.\n")
    assert checker.coverage([documented], checker.known_commands()) == 0
    assert "_keycloak_env" not in capsys.readouterr().err

    # The half still works: a real command nobody documents is reported.
    undocumented = _page(tmp_path, "Run `lex start`.\n")
    assert checker.coverage([undocumented], checker.known_commands()) == 1
    report = capsys.readouterr().err
    assert "`lex register_keycloak_resources`" in report
    assert "_keycloak_env" not in report
