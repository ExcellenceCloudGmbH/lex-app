# lex/bin/lex.py
import asyncio
import io
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
import shutil

import click
import uvicorn
from lex.tools.ai_dashboard import launch_ai_dashboard
from lex.tools.ai_faq import launch_ai_faq
from lex.tools.project_root import find_project_root, resolve_llm_working_directory
from lex.tools.setup_with_ai import (
    DEFAULT_REMOTE_MCP_URL,
    SetupWithAICredentials,
    SetupWithAIError,
    apply_ai_update,
    bootstrap_github_copilot_mcp_server_for_pycharm,
    build_ai_env_values,
    configure_ai_integration,
    install_lex_mcp_local,
    launch_setup_with_ai_form,
    probe_lex_mcp_local_server_for_pycharm,
    resolve_active_python_executable,
)
from lex.tools.verify_ai_assets import verify_ai_assets

# Defer Django imports and setup until needed (NOT at import time)
_DJANGO_READY = False
_GET_COMMANDS = None
_CALL_COMMAND = None

LEX_APP_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.as_posix()
PROJECT_ROOT_DIR = Path(find_project_root(os.getcwd())).resolve()
sys.path.append(LEX_APP_PACKAGE_ROOT)


def _load_project_env_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_project_env_file(PROJECT_ROOT_DIR)

# Set essential env vars early so they are available when any downstream code
# (celery.py, settings.py, asgi.py) eventually triggers Django setup.
# This is cheap — the expensive part (django.setup()) remains deferred.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
os.environ.setdefault("PROJECT_ROOT", PROJECT_ROOT_DIR.as_posix())
os.environ.setdefault("LEX_APP_PACKAGE_ROOT", LEX_APP_PACKAGE_ROOT)

lex = click.Group(help="lex-app Command Line Interface")

# ---------- Project root and configs (no Django) ----------

DEFAULT_ENV = """KEYCLOAK_URL=https://auth.excellence-cloud.de
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
FLOWER_ADDRESS=127.0.0.1
FLOWER_PORT=5555
FLOWER_URL_PREFIX=
"""


def _has_explicit_pytest_target(project_root: Path, forwarded_args: list[str]) -> bool:
    for arg in forwarded_args:
        if arg.startswith("-"):
            continue
        target = arg.split("::", 1)[0]
        if (project_root / target).exists():
            return True
    return False


def ensure_env_file(project_root: str, content: str = DEFAULT_ENV):
    p = Path(project_root) / ".env"
    if p.exists():
        return str(p), False
    p.write_text(content, encoding="utf-8")
    return str(p), True

def generate_configs(project_root: str):
    from generate_pycharm_configs import generate_pycharm_configs
    generate_pycharm_configs(project_root)
    (Path(project_root) / Path("migrations")).mkdir(exist_ok=True, parents=True)
    (Path(project_root) / Path("migrations") / Path("__init__.py")).touch(exist_ok=True)

# ---------- Lazy Django bootstrap and dynamic forwarding ----------

def _bootstrap_django():
    global _DJANGO_READY, _GET_COMMANDS, _CALL_COMMAND
    if _DJANGO_READY:
        return _GET_COMMANDS, _CALL_COMMAND
    # Env vars are already set at module level; just call setup.
    import django
    django.setup()
    from django.core.management import get_commands, call_command
    _DJANGO_READY = True
    _GET_COMMANDS = get_commands
    _CALL_COMMAND = call_command
    return _GET_COMMANDS, _CALL_COMMAND

def _forward_to_django(command_name, args):
    get_commands, call_command = _bootstrap_django()
    cmds = get_commands()
    if command_name not in cmds:
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", command_name, *args])
        return
    call_command(command_name, *args)

def _install_dynamic_commands():
    # Only called for non-setup entry, so safe to initialize Django
    get_commands, _ = _bootstrap_django()
    for name in get_commands().keys():
        if name in lex.commands:
            continue

        @lex.command(name=name, context_settings=dict(
            ignore_unknown_options=True,
            allow_extra_args=True,
        ))
        @click.pass_context
        def _cmd(ctx, __name=name):
            _forward_to_django(__name, ctx.args)

# ---------- Existing specialized commands (unchanged behavior) ----------

def _run_celery_command(args):
    _bootstrap_django()
    from celery.bin.celery import celery as celery_main
    celery_main(list(args))


def _should_use_threads_pool():
    """
    Use a non-prefork worker pool on platforms where Celery's default billiard
    pool is unreliable for local development.
    """
    return platform.system() in {"Darwin", "Windows"}


def build_celery_worker_command(worker_number, extra_args=()):
    command = [
        sys.executable,
        "-m",
        "lex",
        "celery",
        "-A",
        "lex_app",
        "worker",
        "--loglevel=info",
        "--concurrency=1",
        "--prefetch-multiplier=1",
    ]

    if _should_use_threads_pool():
        command.extend(["-P", "threads"])

    command.extend(["-n", f"worker{worker_number}@%h", *extra_args])
    return command


def _stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()

    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def build_flower_command(settings, extra_args=()):
    command = [
        "--app",
        "lex_app.celery:app",
        "flower",
        f"--address={settings.FLOWER_ADDRESS}",
        f"--port={settings.FLOWER_PORT}",
    ]

    if getattr(settings, "FLOWER_URL_PREFIX", ""):
        command.append(f"--url_prefix={settings.FLOWER_URL_PREFIX}")

    return [*command, *extra_args]

@lex.command(name="celery", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def celery(ctx):
    # Celery needs Django bootstrapped so that lex_app.celery can load
    # broker/backend settings from django.conf:settings.  Without this,
    # Celery falls back to its default AMQP broker (RabbitMQ).
    _run_celery_command(ctx.args)


@lex.command(name="celery-workers", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("count", type=click.IntRange(1, None))
@click.pass_context
def celery_workers(ctx, count):
    env = os.environ.copy()
    env["IS_RUNNING_IN_CELERY"] = "true"
    env["CELERY_ACTIVE"] = "true"

    processes = []
    exit_code = None

    try:
        for worker_number in range(1, count + 1):
            command = build_celery_worker_command(worker_number, ctx.args)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT_DIR.as_posix(),
                    env=env,
                )
            )

        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    exit_code = code
                    break
            if exit_code is not None:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("Stopping Celery workers...")
    finally:
        _stop_processes(processes)

    if exit_code:
        raise SystemExit(exit_code)


@lex.command(name="flower", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def flower(ctx):
    _bootstrap_django()
    from django.conf import settings

    _run_celery_command(build_flower_command(settings, ctx.args))

@lex.command(name="streamlit", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def streamlit(ctx):
    # ── uvloop / nest_asyncio fix ──────────────────────────────────────
    # uvloop (pulled in by `uv` or `uvicorn[standard]`) sets itself as
    # the global event-loop policy.  Streamlit imports nest_asyncio which
    # patches asyncio.run at import time, capturing the *current* policy.
    # If uvloop is still active at that moment, nest_asyncio's patched
    # asyncio.run will later call uvloop's get_event_loop(), which raises:
    #   RuntimeError: There is no current event loop in thread 'MainThread'
    #
    # Fix: reset the policy and create a main-thread loop BEFORE importing
    # streamlit, and prevent uvloop from re-installing itself.
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        import uvloop
        uvloop.install = lambda: None  # prevent re-activation
    except ImportError:
        pass
    # ───────────────────────────────────────────────────────────────────

    # Keep streamlit startup non-interactive in terminal/CI sessions.
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

    from streamlit.web.cli import main as streamlit_main
    streamlit_args = list(ctx.args)
    if not streamlit_args:
        streamlit_args = ["run", f"{LEX_APP_PACKAGE_ROOT}/streamlit_app.py"]
    file_index = next((i for i, item in enumerate(streamlit_args) if 'streamlit_app.py' in item), None)
    if file_index is not None:
        streamlit_app_path = streamlit_args[file_index]
        if not os.path.isabs(streamlit_app_path):
            streamlit_args[file_index] = f"{LEX_APP_PACKAGE_ROOT}/{streamlit_app_path}"

    def run_uvicorn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run("proxy:app", host="0.0.0.0", port=8501, loop="asyncio")

    t = threading.Thread(target=run_uvicorn, daemon=True)
    t.start()

    streamlit_main(streamlit_args + ["--browser.serverPort", "8501", "--server.port", "8080"])


@lex.command(name="pytest", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def pytest_cmd(ctx):
    """Run pytest with Django bootstrapped (mirrors celery/flower handlers).

    \b
    Lex-only flags (intercepted, NOT forwarded to pytest):
      --report           Generate a per-group PDF summary and HTML coverage
                           bundle under the effective Lex report output dir.
      --report-and-email Generate the PDF summary + HTML coverage bundle and
                           then prepare/send report emails if email delivery is
                           enabled in the effective Lex test config.
      --send-emails      Skip the interactive confirmation prompt and send
                           report emails immediately. Only applies together
                           with --report-and-email. Intended for CI runs.

    \b
    Selecting / excluding groups (standard pytest `-m` marker expressions —
    every configured group name is registered as a marker):
      lex pytest -m creation                  # only the `creation` group
      lex pytest -m "creation or creation2"   # union of two groups
      lex pytest -m "not creation2"           # exclude one group
      lex pytest -m "creation and smoke"      # tests tagged with BOTH

    \b
    Discovery:
      lex pytest-groups                       # list groups + their tests (no run)

    Group metadata, receivers, and the tests entrypoint are read from
    the resolved effective Lex test config: repo-local ``lex_test_config.yaml``
    when present, otherwise a workflow/backend supplied
    ``$LEX_TEST_CONFIG_PAYLOAD`` JSON object. An in-process pytest plugin
    registers each group as a marker, validates that every used marker maps to
    a configured group (hard error otherwise), and aggregates
    pass/fail/skip/error counts per group. `lex pytest --report` writes the
    PDF report plus the HTML coverage bundle. `lex pytest --report-and-email`
    additionally prepares one report email per resolved recipient delivery
    using the configured sender and recipient data and asks for confirmation
    before sending unless ``--send-emails`` is supplied.
    """
    # Run from project root so lex_test_config.yaml and the tests entrypoint
    # are auto-discovered regardless of where `lex pytest` was invoked.
    os.chdir(PROJECT_ROOT_DIR.as_posix())

    from lex.tools.test_groups import (
        build_report_email_recap,
        LexGroupsPlugin,
        LexTestConfigError,
        parse_lex_pytest_args,
        plan_recipient_deliveries,
        resolve_config,
        send_report_emails,
        write_pdf_report,
    )

    parsed = parse_lex_pytest_args(list(ctx.args))
    should_generate_report = parsed.report or parsed.report_and_email

    try:
        lex_test_config = resolve_config(PROJECT_ROOT_DIR)
    except LexTestConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Inject the configured tests entrypoint only if the user did not already
    # pass an explicit pytest path or nodeid target.
    forwarded = list(parsed.forwarded)
    if not _has_explicit_pytest_target(PROJECT_ROOT_DIR, forwarded):
        forwarded.insert(0, lex_test_config.tests_entrypoint)

    plugin = LexGroupsPlugin(lex_test_config, strict=True)

    import pytest as _pytest

    coverage_runner = None
    coverage_error = None
    collect_coverage = should_generate_report
    if collect_coverage:
        try:
            from coverage import Coverage
        except Exception:
            Coverage = None
        if Coverage is not None:
            report_output_dir = lex_test_config.report_dir
            coverage_runner = Coverage(
                data_file=None,
                source=[PROJECT_ROOT_DIR.as_posix()],
                omit=[
                    str(PROJECT_ROOT_DIR / ".venv" / "*"),
                    str(PROJECT_ROOT_DIR / "venv" / "*"),
                    str(PROJECT_ROOT_DIR / "env" / "*"),
                    str(PROJECT_ROOT_DIR / "Tests" / "*"),
                    str(PROJECT_ROOT_DIR / "tests" / "*"),
                    str(report_output_dir / "*"),
                    str(PROJECT_ROOT_DIR / "reports" / "*"),
                    str(PROJECT_ROOT_DIR / "htmlcov" / "*"),
                    str(PROJECT_ROOT_DIR / "static" / "*"),
                    str(PROJECT_ROOT_DIR / "media" / "*"),
                    str(PROJECT_ROOT_DIR / "build" / "*"),
                    str(PROJECT_ROOT_DIR / "dist" / "*"),
                    str(PROJECT_ROOT_DIR / "_authentication_settings.py"),
                    str(PROJECT_ROOT_DIR / "_streamlit_structure.py"),
                    "*/.pytest_cache/*",
                    "*/__pycache__/*",
                    "*/migrations/*",
                    "*/node_modules/*",
                    "*/site-packages/*",
                ],
            )
            coverage_runner.start()
        else:
            coverage_error = "coverage.py is not available in this environment."

    # Bootstrap Django after starting report coverage so app/model import-time
    # code is included in the measured project coverage.
    _bootstrap_django()

    # Stand up Django's test database the same way `manage.py test` /
    # DiscoverRunner.run_tests() does:
    #   1. setup_test_environment() — installs the test client, RequestFactory
    #      patches, deprecation-warning filter etc.
    #   2. DiscoverRunner.setup_databases() — creates the ``test_<dbname>``
    #      database (or `test_<NAME>` from the settings TEST overrides), runs
    #      migrations, and re-points ``connection.settings_dict["NAME"]``
    #      so any TestCase / TransactionTestCase / SimpleTestCase subclass
    #      that touches the ORM hits the test DB, not the production one.
    # Without this, every unittest.TestCase-derived test errors at setUp with
    # `database "<prod name>" does not exist` because Django's runner machinery
    # never ran.
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    _db_runner = DiscoverRunner(verbosity=1, interactive=False, keepdb=False)
    _old_db_config = _db_runner.setup_databases()

    started_at = time.perf_counter()
    try:
        exit_code = _pytest.main(forwarded, plugins=[plugin])
    except LexTestConfigError as exc:
        # Raised from pytest_collection_modifyitems on marker/group mismatch.
        raise click.ClickException(str(exc)) from exc
    finally:
        try:
            _db_runner.teardown_databases(_old_db_config)
        finally:
            teardown_test_environment()
        plugin.run_duration = f"{time.perf_counter() - started_at:.1f} s"
        if coverage_runner is not None:
            try:
                coverage_runner.stop()
                with io.StringIO() as stream:
                    total = float(
                        coverage_runner.report(
                            file=stream,
                            show_missing=False,
                            ignore_errors=True,
                        )
                    )
                    file_coverage = _parse_coverage_text_report(stream.getvalue())
                shutil.rmtree(lex_test_config.coverage_html_dir, ignore_errors=True)
                coverage_runner.html_report(
                    directory=str(lex_test_config.coverage_html_dir),
                    ignore_errors=True,
                )
                coverage_index = lex_test_config.coverage_html_dir / "index.html"
                if not coverage_index.exists():
                    raise RuntimeError(
                        f"Missing coverage HTML entrypoint at {coverage_index}."
                    )
            except Exception as exc:
                coverage_error = f"Coverage summary/HTML generation failed: {exc}"
                plugin.coverage_summary = None
            else:
                plugin.coverage_summary = {
                    "label": "Framework-wide code coverage",
                    "display": f"{total:.1f}%",
                    "percentage": round(total, 1),
                    "files": file_coverage,
                }

    if should_generate_report and plugin.coverage_summary is None:
        message = (
            "Coverage data is required for Lex test report PDF/HTML artifacts. "
            "The report would otherwise show `n/a`."
        )
        if coverage_error:
            message = f"{message} {coverage_error}"
        raise click.ClickException(message)


    if should_generate_report:
        try:
            pdf_path = write_pdf_report(
                config=lex_test_config,
                plugin=plugin,
                pytest_exit_code=int(exit_code),
            )
        except Exception as exc:  # pragma: no cover - defensive
            click.echo(f"Warning: failed to write PDF report: {exc}", err=True)
        else:
            click.echo(f"Lex test report: {pdf_path}")
            click.echo(
                f"Lex test coverage HTML: {lex_test_config.coverage_html_dir / 'index.html'}"
            )

    if parsed.report_and_email:
        if not lex_test_config.email.get("from_email"):
            raise click.ClickException(
                "email.from_email is required for `lex pytest --report-and-email`."
            )
        deliveries = plan_recipient_deliveries(config=lex_test_config, group_results=plugin.results)
        should_send = bool(deliveries)
        if deliveries and not parsed.send_emails:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise click.ClickException(
                    "Lex test report emails were requested, but this session cannot confirm the send. "
                    "Re-run with --send-emails for CI or other non-interactive environments."
                )
            click.echo(build_report_email_recap(config=lex_test_config, deliveries=deliveries))
            should_send = click.confirm("Send these Lex test report emails now?", default=False)
            if not should_send:
                click.echo("Skipped Lex test report emails.")

        if should_send:
            try:
                deliveries = send_report_emails(
                    config=lex_test_config,
                    plugin=plugin,
                    pytest_exit_code=int(exit_code),
                )
            except Exception as exc:
                raise click.ClickException(f"Failed to send Lex test report emails: {exc}") from exc
            else:
                if deliveries:
                    click.echo(f"Sent {len(deliveries)} Lex test report email(s).")

    raise SystemExit(int(exit_code))

def _parse_coverage_text_report(text: str) -> list[dict]:
    """Parse ``coverage report`` text output into per-file dicts.

    Each dict has keys: ``name``, ``stmts``, ``miss``, ``cover``.
    The TOTAL row is excluded.
    """
    import re

    files: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("-") or line.upper().startswith("NAME") or line.upper().startswith("TOTAL"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 4:
            continue
        name = parts[0]
        try:
            stmts = int(parts[1])
            miss = int(parts[2])
            cover_str = parts[3].rstrip("%")
            cover = float(cover_str)
        except (ValueError, IndexError):
            continue
        files.append({"name": name, "stmts": stmts, "miss": miss, "cover": cover})
    files.sort(key=lambda f: f["cover"])
    return files


@lex.command(
    name="pytest-groups",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.pass_context
def pytest_groups_cmd(ctx):
    """List configured Lex test groups and the tests attributed to each.

    Runs pytest in `--collect-only` mode (no tests are executed) and prints
    the mapping group -> [test nodeids] using the same marker-based
    attribution as `lex pytest`.  Any extra args are forwarded to pytest's
    collection phase, so you can scope the listing, e.g.:

    \b
      lex pytest-groups                     # full listing
      lex pytest-groups -m creation         # only tests in the `creation` group
      lex pytest-groups Tests/creation2     # only tests under that path
    """
    _bootstrap_django()
    os.chdir(PROJECT_ROOT_DIR.as_posix())

    from lex.tools.test_groups import (
        LexTestConfigError,
        collect_groups,
        resolve_config,
    )

    try:
        config = resolve_config(PROJECT_ROOT_DIR)
    except LexTestConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    listing = collect_groups(config, extra_args=list(ctx.args))

    click.echo("")
    click.echo(f"Lex test groups (from {config.source}):")
    click.echo(f"  tests_entrypoint: {config.tests_entrypoint}")
    click.echo("")

    for group in config.groups:
        tests = listing.group_to_tests.get(group.name, [])
        header = f"[{group.name}] {group.description or '(no description)'}"
        click.echo(click.style(header, bold=True))
        click.echo(f"  tests: {len(tests)}")
        for nodeid in tests:
            click.echo(f"    - {nodeid}")
        if not tests:
            click.echo("    (no tests carry this marker)")
        click.echo("")

    if listing.untagged:
        click.echo(click.style(f"Untagged tests ({len(listing.untagged)}):", fg="yellow"))
        for nodeid in listing.untagged:
            click.echo(f"  - {nodeid}")
        click.echo("")

    raise SystemExit(listing.exit_code)


def _collect_static_if_deployed():
    if not os.getenv("DEPLOYMENT_ENVIRONMENT"):
        return

    _, call_command = _bootstrap_django()
    call_command("collectstatic", interactive=False, verbosity=0)


@lex.command(name="start", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def start(ctx):
    os.environ.setdefault("CALLED_FROM_START_COMMAND", "True")
    _collect_static_if_deployed()
    uvicorn.main(ctx.args)

# ---------- New: setup (never bootstraps Django) ----------

@lex.command(name="setup", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def setup(project_root):
    root = find_project_root(project_root or os.getcwd())
    env_path, created = ensure_env_file(root)
    generate_configs(root)
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    click.echo(f".run: {os.path.join(root, '.run')} (updated)")


@lex.command(name="setup-with-ai", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
@click.option("--github-token", help="Fine-grained GitHub token for Copilot Extensions.")
@click.option("--remote-mcp-api-key", help="API key for the hosted remote MCP server.")
@click.option(
    "--remote-mcp-url",
    default=DEFAULT_REMOTE_MCP_URL,
    show_default=True,
    help="Remote MCP HTTP endpoint used by lex-mcp-local.",
)
@click.option(
    "--mcp-mode",
    default="forward",
    show_default=True,
    type=click.Choice(["forward", "backward"], case_sensitive=False),
    help="MCP workflow mode. Determines which .github directory is copied and "
         "which mode the server runs in (written to LEX_MCP_MODE in .env and mcp.json).",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Skip the local setup page and prompt in the terminal instead.",
)
def setup_with_ai(project_root, github_token, remote_mcp_api_key, remote_mcp_url, mcp_mode, no_browser):
    # The LLM agent's working directory IS the project root for setup-with-ai.
    # Do not walk up to a git toplevel / marker file: the LLM often runs
    # inside a subdirectory of a larger checkout, and walking up causes
    # docs/ and .github/ to be written into an ancestor (or be skipped
    # entirely when that ancestor is the lex package itself).
    root = resolve_llm_working_directory(project_root)
    python_executable = resolve_active_python_executable(root)

    env_path, created = ensure_env_file(root.as_posix())
    generate_configs(root.as_posix())
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    click.echo(f".run: {os.path.join(root.as_posix(), '.run')} (updated)")

    credentials = _collect_setup_with_ai_credentials(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        project_root=root,
        env_file_path=Path(env_path),
        no_browser=no_browser,
    )

    # The browser form lets the user pick forward/backward; use that choice
    # unless the CLI flag was explicitly set by the caller.
    effective_mcp_mode = credentials.mcp_mode if credentials.mcp_mode else mcp_mode
    click.echo(f"MCP workflow mode: {effective_mcp_mode}")

    click.echo("Installing lex-mcp-local into the active virtual environment...")
    try:
        install_lex_mcp_local(python_executable, credentials.remote_mcp_api_key, upgrade=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Failed to install lex-mcp-local into {python_executable}."
        ) from exc

    # Post-install validation: check the installed version supports the
    # requested mode and warn loudly if not.
    from lex.tools.setup_with_ai import (
        get_installed_lex_mcp_local_version,
        MINIMUM_DUAL_MODE_VERSION,
        _has_unified_mcp_entry_point,
    )
    installed_version = get_installed_lex_mcp_local_version(python_executable)
    if installed_version:
        click.echo(f"Installed lex-mcp-local {installed_version}.")
    else:
        click.echo("Warning: could not detect installed lex-mcp-local version.")
    if effective_mcp_mode == "backward" and not _has_unified_mcp_entry_point(python_executable):
        click.echo(
            f"Warning: backward mode requires lex-mcp-local >= {MINIMUM_DUAL_MODE_VERSION}, "
            f"but {installed_version or 'an older version'} is installed. "
            f"The server will start in forward-only (legacy) mode. "
            f"Ask your administrator to publish >= {MINIMUM_DUAL_MODE_VERSION} to Cloudsmith."
        )
        effective_mcp_mode = "forward"

    try:
        artifacts = configure_ai_integration(
            project_root=root,
            github_token=credentials.github_token,
            remote_mcp_api_key=credentials.remote_mcp_api_key,
            remote_mcp_url=remote_mcp_url,
            mcp_mode=effective_mcp_mode,
            python_executable=python_executable,
            verify_server=False,
        )
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc

    server_probe = None
    server_probe_warning: str | None = None
    copilot_state_db_path = None
    copilot_state_warning: str | None = None
    click.echo(f"Validating {artifacts.server_name} with a PyCharm-style MCP session...")
    try:
        server_probe = probe_lex_mcp_local_server_for_pycharm(
            project_root=root,
            python_executable=artifacts.python_executable,
            wrapper_script_path=artifacts.wrapper_script_path,
            server_name=artifacts.server_name,
            env_values=build_ai_env_values(
                github_token=credentials.github_token,
                remote_mcp_api_key=credentials.remote_mcp_api_key,
                remote_mcp_url=remote_mcp_url,
                mcp_mode=effective_mcp_mode,
            ),
        )
    except SetupWithAIError as exc:
        server_probe_warning = str(exc)
    else:
        try:
            copilot_state_db_path = bootstrap_github_copilot_mcp_server_for_pycharm(
                server_probe,
                mcp_config_path=artifacts.mcp_config_path,
                server_name=artifacts.server_name,
            )
        except SetupWithAIError as exc:
            copilot_state_warning = str(exc)

    click.echo(f"Updated .env with AI credentials: {artifacts.env_file_path}")
    if artifacts.github_directory_path is not None:
        click.echo(f"Copied lex-mcp-local GitHub files: {artifacts.github_directory_path}")
    if artifacts.docs_directory_path is not None:
        click.echo(f"Copied lex-app docs: {artifacts.docs_directory_path}")
    click.echo(f"Registered {artifacts.server_name} in GitHub Copilot MCP config: {artifacts.mcp_config_path}")
    click.echo(f"Using interpreter: {artifacts.python_executable}")
    click.echo(f"Using wrapper: {artifacts.wrapper_script_path}")
    if server_probe is not None:
        version_suffix = f" v{server_probe.server_version}" if server_probe.server_version else ""
        click.echo(
            f"Validated {artifacts.server_name}{version_suffix}: "
            f"{server_probe.tool_count} tools, "
            f"{server_probe.prompt_count} prompts, "
            f"{server_probe.resource_count} resources, "
            f"{server_probe.resource_template_count} templates."
        )
        if copilot_state_db_path is not None:
            click.echo(f"Primed GitHub Copilot MCP cache: {copilot_state_db_path}")
            click.echo(
                f"{artifacts.server_name} is configured and primed for PyCharm-managed stdio launch."
            )
        elif copilot_state_warning is not None:
            click.echo(f"Warning: {copilot_state_warning}")
            click.echo(
                f"{artifacts.server_name} is configured for PyCharm-managed stdio launch, "
                "but the Copilot cache could not be primed automatically."
            )
        else:
            click.echo(f"{artifacts.server_name} is configured for PyCharm-managed stdio launch.")
        click.echo(
            "If PyCharm is already open, restart it once so GitHub Copilot reloads the MCP configuration "
            f"and starts {artifacts.server_name} from inside the IDE."
        )
    elif server_probe_warning is not None:
        click.echo(
            "Warning: "
            f"{server_probe_warning} GitHub Copilot may still be able to launch the server from mcp.json on demand."
        )

    # Final asset sweep: make sure every required directory (docs, .github,
    # ...) is on disk and up to date. Catches the case where an earlier
    # plain `lex setup` left the project partially initialized, or where a
    # copy step silently no-op'd.
    click.echo("Verifying AI asset directories...")
    try:
        verify_result = verify_ai_assets(project_root=root, mode=effective_mcp_mode)
    except SetupWithAIError as exc:
        click.echo(f"Warning: AI asset verification failed: {exc}")
    else:
        restored_total = len(verify_result.restored_files)
        for directory_result in verify_result.directories:
            if directory_result.skipped_reason is not None:
                click.echo(
                    f"  [skip] {directory_result.directory_name}: "
                    f"{directory_result.skipped_reason}"
                )
            elif directory_result.restored_files:
                click.echo(
                    f"  [fix]  {directory_result.directory_name}: "
                    f"restored {len(directory_result.restored_files)} file(s) "
                    f"into {directory_result.destination_directory}"
                )
            else:
                click.echo(
                    f"  [ok]   {directory_result.directory_name}: up to date."
                )
        if restored_total:
            click.echo(f"AI assets verified: restored {restored_total} file(s).")
        else:
            click.echo("AI assets verified: nothing to restore.")

    click.echo(
        "Setup complete. Open GitHub Copilot in PyCharm and write your first prompt."
    )


@lex.command(name="ai-update", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def ai_update(project_root):
    """Apply incremental updates to an existing LEX AI setup."""
    root = Path(find_project_root(project_root or os.getcwd())).resolve()

    click.echo("Applying LEX AI updates...")
    try:
        results = apply_ai_update(project_root=root)
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc

    if not results:
        click.echo("No updates to apply.")
        return

    for result in results:
        click.echo(f"Applied update to v{result.version}:")
        if result.env_keys_removed:
            click.echo(f"  Removed from .env: {', '.join(result.env_keys_removed)}")
        if result.mcp_env_keys_removed:
            click.echo(f"  Removed from mcp.json: {', '.join(result.mcp_env_keys_removed)}")
        if result.package_upgraded:
            click.echo("  Upgraded lex-mcp-local package.")
        for dest in result.artifact_directories_copied:
            click.echo(f"  Copied {dest.name} to {dest}")
        if result.server_restarted:
            click.echo("  Stopped MCP server (will restart on next use).")
        has_action = (
            result.env_keys_removed
            or result.mcp_env_keys_removed
            or result.package_upgraded
            or result.artifact_directories_copied
            or result.server_restarted
        )
        if not has_action:
            click.echo("  Already up to date.")

    click.echo("LEX AI update complete.")


@lex.command(name="ai-faq", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
def ai_faq():
    """Open the LEX AI FAQ page in your browser."""
    launch_ai_faq(reporter=click.echo)


@lex.command(name="ai-verify", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
@click.option(
    "--mode",
    "mode",
    type=click.Choice(["auto", "forward", "backward", "all"], case_sensitive=False),
    default="auto",
    show_default=True,
    help=(
        "Which MCP mode's assets to verify. 'auto' (default) detects the active "
        "mode from --mode > override file > project .env > mcp.json > "
        "process env > forward. 'all' verifies both forward and backward."
    ),
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress per-file output; only print a summary line (or nothing on success).",
)
@click.option(
    "--silent",
    is_flag=True,
    help="Print nothing on success. Implies --quiet. Intended for use as a fast pre-flight "
         "guard at the start of every MCP tool call.",
)
def ai_verify(project_root, mode, quiet, silent):
    """Verify (and silently restore) the LEX AI asset directories.

    Walks the canonical ``.github`` directory shipped by the active MCP mode's
    package (``lex_mcp_local`` for forward, ``lex_mcp_reverse`` for backward)
    and the ``docs`` directory shipped by ``lex``, then rewrites any file under
    the project root that is missing or whose contents have drifted. Existing
    user-only files are left untouched.
    """
    # The directory passed via --project-root (or cwd) IS the LLM working
    # directory. We verify assets in *that* directory literally; we do not
    # walk up to a git toplevel / marker file (see resolve_llm_working_directory).
    root = resolve_llm_working_directory(project_root)
    quiet = quiet or silent
    explicit_mode = None if mode == "auto" else mode.lower()

    try:
        result = verify_ai_assets(project_root=root, mode=explicit_mode)
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc

    restored_total = len(result.restored_files)

    if not quiet:
        click.echo(f"Active MCP mode: {result.mode} (source: {result.mode_source})")

    for directory_result in result.directories:
        if directory_result.skipped_reason is not None:
            if not quiet:
                click.echo(
                    f"[skip] {directory_result.directory_name}: "
                    f"{directory_result.skipped_reason}"
                )
            continue

        if not directory_result.restored_files:
            if not quiet:
                click.echo(f"[ok]   {directory_result.directory_name}: up to date.")
            continue

        if not quiet:
            click.echo(
                f"[fix]  {directory_result.directory_name}: "
                f"restored {len(directory_result.restored_files)} file(s) "
                f"({len(directory_result.missing_files)} missing, "
                f"{len(directory_result.modified_files)} modified) "
                f"into {directory_result.destination_directory}"
            )
            for relative_path in directory_result.restored_files:
                click.echo(f"        - {relative_path}")

    if restored_total == 0:
        if not silent:
            click.echo("AI assets verified: nothing to restore.")
        return

    # --silent must print NOTHING on success — including when files were
    # restored.  The MCP wrapper invokes this as a pre-flight on every tool
    # call; any stdout leak corrupts the stdio JSON-RPC stream and causes
    # the IDE to time out.
    if not silent:
        click.echo(f"AI assets verified: restored {restored_total} file(s).")


@lex.command(name="ai-dashboard", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def ai_dashboard(project_root):
    """Open the LEX AI Dashboard in your browser.

    Displays and lets you edit the MCP server mode, GitHub token, Remote MCP
    API key, and other configuration. Changes are written to .env and mcp.json.
    Press Ctrl+C to stop the dashboard server.
    """
    root = Path(find_project_root(project_root or os.getcwd())).resolve()
    try:
        launch_ai_dashboard(
            project_root=root,
            reporter=click.echo,
        )
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc


def _collect_setup_with_ai_credentials(
    *,
    github_token: str | None,
    remote_mcp_api_key: str | None,
    project_root: Path,
    env_file_path: Path,
    no_browser: bool,
) -> SetupWithAICredentials:
    if github_token and remote_mcp_api_key:
        return SetupWithAICredentials(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
        )

    if not no_browser:
        click.echo("Opening the local AI setup page in your browser...")
        try:
            return launch_setup_with_ai_form(
                project_root=project_root,
                env_file_path=env_file_path,
                reporter=click.echo,
            )
        except SetupWithAIError as exc:
            click.echo(f"Browser setup page failed: {exc}")
            click.echo("Falling back to terminal prompts.")

    final_github_token = github_token or click.prompt(
        "GitHub token",
        hide_input=True,
    )
    final_remote_mcp_api_key = remote_mcp_api_key or click.prompt(
        "Remote MCP API key",
        hide_input=True,
    )
    return SetupWithAICredentials(
        github_token=final_github_token,
        remote_mcp_api_key=final_remote_mcp_api_key,
    )

# Commands that have dedicated handlers and do NOT need Django management
# command enumeration.  For these, _bootstrap_django() is skipped so that
# django.setup() (and every AppConfig.ready()) only fires once — inside
# the actual server process (uvicorn / celery worker / streamlit).
_SKIP_BOOTSTRAP_COMMANDS = frozenset(
    {"start", "celery", "celery-workers", "flower", "pytest", "pytest-groups", "setup", "setup-with-ai", "ai-update", "ai-faq", "ai-verify", "ai-dashboard"}
)


def main():
    argv = sys.argv[1:]
    first_arg = argv[0] if argv else None

    # Top-level help/version requests must never bootstrap Django — Django
    # setup can fail in directories whose name is not a valid Python
    # identifier (e.g. "release-smoke"), and `lex --help` should always
    # work regardless of CWD.
    if first_arg is None or first_arg in {"--help", "-h", "--version"}:
        return lex(prog_name="lex")

    if first_arg in _SKIP_BOOTSTRAP_COMMANDS:
        # These commands have dedicated Click handlers registered above.
        # Do NOT call _install_dynamic_commands() — that would trigger
        # django.setup() in the CLI process, causing every AppConfig.ready()
        # to fire twice (once here, once when the real server starts).
        return lex(prog_name="lex")

    # All other commands (including init, migrate, makemigrations, …) need the full
    # set of Django management commands registered as Click sub-commands.
    _install_dynamic_commands()
    return lex(prog_name="lex")
