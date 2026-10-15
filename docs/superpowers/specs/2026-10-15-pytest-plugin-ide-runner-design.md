# A lex project's tests in the IDE's own test runner

**Status:** design approved in chat, 2026-10-15. **Branch:** `feat/pytest-plugin-ide-runner`.

## Problem

`lex pytest` does five things before it calls `pytest.main()` (`lex/bin/lex.py::pytest_cmd`):

1. loads the project's `.env`, sets `DJANGO_SETTINGS_MODULE`, `PROJECT_ROOT` and
   `LEX_APP_PACKAGE_ROOT`, and moves to the project root;
2. `django.setup()`;
3. `setup_test_environment()`;
4. `DiscoverRunner(keepdb=True).setup_databases(aliases={"default"})`;
5. makes `register_converter` idempotent for the run.

It also registers `LexGroupsPlugin`, which declares every group in
`lex_test_config.yaml` as a marker and aborts the run on an undeclared one.

PyCharm's pytest runner (`_jb_pytest_runner.py`), VS Code's test explorer and a plain
`pytest` call `pytest.main()` directly, so none of it happens. Measured with PyCharm's
own runner on `lex/test_project/tests/crud_api/test_2a_create.py`: the test tree shows a
single failed `top_level_collect` node (`ImproperlyConfigured: Requested setting
INSTALLED_APPS`) and 0 tests. Projects that want PyCharm's test tree copy the five steps
into a root `conftest.py`.

## Goal

In any lex project that has a `lex_test_config.yaml`, a plain pytest run — PyCharm's run
configurations, its gutter buttons and "Run pytest in …", VS Code, a terminal — behaves
exactly like `lex pytest`, with nothing in the project. `lex pytest` and the plain run use
one implementation, so they cannot drift.

**Not in scope:** test isolation (unchanged: one reused test database, no per-test rollback
for plain `unittest.TestCase` classes); parallel runs (`pytest-xdist`); any change to how
Celery behaves under test; choosing default test paths for a plain `pytest` with no target
(`lex pytest` keeps inserting `tests_entrypoint`).

## Design

### The plugin

A new module, `lex/tools/pytest_plugin.py`, registered with pytest as an entry point:

```toml
[project.entry-points.pytest11]
lex = "lex.tools.pytest_plugin"
```

It lives under `lex/tools/` for the reason `lex/tools/django_bootstrap.py` does: importing
anything under `lex.lex_app` evaluates Django settings, which read the environment, so the
`.env` must be loaded before that first import. The module imports nothing from Django or
`lex.lex_app` at import time.

**Activation.** The project root is found the way the settings find it:
`find_project_root()` from `$PROJECT_ROOT` when set, otherwise from the directory
pytest was invoked in (`lex.runtime_config.resolve_project_root`). The plugin is
active when that root holds a
`lex_test_config.yaml`, or `$LEX_TEST_CONFIG_PAYLOAD` is set (the same two sources
`resolve_config()` accepts). Otherwise it does nothing at all. `-p no:lex` switches it off.

**Hooks**, in the order pytest calls them:

| Hook | What it does |
|---|---|
| `pytest_load_initial_conftests` (tryfirst) | Resolves the test config. Runs `django_bootstrap.prepare_environment()`, moves to the project root and calls `django.setup()` — before any `conftest.py` is imported, so a project conftest may import models. |
| `pytest_configure` (tryfirst) | Registers `LexGroupsPlugin(strict=True)` under a fixed name unless one is registered already. Unless this is a `--collect-only` run, or the test environment is already set up: `setup_test_environment()`, test-database setup for the `default` alias with `keepdb=True`, and the idempotent `register_converter` patch. |
| `pytest_unconfigure` (trylast) | Undoes, in reverse, only what this plugin did: stops the patch, tears the databases down (keepdb: connection settings restored, nothing dropped), tears the test environment down. |

"Already set up" is `django.test.utils._TestState.saved_data` existing — the attribute
`setup_test_environment()` sets and refuses to see twice.

### `lex pytest` and `lex pytest-groups`

`lex pytest` keeps everything that is its own: the lex-only flags, the config check, inserting
`tests_entrypoint` when no target is given, coverage, the PDF report and email delivery. Its
copy of steps 2–5 is removed. It calls

```python
pytest.main(["-p", "no:lex", *forwarded], plugins=[pytest_plugin, groups_plugin])
```

The module is passed in directly, so the bootstrap runs even where the entry point is not
installed (an editable install made before this change); `-p no:lex` stops pytest loading the
same module a second time through the entry point. `groups_plugin` is the `LexGroupsPlugin`
whose results feed the report; the plugin sees it registered and adds no second one.

`lex pytest-groups` passes the module the same way. Being collect-only, it gets Django and the
markers and no database.

### Projects that copied the setup into a `conftest.py`

Nothing breaks when such a project upgrades. The plugin's `pytest_configure` runs before any
conftest's, so a copied conftest that checks `_TestState.saved_data` (as the known copy does)
finds the work done and stands down. If a conftest set the environment up first, the plugin
stands down the same way. The copied file can then be deleted.

### Run configurations

`generate_run_configs()` (run by `lex setup` and `lex-generate-configs`) additionally writes,
when `lex_test_config.yaml` loads:

- `.run/Tests.run.xml` — type `tests`, factory `py.test`, target type `PATH`, target
  `<project root>/<tests_entrypoint>` as an absolute path (as the other generated
  configurations write theirs), working directory the project root, the project's
  `.env`, `PYTHONUNBUFFERED=1` and `CELERY_ACTIVE=False`;
- `.run/Tests_<group>.run.xml` per configured group — the same, with `-m <group>`.

The field names (`_new_target`, `_new_targetType`, `_new_additionalArguments`, …) and the
`py.test` factory id were read from the installed PyCharm's `PyTestFactory` /
`PyTestsShared` classes. VS Code gets the matching `launch.json` entries (`module: pytest`,
same arguments), keeping the generator's rule that every PyCharm entry has a VS Code twin.

`CELERY_ACTIVE=False` is set in the run configurations only. The plugin leaves Celery alone,
so `lex pytest` on a deployed instance behaves as it does today.

### Docs (lex-app-docs)

- `CLI Commands.md`: running tests in PyCharm or VS Code; `-p no:lex`; that `lex pytest`
  remains the command for reports and CI.
- The `lex_test_config.yaml` table there documents keys that do not exist (`tests_root`,
  `report.recipients`, `report.sender`, groups as a mapping). Replace it with the real ones:
  `tests_entrypoint`, `groups` (a list of `name` / `description`), `receivers`, `email.*`,
  `report.output_dir`.

## Errors

| Situation | Result |
|---|---|
| `lex_test_config.yaml` invalid | `pytest.UsageError` with the loader's message, exit code 4 — as `lex pytest` does today |
| Test database cannot be set up | `pytest.UsageError` naming the database error; no tests run |
| Undeclared marker | exit code 4 with `LexGroupsPlugin`'s existing message |
| `pytest-django` active as well | `pytest.UsageError` saying to add `-p no:django` or remove it — both would set up Django's test environment and the second call raises |
| No `lex_test_config.yaml` | plugin inactive; plain pytest as before |

## Testing

Cluster allocation follows the test plan (`lex/test_project/test-plan/`). Surfaces:

1. A plain `pytest` run (a subprocess, no `lex` CLI) of a lex-app test that needs Django and
   the database passes. Red today with `ImproperlyConfigured`.
2. The same through PyCharm's runner contract: `_jb_pytest_runner.py` is not vendored, so the
   test drives `pytest.main()` the way it does (targets, no lex arguments).
3. Inactive without a config: a project without `lex_test_config.yaml` is untouched.
4. `-p no:lex` switches it off.
5. `--collect-only` sets up Django and markers but no database.
6. Group markers are registered, and an undeclared marker aborts with exit code 4.
7. `lex pytest` and `lex pytest-groups` behave as before (existing report-flag tests plus a run).
8. A copied conftest that guards on `_TestState.saved_data` stands down; no double setup.
9. The generated run configurations: XML shape, one per group, VS Code twins.

Verified by hand before the PR: PyCharm's real `_jb_pytest_runner.py`, from the local
PyCharm install, shows the lex-app test passing instead of the collection error.

## Compatibility

- Editable installs need reinstalling to register the entry point; `lex pytest` works either
  way because it passes the module directly.
- Paths use `as_posix()` throughout; the known copied conftest runs on Windows.
- A project gets this by upgrading lex-app; nothing else is required.
- lex-app's own CI-helper tests under `.github/scripts/` get a `pytest.ini` with
  `-p no:lex -p no:django`: this repository has a `lex_test_config.yaml`, so with
  lex-app installed the plugin would otherwise set Django and the shared test
  database up for those plain tests.
