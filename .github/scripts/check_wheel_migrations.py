#!/usr/bin/env python3
"""Install the built lex-app wheel where no source tree can help it, and migrate.

Every test the release gate runs uses an editable install, which reads the
source tree. A migration that needs a file the wheel does not carry, or a
migration module the wheel leaves out, passes all of them and fails on every
machine that installs the release: 2.2.2, 2.3.0 and 2.3.1 shipped the core
migration without the SQL file it executes, and `lex init` stopped at migrate
on every instance that installed them.

This runs the database half of `lex init` against the wheel itself:

  1. install the wheel, with its dependencies, into a fresh virtualenv;
  2. make sure `lex` is imported from that virtualenv, not from a checkout;
  3. in a throwaway project outside any git checkout, on a database of its own:
     `lex create_db`, then `lex makemigrations --check --dry-run` while the
     project has no model, so anything it finds is a framework migration the
     wheel left out; then, with one model of each framework base,
     `lex createcachetable`, `lex makemigrations`, `lex migrate` in the order
     `lex init` runs them, and `lex migrate --check`.

`lex init` runs makemigrations for every installed app, so an installed
package whose models no longer match the migrations it ships gets a migration
written into site-packages on every customer machine, and conflicts with the
package's own next migration. The two packages that already do that are listed
in KNOWN_THIRD_PARTY_DRIFT, reported as a warning on every run; any other
package starting to drift fails the check, as does a lex app.

It needs PostgreSQL on localhost:5432 with the `default` target's credentials.
Not SQLite: the core migration does nothing on any other backend, so SQLite
passes the very release this check exists for.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Mapping

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from check_wheel_runtime_files import find_wheel  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parents[1]

# The `default` database target, as lex/lex_app/settings.py hardcodes it.
DEFAULT_TARGET = {"user": "django", "password": "lundadminlocal", "host": "localhost", "port": "5432"}

# settings.py turns the project folder into an app unless its name starts with
# "lex", and names the database db_<folder>. A fresh name is a fresh database.
PROJECT_PREFIX = "wheelcheck_"

# One model of each framework base, so the project's first migration is built
# against the installed framework the way a customer's is.
MODELS_FILE = "Notes.py"
MODELS_SOURCE = '''\
from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel


class Note(LexModel):
    text = models.CharField(max_length=200, blank=True, default="")


class NoteTotal(CalculationModel):
    total = models.IntegerField(default=0)

    def calculate(self):
        self.total += 1
'''

# Installed packages whose models already differ from the migrations they ship,
# so `lex init` writes a migration for them into site-packages. Known and tracked:
# each is reported on every run, and any other package that starts drifting fails.
KNOWN_THIRD_PARTY_DRIFT = {
    "oauth2_authcodeflow": (
        "it sets no default_auto_field, so lex's DEFAULT_AUTO_FIELD (BigAutoField) makes its "
        "implicit id a bigint its migrations never created"
    ),
    "rest_framework_api_key": (
        "exc-djangorestframework-api-key 1.0.0 widened id and hashed_key to 150 characters in "
        "its model and ships no migration for it"
    ),
}

# Before the project has a model: anything makemigrations finds is drift in the
# framework or in an installed package, never the project's own.
CREATE_DB_STEP = ("create the project's empty database", ["create_db"])
DRIFT_STEP = ("framework migrations are all in the wheel", ["makemigrations", "--check", "--dry-run"])
FRAMEWORK_STEPS = (CREATE_DB_STEP, DRIFT_STEP)
# With the project's models: the database half of `lex init`, in its order.
INIT_STEPS = (
    ("create the cache table", ["createcachetable"]),
    ("make the project's migrations", ["makemigrations", "--noinput"]),
    ("apply every migration to the empty database", ["migrate", "--noinput"]),
    ("leave nothing unapplied", ["migrate", "--check"]),
)

TAIL_LINES = 40
Runner = Callable[..., subprocess.CompletedProcess]


def project_name() -> str:
    return PROJECT_PREFIX + uuid.uuid4().hex[:10]


def database_name(project: str) -> str:
    return f"db_{project.lower()}"


def bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def child_env(base: Mapping[str, str], venv: Path, project: Path) -> dict[str, str]:
    """The environment every step runs in: the venv first on PATH (``/usr/bin/lex``
    is flex), nothing that points Python at a checkout, and the `default` target."""
    dropped = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PROJECT_ROOT",
               "DJANGO_SETTINGS_MODULE", "DATABASE_DEPLOYMENT_TARGET", "CELERY_ACTIVE"}
    env = {key: value for key, value in base.items() if key not in dropped}
    env["PATH"] = os.pathsep.join(filter(None, [str(bin_dir(venv)), base.get("PATH", "")]))
    env["VIRTUAL_ENV"] = str(venv)
    env["PROJECT_ROOT"] = str(project)
    env["DJANGO_SETTINGS_MODULE"] = "lex_app.settings"
    env["DATABASE_DEPLOYMENT_TARGET"] = "default"
    env["CELERY_ACTIVE"] = "False"
    return env


def installed_in(path: Path, venv: Path) -> bool:
    return venv.resolve() in Path(path).resolve().parents


def inside_checkout(path: Path) -> bool:
    """A project inside a git checkout resolves its root to that checkout."""
    resolved = path.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def planned_migrations(output: str) -> dict[str, list[str]]:
    """What `makemigrations --dry-run` would write: app label -> migration files."""
    planned: dict[str, list[str]] = {}
    app = None
    for line in output.splitlines():
        header = re.match(r"\s*Migrations for '([^']+)':", line)
        if header:
            app = header.group(1)
            planned.setdefault(app, [])
        elif app and line.strip().endswith(".py"):
            planned[app].append(line.strip())
    return planned


def _within(path: str, directory: Path) -> bool:
    return directory.resolve() in Path(path).resolve().parents


def _text(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _tail(result: subprocess.CompletedProcess) -> str:
    return "\n".join(_text(result).strip().splitlines()[-TAIL_LINES:])


def _fail(step: str, cmd: list[str], result: subprocess.CompletedProcess, hint: str = "") -> int:
    output = _tail(result)
    if not hint and ("FileNotFoundError" in output or "No such file or directory" in output):
        hint = (" A file the code opens is missing from the wheel: declare it under "
                "[tool.setuptools.package-data] in pyproject.toml and include it in MANIFEST.in.")
    shown = " ".join(Path(cmd[0]).name if i == 0 else part for i, part in enumerate(cmd))
    print(f"::error title=Wheel migrations failed::Step \"{step}\" failed: `{shown}` exited "
          f"{result.returncode}.{hint}")
    print(output)
    return 1


def _check_drift(step: str, cmd: list[str], result: subprocess.CompletedProcess, lex_dir: Path) -> int | None:
    """Fail on a lex app or a newly drifting package; warn on the known ones."""
    planned = planned_migrations(_text(result)) if result.returncode != 0 else {}
    if result.returncode != 0 and not planned:
        return _fail(step, cmd, result)

    framework = sorted(app for app, files in planned.items() if any(_within(f, lex_dir) for f in files))
    if framework:
        return _fail(step, cmd, result, hint=(
            f" {', '.join(framework)}: the wheel lacks a migration its models need, so `lex init` "
            "would write one into site-packages on every customer machine. Commit and ship the migration."))
    new = sorted(app for app in planned if app not in KNOWN_THIRD_PARTY_DRIFT)
    if new:
        return _fail(step, cmd, result, hint=(
            f" {', '.join(new)}: these installed packages' models no longer match the migrations they "
            "ship, so `lex init` would write migrations into site-packages on every customer machine, "
            "and the package's own next migration would conflict with them. Fix the package or the "
            "setting that causes it; list an app in KNOWN_THIRD_PARTY_DRIFT only with its reason."))

    known = sorted(planned)
    if known:
        details = "; ".join(
            f"{app} ({', '.join(Path(f).name for f in planned[app])}): {KNOWN_THIRD_PARTY_DRIFT[app]}"
            for app in known
        )
        print(f"::warning title=lex init writes migrations into site-packages::{details}. Every install "
              "gets these files, and an upgrade that ships its own migration under the same number "
              "will conflict with them.")
    gone = sorted(set(KNOWN_THIRD_PARTY_DRIFT) - set(planned))
    if gone:
        print(f"::warning title=Known drift is gone::{', '.join(gone)} no longer drift. Remove them from "
              "KNOWN_THIRD_PARTY_DRIFT in .github/scripts/check_wheel_migrations.py, so that a new drift "
              "in them fails the check.")
    print(f"  ok: {step}")
    return None


def _drop_database(python: Path, name: str, env: Mapping[str, str], run: Runner) -> None:
    """Best effort: a database per run, so nothing is left behind locally."""
    target = DEFAULT_TARGET
    code = (
        "import psycopg2, sys; "
        f"c = psycopg2.connect(dbname='postgres', user='{target['user']}', "
        f"password='{target['password']}', host='{target['host']}', port='{target['port']}'); "
        "c.autocommit = True; "
        "c.cursor().execute('DROP DATABASE IF EXISTS \"%s\"' % sys.argv[1])"
    )
    run([str(python), "-c", code, name], env=dict(env), capture_output=True, text=True, timeout=120)


def main(argv: list[str] | None = None, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default=str(REPO_ROOT / "dist"), help="Directory holding the built wheel.")
    parser.add_argument("--python", default=sys.executable, help="Interpreter for the fresh virtualenv.")
    parser.add_argument("--workdir", help="Where to put the venv and the project (default: a new temp dir).")
    parser.add_argument("--keep", action="store_true", help="Keep the workdir and the database, to debug.")
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    wheel = find_wheel(dist)
    if wheel is None:
        found = sorted(p.name for p in dist.glob("*.whl")) if dist.is_dir() else []
        print(f"::error title=No wheel to check::Expected exactly one wheel in {dist}, found "
              f"{len(found)}: {found or 'nothing'}. The build step must run before this one.")
        return 1

    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="lex-wheel-migrations-"))
    if inside_checkout(work):
        print(f"::error title=Workdir inside a checkout::{work} is inside a git checkout, so the "
              "project would resolve its root to that checkout. Use a directory outside it.")
        return 1
    work.mkdir(parents=True, exist_ok=True)

    venv = work / "venv"
    python, lex = bin_dir(venv) / "python", bin_dir(venv) / "lex"
    project = work / project_name()
    env = child_env(os.environ, venv, project)
    database_made = False
    try:
        print(f"Checking {wheel.name} in {work}")
        steps = (
            ("make a fresh virtualenv", [args.python, "-m", "venv", str(venv)], 600),
            ("install the wheel with its dependencies",
             [str(python), "-m", "pip", "install", "--quiet", "--prefer-binary", str(wheel.resolve())], 1800),
        )
        for step, cmd, timeout in steps:
            result = run(cmd, cwd=str(work), capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                return _fail(step, cmd, result)
            print(f"  ok: {step}")

        project.mkdir()
        where = run([str(python), "-c", "import lex, pathlib; print(pathlib.Path(lex.__file__).resolve())"],
                    cwd=str(project), env=env, capture_output=True, text=True, timeout=300)
        lex_file = (where.stdout or "").strip().splitlines()[-1:] or [""]
        if where.returncode != 0 or not installed_in(Path(lex_file[0]), venv):
            print(f"::error title=Wrong lex::lex was imported from {lex_file[0] or '(nowhere)'}, not from "
                  f"the installed wheel in {venv}. The check would be testing a source tree.")
            print(_tail(where))
            return 1
        print(f"  ok: lex comes from the wheel ({lex_file[0]})")

        def run_lex(step: str, lex_args: list[str]) -> int | None:
            cmd = [str(lex), *lex_args]
            result = run(cmd, cwd=str(project), env=env, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                return _fail(step, cmd, result)
            print(f"  ok: {step}")
            return None

        failed = run_lex(*CREATE_DB_STEP)
        database_made = True
        if failed is not None:
            return failed
        step, drift_args = DRIFT_STEP
        cmd = [str(lex), *drift_args]
        result = run(cmd, cwd=str(project), env=env, capture_output=True, text=True, timeout=900)
        failed = _check_drift(step, cmd, result, Path(lex_file[0]).parent)
        if failed is not None:
            return failed

        (project / MODELS_FILE).write_text(MODELS_SOURCE)
        for step, lex_args in INIT_STEPS:
            failed = run_lex(step, lex_args)
            if failed is not None:
                return failed
    finally:
        if args.keep:
            print(f"Kept {work} and database {database_name(project.name)}.")
        else:
            if database_made:
                _drop_database(python, database_name(project.name), env, run)
            shutil.rmtree(work, ignore_errors=True)

    print(f"{wheel.name}: an empty database migrates from the installed wheel. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
