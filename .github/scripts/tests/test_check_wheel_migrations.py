"""Tests for the check that migrates an empty database from the built wheel.

Every test the release gate runs uses an editable install, which reads the
source tree. 2.2.2, 2.3.0 and 2.3.1 shipped the core migration without the SQL
file it executes: the suite passed, and `lex init` stopped at migrate on every
instance that installed them. The check installs the wheel where no source tree
can help it and runs the database half of `lex init`. These tests pin how it
does that without a database or a network: the order of the steps, where `lex`
must come from, and what a failure tells the reader.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import check_wheel_migrations as check  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _wheel(dist: Path, name: str = "lex_app-9.9.9-py3-none-any.whl") -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    path = dist / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lex_app-9.9.9.dist-info/METADATA", "Name: lex-app\nVersion: 9.9.9")
    return path


class FakeRunner:
    """Stands in for subprocess.run: records every command, fails on request.

    ``fail_on`` names the lex subcommand (its arguments joined by spaces) that
    exits 1 with ``failure_output``; ``lex_file`` is what ``import lex`` reports.
    """

    def __init__(self, *, fail_on: str | None = None, failure_output: str = "",
                 lex_file: str | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.fail_on = fail_on
        self.failure_output = failure_output
        self.lex_file = lex_file
        self.models_written_before: dict[str, bool] = {}

    def __call__(self, cmd, **kwargs):
        cmd = [str(part) for part in cmd]
        self.calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cwd is not None:
            self.models_written_before[" ".join(cmd[1:])] = (Path(cwd) / check.MODELS_FILE).exists()
        if cmd[-1].startswith("import lex"):
            venv = Path(cmd[0]).parents[1]
            where = self.lex_file or str(venv / "lib" / "python3.12" / "site-packages" / "lex" / "__init__.py")
            return subprocess.CompletedProcess(cmd, 0, stdout=where + "\n", stderr="")
        if Path(cmd[0]).name == "lex" and " ".join(cmd[1:]) == self.fail_on:
            return subprocess.CompletedProcess(cmd, 1, stdout=self.failure_output, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    def lex_commands(self) -> list[str]:
        return [" ".join(cmd[1:]) for cmd, _ in self.calls if Path(cmd[0]).name == "lex"]


def _run(tmp_path: Path, runner: FakeRunner, capsys, *extra: str):
    dist = tmp_path / "dist"
    if not dist.exists():
        _wheel(dist)
    code = check.main(["--dist", str(dist), "--workdir", str(tmp_path / "work"), *extra], run=runner)
    return code, capsys.readouterr().out


# ── The project the check builds ──────────────────────────────────────────

def test_the_project_name_is_one_settings_turns_into_an_app():
    """lex_app.settings adds the project folder as an app unless its name
    starts with "lex"; a name it skipped would leave the models unmigrated."""
    names = {check.project_name() for _ in range(20)}
    assert len(names) == 20, "Each run needs its own project, so its own database."
    for name in names:
        assert not name.startswith("lex")
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name


def test_the_database_name_follows_the_default_target():
    """The `default` target names its database db_<project folder, lowercased>."""
    assert check.database_name("wheelcheck_ab12") == "db_wheelcheck_ab12"
    settings = (REPO_ROOT / "lex/lex_app/settings.py").read_text()
    assert '"NAME": f"db_{repo_name.lower()}"' in settings, (
        "settings.py no longer names the default database db_<project>; "
        "update database_name() to match."
    )


def test_the_postgres_credentials_are_the_default_targets():
    """The check connects the way an installed lex-app does with the `default`
    target, so its credentials have to be the ones settings.py hardcodes."""
    settings = (REPO_ROOT / "lex/lex_app/settings.py").read_text()
    block = settings[settings.index('"default": {', settings.index("DATABASES = {")):]
    block = block[: block.index("},")]
    for key, value in check.DEFAULT_TARGET.items():
        assert f'"{key.upper()}": "{value}"' in block, f"{key} drifted from settings.py"


def test_the_models_file_is_one_project_discovery_imports():
    """Discovery skips files starting with _ or test_ and files ending in _;
    a skipped file would leave the project with no model to migrate."""
    stem = Path(check.MODELS_FILE).stem
    assert check.MODELS_FILE.endswith(".py")
    assert not stem.startswith(("_", ".", "test_")) and not stem.endswith(("_", "_test"))
    assert "(LexModel)" in check.MODELS_SOURCE and "(CalculationModel)" in check.MODELS_SOURCE


def test_the_child_environment_cannot_reach_a_source_tree(tmp_path):
    venv, project = tmp_path / "venv", tmp_path / "wheelcheck_x"
    env = check.child_env(
        {"PATH": "/usr/bin", "PYTHONPATH": "/repo", "PYTHONHOME": "/x", "PROJECT_ROOT": "/repo",
         "DATABASE_DEPLOYMENT_TARGET": "local", "HOME": "/home/u"},
        venv, project,
    )
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
    assert env["PATH"].split(":")[0] == str(check.bin_dir(venv)), "The venv's lex must win over /usr/bin/lex (flex)."
    assert env["PROJECT_ROOT"] == str(project)
    assert env["DJANGO_SETTINGS_MODULE"] == "lex_app.settings"
    assert env["DATABASE_DEPLOYMENT_TARGET"] == "default"
    assert env["CELERY_ACTIVE"] == "False"
    assert env["HOME"] == "/home/u"


def test_installed_in_tells_the_venv_from_a_source_tree(tmp_path):
    venv = tmp_path / "venv"
    assert check.installed_in(venv / "lib/python3.12/site-packages/lex/__init__.py", venv)
    assert not check.installed_in(REPO_ROOT / "lex/__init__.py", venv)


# ── What main() does, in order ────────────────────────────────────────────

def test_the_steps_run_in_the_order_lex_init_runs_them(tmp_path, capsys):
    runner = FakeRunner()
    code, out = _run(tmp_path, runner, capsys)

    assert code == 0, out
    assert runner.lex_commands() == [
        "create_db",
        "makemigrations --check --dry-run",
        "createcachetable",
        "makemigrations --noinput",
        "migrate --noinput",
        "migrate --check",
    ]
    assert "OK" in out


def test_the_framework_is_checked_before_the_project_has_a_model(tmp_path, capsys):
    """Before the project has a model, `makemigrations --check` can only find
    framework drift: a migration the wheel left out. After it, the project's
    own first migration would be reported too."""
    runner = FakeRunner()
    _run(tmp_path, runner, capsys)

    assert runner.models_written_before["makemigrations --check --dry-run"] is False
    assert runner.models_written_before["makemigrations --noinput"] is True


def test_the_wheel_is_installed_with_its_dependencies_into_a_fresh_venv(tmp_path, capsys):
    runner = FakeRunner()
    _run(tmp_path, runner, capsys)

    commands = [cmd for cmd, _ in runner.calls]
    assert commands[0][1:3] == ["-m", "venv"]
    install = commands[1]
    assert install[1:4] == ["-m", "pip", "install"]
    assert install[-1].endswith(".whl") and "--no-deps" not in install


def test_every_lex_command_runs_in_the_project_outside_the_checkout(tmp_path, capsys):
    runner = FakeRunner()
    _run(tmp_path, runner, capsys)

    for cmd, kwargs in runner.calls:
        if Path(cmd[0]).name == "lex":
            cwd = Path(kwargs["cwd"])
            assert cwd.name.startswith(check.PROJECT_PREFIX)
            assert REPO_ROOT not in cwd.parents
            assert kwargs["env"]["PROJECT_ROOT"] == str(cwd)


# ── What it refuses ───────────────────────────────────────────────────────

def test_a_lex_imported_from_a_source_tree_is_refused(tmp_path, capsys):
    """The whole point is to test the wheel; a lex that comes from a checkout
    would make every step pass for the wrong reason."""
    runner = FakeRunner(lex_file=str(REPO_ROOT / "lex" / "__init__.py"))
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "not from the installed wheel" in out
    assert runner.lex_commands() == [], "Nothing may run against a source tree."


def test_a_workdir_inside_the_checkout_is_refused(tmp_path, capsys):
    dist = tmp_path / "dist"
    _wheel(dist)
    code = check.main(["--dist", str(dist), "--workdir", str(REPO_ROOT / "build" / "wheelcheck")],
                      run=FakeRunner())
    out = capsys.readouterr().out

    assert code == 1
    assert "outside" in out


@pytest.mark.parametrize("wheels", [0, 2])
def test_anything_but_exactly_one_wheel_is_refused(tmp_path, capsys, wheels):
    dist = tmp_path / "dist"
    dist.mkdir()
    for n in range(wheels):
        _wheel(dist, f"lex_app-9.9.{n}-py3-none-any.whl")
    runner = FakeRunner()
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "exactly one wheel" in out
    assert runner.calls == []


# ── What a failure says ───────────────────────────────────────────────────

def test_a_failing_migrate_names_the_step_and_shows_why(tmp_path, capsys):
    trace = (
        "Applying core.0001_bitemporal_activation...Traceback (most recent call last):\n"
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/venv/lib/python3.12/site-packages/lex/core/sql/bitemporal_activation.sql'\n"
    )
    runner = FakeRunner(fail_on="migrate --noinput", failure_output=trace)
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "::error" in out
    assert "apply every migration" in out
    assert "bitemporal_activation.sql" in out, "The reader needs the line that says what broke."
    assert "package-data" in out, "A missing file is a packaging fix; say where it goes."
    assert "migrate --check" not in runner.lex_commands(), "Stop at the first failing step."


def _site_packages(tmp_path: Path) -> Path:
    """Where the fake venv's packages live: the one _run builds under work/venv."""
    return tmp_path / "work" / "venv" / "lib" / "python3.12" / "site-packages"


def _planned(app: str, path: Path, change: str = "~ Alter field id on x") -> str:
    """One app's block of `makemigrations --check --dry-run` output, as Django prints it."""
    return f"Migrations for '{app}':\n  {path}\n    {change}\n"


def test_the_two_packages_lex_now_supplies_are_not_waved_through():
    """lex supplies what oauth2_authcodeflow and the api-key fork lacked
    (lex/lex_app/vendored); listing them again would hide a regression."""
    assert "oauth2_authcodeflow" not in check.KNOWN_THIRD_PARTY_DRIFT
    assert "rest_framework_api_key" not in check.KNOWN_THIRD_PARTY_DRIFT


def test_planned_migrations_reads_what_django_would_write():
    output = (
        "Skipping migration optimization for oauth2_authcodeflow\n"
        + _planned("oauth2_authcodeflow", Path("/v/site-packages/oauth2_authcodeflow/migrations/0006_a.py"))
        + _planned("audit_logging", Path("/v/site-packages/lex/audit_logging/migrations/0008_b.py"), "+ Add field y")
        + "INFO 2026-10-12 Registering 3 models from wheelcheck_x\n"
    )
    assert check.planned_migrations(output) == {
        "oauth2_authcodeflow": ["/v/site-packages/oauth2_authcodeflow/migrations/0006_a.py"],
        "audit_logging": ["/v/site-packages/lex/audit_logging/migrations/0008_b.py"],
    }


def test_framework_drift_in_the_wheel_fails(tmp_path, capsys):
    """A lex app whose migration is missing from the wheel: `lex init` would
    write one into site-packages on every customer machine."""
    path = _site_packages(tmp_path) / "lex" / "audit_logging" / "migrations" / "0008_auto.py"
    runner = FakeRunner(fail_on="makemigrations --check --dry-run",
                        failure_output=_planned("audit_logging", path, "+ Add field x"))
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "framework migrations" in out
    assert "audit_logging" in out
    assert "Commit and ship the migration" in out
    assert runner.models_written_before.get("makemigrations --noinput") is None, "The project steps must not run."


def test_known_third_party_drift_warns_and_carries_on(tmp_path, capsys, monkeypatch):
    """A package the team has decided to live with is reported on every run,
    with its reason, and does not block the check of lex's own migrations."""
    monkeypatch.setattr(check, "KNOWN_THIRD_PARTY_DRIFT", {"somepkg": "it is waiting on upstream"})
    packages = _site_packages(tmp_path)
    output = "".join(
        _planned(app, packages / app / "migrations" / "0099_drift.py") for app in check.KNOWN_THIRD_PARTY_DRIFT
    )
    runner = FakeRunner(fail_on="makemigrations --check --dry-run", failure_output=output)
    code, out = _run(tmp_path, runner, capsys)

    assert code == 0, out
    assert "::warning" in out
    for app in check.KNOWN_THIRD_PARTY_DRIFT:
        assert app in out
    assert "site-packages" in out
    assert runner.lex_commands()[-1] == "migrate --check", "The init steps must still run."


def test_new_third_party_drift_fails(tmp_path, capsys):
    """Any other package starting to drift fails: one more migration written
    into site-packages on every customer machine is a regression."""
    path = _site_packages(tmp_path) / "someapp" / "migrations" / "0002_alter_thing_id.py"
    runner = FakeRunner(fail_on="makemigrations --check --dry-run", failure_output=_planned("someapp", path))
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "someapp" in out
    assert "KNOWN_THIRD_PARTY_DRIFT" in out


def test_a_failing_check_that_plans_no_migration_fails(tmp_path, capsys):
    runner = FakeRunner(fail_on="makemigrations --check --dry-run",
                        failure_output="Traceback (most recent call last):\nImproperlyConfigured: no\n")
    code, out = _run(tmp_path, runner, capsys)

    assert code == 1
    assert "ImproperlyConfigured" in out


def test_known_drift_that_is_gone_asks_to_be_removed(tmp_path, capsys, monkeypatch):
    """Once a package is fixed, its entry must go, or a real regression in it
    would later be waved through as known."""
    monkeypatch.setattr(check, "KNOWN_THIRD_PARTY_DRIFT", {"somepkg": "it is waiting on upstream"})
    code, out = _run(tmp_path, FakeRunner(), capsys)

    assert code == 0
    assert "no longer" in out
    for app in check.KNOWN_THIRD_PARTY_DRIFT:
        assert app in out


def test_the_workdir_is_removed_unless_kept(tmp_path, capsys):
    _run(tmp_path, FakeRunner(), capsys)
    assert not (tmp_path / "work").exists()

    _run(tmp_path, FakeRunner(), capsys, "--keep")
    assert (tmp_path / "work").exists()


# ── Wired into the release ────────────────────────────────────────────────

def _publish_steps() -> list[dict]:
    workflow = yaml.safe_load((WORKFLOWS / "pip_publish.yml").read_text())
    return workflow["jobs"]["publish"]["steps"]


def _index(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in str(step.get("run", "")) or needle in str(step.get("uses", "")):
            return i
    raise AssertionError(f"pip_publish.yml's publish job has no step running {needle!r}")


def test_every_wheel_guard_runs_before_publishing():
    """check_wheel_runtime_files.py was written after the SQL incident and never
    wired in: its own tests passed while no workflow ran it. Every wheel guard
    in .github/scripts has to run on the built dist before the upload."""
    steps = _publish_steps()
    build = _index(steps, "python -m build")
    publish = _index(steps, "pypa/gh-action-pypi-publish")
    guards = sorted(p.name for p in SCRIPTS_DIR.glob("check_wheel_*.py"))
    assert "check_wheel_migrations.py" in guards and "check_wheel_runtime_files.py" in guards
    for guard in guards:
        at = _index(steps, guard)
        assert build < at < publish, f"{guard} must run after the build and before the upload."
        assert "--dist dist" in steps[at]["run"], f"{guard} must check the dist that gets uploaded."
        assert "if" not in steps[at], f"{guard} must run on dry runs too."


def test_the_publish_job_has_the_database_the_check_needs():
    workflow = yaml.safe_load((WORKFLOWS / "pip_publish.yml").read_text())
    postgres = workflow["jobs"]["publish"]["services"]["postgres"]
    assert postgres["env"]["POSTGRES_USER"] == check.DEFAULT_TARGET["user"]
    assert postgres["env"]["POSTGRES_PASSWORD"] == check.DEFAULT_TARGET["password"]
    assert any(str(port).startswith(f"{check.DEFAULT_TARGET['port']}:") for port in postgres["ports"])


def test_the_prerelease_gate_counts_the_wheel_check():
    """A prerelease whose wheel cannot migrate must not be stamped as validated,
    and must be cleaned up like one whose tests failed."""
    gate = yaml.safe_load((WORKFLOWS / "prerelease_gate.yml").read_text())["jobs"]
    job = next(name for name, spec in gate.items() if "wheel_migrations.yml" in str(spec.get("uses", "")))
    assert job in gate["mark-validated"]["needs"]
    assert job in gate["mark-validated"]["if"] and "success" in gate["mark-validated"]["if"]
    assert job in gate["cleanup-on-red"]["needs"]
    assert job in gate["cleanup-on-red"]["if"] and "failure" in gate["cleanup-on-red"]["if"]


def test_pull_requests_that_touch_migrations_or_packaging_run_the_check():
    workflow = yaml.safe_load((WORKFLOWS / "wheel_migrations.yml").read_text())
    triggers = workflow[True] if True in workflow else workflow["on"]  # yaml reads `on:` as True
    paths = triggers["pull_request"]["paths"]
    for needed in ("lex/**/migrations/**", "lex/**/sql/**", "pyproject.toml", "MANIFEST.in",
                   "requirements.txt", ".github/scripts/check_wheel_migrations.py"):
        assert needed in paths, needed
    assert "workflow_call" in triggers, "The prerelease gate calls it."
    steps = " ".join(str(s.get("run", "")) for s in workflow["jobs"]["wheel-migrations"]["steps"])
    assert "python -m build" in steps, "Build the way the release does: the sdist first, the wheel from it."
    assert "check_wheel_runtime_files.py --dist dist" in steps
    assert "check_wheel_migrations.py --dist dist" in steps


def test_the_wiring_tests_run_when_the_workflows_change():
    """The tests above read the workflows. If editing a workflow did not run
    them, a guard step could be deleted with nothing going red."""
    workflow = yaml.safe_load((WORKFLOWS / "scripts_tests.yml").read_text())
    triggers = workflow[True] if True in workflow else workflow["on"]
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        for name in ("pip_publish.yml", "prerelease_gate.yml", "wheel_migrations.yml"):
            assert f".github/workflows/{name}" in paths, (event, name)
