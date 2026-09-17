"""The doc gates must scan the package, and only the package.

Both gates glob `lex/**`. On a clean CI checkout that is the package and nothing
else, which is why the omission survived: the scan was correct in the only place
it ever ran. On a working copy `lex/` also holds `.venv-test` (12k files of
site-packages) and `.claude/worktrees` (13k more), and the gates reported
`ARROW_HOME` as framework configuration and `lex collectstatic` as an
undocumented command -- Django's own, found in site-packages.

The consequence is worse than local noise: anything that ever vendors Python
under `lex/` turns CI red with findings about somebody else's library.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tree(root: Path) -> None:
    """A package with the three intruders a working copy actually grows."""
    (root / "core").mkdir(parents=True)
    (root / "core" / "real.py").write_text("import os\nos.getenv('LEX_REAL_ONE')\n")
    (root / "core" / "management" / "commands").mkdir(parents=True)
    (root / "core" / "management" / "commands" / "real_command.py").write_text("")

    for intruder in (".venv-test/lib/python3.12/site-packages/pyarrow",
                     ".claude/worktrees/wt/lex/core",
                     "node_modules/pkg"):
        d = root / intruder
        d.mkdir(parents=True)
        (d / "vendored.py").write_text("import os\nos.getenv('ARROW_HOME')\n")
        cmds = d / "management" / "commands"
        cmds.mkdir(parents=True)
        (cmds / "collectstatic.py").write_text("")


def test_env_var_scan_skips_everything_that_is_not_the_package(tmp_path):
    _tree(tmp_path)
    found = {p.name for p in _load("check_doc_env_vars").package_sources(tmp_path)}
    # Both package files, and neither intruder. The management-command module is
    # a package source like any other -- what must not appear is `vendored.py`.
    assert found == {"real.py", "real_command.py"}, (
        f"scanned outside the package: {sorted(found)}"
    )


def test_command_scan_skips_everything_that_is_not_the_package(tmp_path):
    _tree(tmp_path)
    found = {p.stem for p in _load("check_doc_commands").management_commands(tmp_path)}
    assert found == {"real_command"}, f"scanned outside the package: {sorted(found)}"


def test_a_venv_inside_the_package_does_not_change_the_answer(tmp_path):
    """The property that matters, stated directly.

    Deleting the venv and re-running must not change what the gate reports. That
    is the invariant the two tests above protect, and the one that was broken:
    the same command gave 126 variables in CI and 467 on a laptop.
    """
    _tree(tmp_path)
    env_vars = _load("check_doc_env_vars")
    with_venv = {p.name for p in env_vars.package_sources(tmp_path)}

    import shutil
    shutil.rmtree(tmp_path / ".venv-test")
    shutil.rmtree(tmp_path / ".claude")
    shutil.rmtree(tmp_path / "node_modules")
    without = {p.name for p in env_vars.package_sources(tmp_path)}

    assert with_venv == without
