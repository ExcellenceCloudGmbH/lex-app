# lex/bin/lex.py
import asyncio
import io
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
import shutil

import click
import uvicorn
from lex.tools.project_root import find_project_root, resolve_llm_working_directory
from lex.tools.setup_with_ai import (
    DEFAULT_LEX_MCP_MODE,
    DEFAULT_REMOTE_MCP_URL,
    SetupWithAICredentials,
    SetupWithAIError,
    bootstrap_github_copilot_mcp_server_for_pycharm,
    build_ai_env_values,
    configure_ai_integration,
    install_lex_mcp_local,
    launch_setup_with_ai_form,
    probe_lex_mcp_local_server,
    resolve_active_python_executable,
    run_ai_update_bootstrap,
    SUPPORTED_MCP_MODES,
)

# Click validates --mode before any of our code runs, so a hardcoded list here
# rejects a mode the server supports no matter what the rest of the stack
# knows. Three copies of this list sat at six modes while the server shipped
# nine, which is how `lex ai-verify --mode brief` came to fail with "not one
# of" rather than verifying anything. Derived, like every other roster.
#
# Only setup-with-ai still needs it. The `ai-*` commands build their choices
# from `payload.MODE_TO_PACKAGE` directly, in the package that defines it --
# see _LexGroup below.
_MODE_CHOICES = list(SUPPORTED_MCP_MODES)


def _require_lex_mcp(module_name: str):
    """Import a submodule of ``lex_mcp`` or raise a Click error.

    The AI commands (ai-dashboard, ai-faq, ai-update, ai-verify, ai-issue-report)
    now live in the ``lex-mcp-local`` package. It is installed by
    ``lex setup-with-ai``; if the user runs one of these commands before
    running setup, we surface a clear, actionable error.
    """
    import importlib

    try:
        return importlib.import_module(f"lex_mcp.{module_name}")
    except ImportError as exc:
        try:
            importlib.import_module("lex_mcp")
        except ImportError:
            raise click.ClickException(
                "lex-mcp-local is not installed in the active environment. "
                "Run `lex setup-with-ai` first to install it, then retry this "
                "command."
            ) from exc
        # The package is there, just older than this lex-app -- the module or a
        # name inside it does not exist yet. Sending the user to setup-with-ai
        # here is a dead end; the thing that fixes it is the upgrade.
        raise click.ClickException(
            f"This lex-app needs a newer lex-mcp-local than the one installed "
            f"(lex_mcp.{module_name} is unavailable). Run `lex ai-update` to "
            "upgrade it, then retry this command."
        ) from exc

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

class _LexGroup(click.Group):
    """A click group that lets lex-mcp-local define its own commands.

    Everything under `lex ai-*` is implemented in lex-mcp-local, and its
    options are declared there too. lex-app used to restate every flag in a
    ``@lex.command`` block here, which put the surface of a command in a
    different repository from the function it calls -- on lex-app's release
    cadence, which the customer drives. A new AI command was therefore
    unreachable until a customer took a whole framework upgrade, even though
    `lex ai-update` had already installed the code implementing it.

    So an `ai-*` name this file has never heard of is not an error: it is
    resolved against the installed package, which owns the flags, the help
    text and the exit code. Adding a command is a lex-mcp-local release.

    `setup-with-ai` and `ai-update` are the exceptions, and keep their own
    blocks below. Both must work *before* lex-mcp-local exists on disk, and
    `ai-update` is the recovery path when the installed one is too old to have
    the registry -- so it must not be resolved through the registry.
    """

    #: Names resolved by delegation rather than registration. Both separators:
    #: `ai_verify` and `ai_issue_report` are what the older docs and the support
    #: macros tell people to type, and each used to need a second hidden click
    #: command here duplicating the whole option block.
    _DELEGATED_PREFIXES = ("ai-", "ai_")

    def get_command(self, ctx, cmd_name):
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command
        if cmd_name.startswith(self._DELEGATED_PREFIXES):
            return self._delegated_command(cmd_name)
        return None

    def list_commands(self, ctx):
        names = set(super().list_commands(ctx))
        try:
            names.update(_require_lex_mcp("cli").command_names())
        except click.ClickException:
            # Not installed, or too old to have the registry. `setup-with-ai`
            # and `ai-update` are static and still listed, which are the two a
            # user needs before anything else here can work.
            pass
        return sorted(names)

    @staticmethod
    def _delegated_command(cmd_name):
        # The summary comes from the registry rather than from a docstring
        # here: `lex --help` has to describe a command this file does not
        # define, and listing must not import the module that does -- one of
        # them is the 1800-line dashboard.
        try:
            summary = _require_lex_mcp("cli").short_help(cmd_name)
        except click.ClickException:
            summary = ""

        # help_option_names=[] is load-bearing: without it click answers
        # `lex ai-verify --help` here, from a command that declares no options,
        # instead of letting the package that owns them render its own help.
        @click.command(
            name=cmd_name,
            short_help=summary,
            context_settings=dict(
                ignore_unknown_options=True,
                allow_extra_args=True,
                help_option_names=[],
            ),
            add_help_option=False,
        )
        @click.pass_context
        def _delegate(ctx):
            lex_mcp_cli = _require_lex_mcp("cli")
            try:
                exit_code = lex_mcp_cli.dispatch(cmd_name, ctx.args)
            except lex_mcp_cli.UnknownCommand:
                raise click.ClickException(
                    f"`lex {cmd_name}` is not a command this lex-mcp-local "
                    f"provides. Run `lex ai-update` to upgrade it, or "
                    f"`lex --help` for the ones it does."
                ) from None
            ctx.exit(exit_code)

        return _delegate


lex = _LexGroup(help="lex-app Command Line Interface")

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
    from generate_pycharm_configs import generate_run_configs

    generated_ide_configs = generate_run_configs(project_root)
    (Path(project_root) / Path("migrations")).mkdir(exist_ok=True, parents=True)
    (Path(project_root) / Path("migrations") / Path("__init__.py")).touch(exist_ok=True)
    return generated_ide_configs


def _echo_generated_config_paths(project_root: str, generated_ide_configs):
    if "pycharm" in generated_ide_configs:
        click.echo(f".run: {os.path.join(project_root, '.run')} (updated)")
    if "vscode" in generated_ide_configs:
        click.echo(
            ".vscode/launch.json: "
            f"{os.path.join(project_root, '.vscode', 'launch.json')} (updated)"
        )

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

def _warn_if_sessions_are_not_durable() -> None:
    """Fail on the main thread rather than inside the uvicorn worker.

    ``lex/proxy.py`` is the authority on these rules and raises on import. But
    it is imported *in the proxy thread*, where a RuntimeError would kill only
    uvicorn and leave Streamlit running and unreachable -- a confusing failure.
    Checking the same environment here turns it into a readable CLI error.
    Deliberately duplicated, and small enough to stay in step; if you change a
    rule, change it in both places.
    """
    public_url = (os.getenv("STREAMLIT_URL") or os.getenv("BASE_URL") or "").rstrip("/")
    # Lowercased, because proxy.py derives the same fact through
    # ``httpx.URL(...).scheme``, which normalises case. Comparing
    # case-sensitively here let ``HTTPS://host`` pass the pre-check and then
    # raise at import -- the pre-check disagreeing with the rule it mirrors.
    is_https = public_url.lower().startswith("https://")
    has_secret = bool(
        os.getenv("SESSION_SECRET")
        or os.getenv("SESSION_KEY")
        or os.getenv("SESSION_SECRET_KEY")
    )
    allow_ephemeral = (
        os.getenv("LEX_ALLOW_EPHEMERAL_SESSION_SECRET", "").strip().lower()
        in ("1", "true", "yes", "y", "on")
    )

    if not has_secret and is_https and not allow_ephemeral:
        raise click.ClickException(
            "SESSION_SECRET is not set, but STREAMLIT_URL/BASE_URL is https, so this "
            "looks like a real deployment. Session cookies would be signed with a "
            "random per-process value: every restart, and every request that lands on "
            "another replica, would silently log all users out and reset their "
            "dashboard state. Set SESSION_SECRET to a fixed value shared by all "
            "replicas, or set LEX_ALLOW_EPHEMERAL_SESSION_SECRET=true to proceed "
            "anyway (single-process development only)."
        )

    replicas = os.getenv("LEX_PROXY_REPLICAS", "1") or "1"
    has_redis = bool(os.getenv("TOKEN_REDIS_URL") or os.getenv("REDIS_URL"))
    try:
        replicated = int(replicas.strip()) > 1
    except ValueError:
        # Matches proxy.py's `_env_int`, which warns and falls back to 1 rather
        # than raising. The two must agree or this pre-check stops being one.
        click.echo(
            f"Warning: LEX_PROXY_REPLICAS={replicas!r} is not an integer; assuming 1.",
            err=True,
        )
        replicated = False
    if replicated and not has_redis:
        raise click.ClickException(
            f"LEX_PROXY_REPLICAS={replicas} but no TOKEN_REDIS_URL/REDIS_URL is set. "
            "The token store would be process-local, so a request routed to another "
            "replica would find no session and return 401."
        )

    # proxy.py raises on these too, and its raise lands in the worker thread
    # where it kills only the proxy. Mirror every rule, not just the first two.
    samesite = (os.getenv("SESSION_SAMESITE") or "").strip().lower()
    if samesite and samesite not in {"lax", "strict", "none"}:
        raise click.ClickException(
            f"SESSION_SAMESITE must be one of lax|strict|none, got {samesite!r}."
        )

    https_only_raw = (os.getenv("SESSION_HTTPS_ONLY") or "").strip().lower()
    https_only = (
        https_only_raw in ("1", "true", "yes", "y", "on") if https_only_raw else is_https
    )
    effective_samesite = samesite or ("none" if is_https else "lax")
    if effective_samesite == "none" and not https_only:
        raise click.ClickException(
            "SESSION_SAMESITE=none requires Secure cookies, but SESSION_HTTPS_ONLY is false. "
            "Browsers discard such cookies, so no session would ever be established. Serve "
            "the proxy over HTTPS, or set SESSION_SAMESITE=lax and keep the frontend and "
            "Streamlit on one registrable domain."
        )

    if not has_secret:
        click.echo(
            "Warning: SESSION_SECRET is not set; sessions will not survive a restart.",
            err=True,
        )
    if not has_redis:
        click.echo(
            "Warning: no TOKEN_REDIS_URL/REDIS_URL set; the token store is in-memory "
            "and will not survive a restart.",
            err=True,
        )


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

    proxy_port = os.environ.setdefault("LEX_PROXY_PORT", "8501")
    disconnected_session_ttl = os.environ.setdefault(
        "LEX_STREAMLIT_DISCONNECTED_SESSION_TTL", "600"
    )

    # Shared secret for the proxy's /auth/token endpoint, which is how the
    # dashboard renews the access token it was handed at connect time. Minted
    # here, before either half starts, so both read the same value out of the
    # environment they share -- the proxy is imported as top-level ``proxy`` in
    # the uvicorn thread while Streamlit imports ``lex.streamlit_app``, so they
    # are separate module objects and cannot share a Python-level constant.
    os.environ.setdefault("LEX_INTERNAL_AUTH_SECRET", secrets.token_urlsafe(32))

    _warn_if_sessions_are_not_durable()

    # `uvicorn.run()` would install signal handlers, which only the main thread
    # may do; modern uvicorn no-ops that off-thread, but it also gives us no
    # handle on the server, so there is no way to ask it to stop. Building the
    # Server here keeps that handle, which is what makes the shutdown below
    # possible: setting `should_exit` lets `serve()` return normally, so its
    # lifespan shutdown runs -- closing the pooled upstream client and sending
    # every open WebSocket a real close frame instead of having the daemon
    # thread killed from under them.
    proxy_server = uvicorn.Server(
        uvicorn.Config("proxy:app", host="0.0.0.0", port=int(proxy_port), loop="asyncio")
    )

    def run_uvicorn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(proxy_server.serve())

    t = threading.Thread(target=run_uvicorn, daemon=True)
    t.start()

    try:
        streamlit_main(
            streamlit_args
            + [
                "--browser.serverPort", proxy_port,
                "--server.port", "8080",
                # Streamlit keeps a disconnected session -- st.session_state,
                # uploaded files -- for this long, and resumes it if the same
                # client reconnects carrying its session id. The default is
                # 120s, which is shorter than a Keycloak round trip that has to
                # show a login form, so a re-auth came back to an evicted
                # session and landed the user on the first page with their work
                # gone. Defence in depth: with renewal working the document
                # should never reload, but a blip still drops the socket.
                "--server.disconnectedSessionTTL", str(disconnected_session_ttl),
            ]
        )
    finally:
        # Streamlit has stopped, so let the proxy finish properly rather than
        # dying with the process. Bounded: a hung shutdown must not stop the
        # command exiting.
        proxy_server.should_exit = True
        t.join(timeout=float(os.getenv("LEX_PROXY_SHUTDOWN_TIMEOUT", "5")))


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
    #   2. DiscoverRunner.setup_databases() — runs migrations and re-points
    #      ``connection.settings_dict["NAME"]`` so any TestCase /
    #      TransactionTestCase / SimpleTestCase subclass that touches the ORM
    #      hits the configured test DB.
    # Without this, every unittest.TestCase-derived test errors at setUp with
    # `database "<prod name>" does not exist` because Django's runner machinery
    # never ran.
    #
    # ``keepdb=True`` on purpose: the K8S/GCP/DOCKER settings set
    # ``TEST["NAME"] == DATABASE_NAME`` (the instance's own DB), so on a
    # deployed instance the "test DB" already exists — keepdb reuses it and
    # runs migrations against it (no DROP/CREATE), matching the pre-cutover
    # behaviour where pytest ran directly against the live/clone DB. This needs
    # no CREATEDB / database-ownership privilege on the app role. In CI, where
    # the DB is missing, setup_databases still CREATEs it (the CI Postgres user
    # is a superuser). With ``keepdb=False`` Django instead force-drops and
    # recreates ``DATABASE_NAME`` on every run, which requires CREATEDB +
    # ownership the Terraform-provisioned app role does not have (and would be
    # dropping the instance's own database) — see rc184 regression / af2a6ea.
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    _db_runner = DiscoverRunner(verbosity=1, interactive=False, keepdb=True)
    # Limit test-DB creation to the `default` alias. Django's own
    # ``manage.py test`` path inspects every collected ``TestCase`` for
    # its ``databases`` attribute (defaults to ``{"default"}``) and
    # passes that set in here, so unused aliases like ``GCP`` / ``K8S``
    # / ``DOCKER-COMPOSE`` are skipped. We bypass that discovery (pytest
    # owns collection now), so without ``aliases=`` Django would try to
    # CREATE test_<name> on every alias and fail on the ones whose host
    # env vars aren't set in CI (e.g. ``GCP`` resolves to host
    # ``envvar_not_existing`` → DNS error).
    _old_db_config = _db_runner.setup_databases(aliases={"default"})

    # Make ``register_converter`` idempotent for the duration of the pytest
    # run. ``lex/process_admin/sites/process_admin_site.py:_get_urls`` calls
    # ``register_converter(create_model_converter(...), "model")`` every time
    # ``processAdminSite.urls`` is accessed, and Django's ``register_converter``
    # raises ``ValueError: Converter 'model' is already registered.`` on the
    # second call. With ``lex test`` only one cluster ran per invocation so
    # the issue rarely surfaced, and ``E2ETestCase._rebuild_urls`` already
    # installs a local idempotent patch around its own reload of
    # ``lex_app.urls``. Pytest now runs every cluster in one process; a
    # plain ``TestCase`` (e.g. ``TestCluster01p_UrlConfResolves``) calling
    # ``reverse()`` after an E2E test reloaded ``lex_app.urls`` hits the
    # unpatched register and aborts collection. Installing the patch at the
    # runner level — once, around ``pytest.main()`` — covers every test
    # class (E2E and plain alike) without modifying framework code, and
    # matches the existing pattern in ``_e2e_test_case.py`` /
    # ``test_user_model_registration.py`` / ``test_api_user_journey.py`` /
    # ``test_bitemporal.py``.
    from django.urls import converters as _django_converters
    from django.urls.converters import REGISTERED_CONVERTERS as _REGISTERED_CONVERTERS
    from unittest.mock import patch as _patch

    _real_register_converter = _django_converters.register_converter

    def _idempotent_register_converter(converter, type_name):
        _REGISTERED_CONVERTERS.pop(type_name, None)
        return _real_register_converter(converter, type_name)

    _converter_patch = _patch(
        "lex.process_admin.sites.process_admin_site.register_converter",
        new=_idempotent_register_converter,
    )
    _converter_patch.start()

    started_at = time.perf_counter()
    try:
        exit_code = _pytest.main(forwarded, plugins=[plugin])
    except LexTestConfigError as exc:
        # Raised from pytest_collection_modifyitems on marker/group mismatch.
        raise click.ClickException(str(exc)) from exc
    finally:
        try:
            _converter_patch.stop()
        except Exception:
            pass
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
    generated_ide_configs = generate_configs(root)
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    _echo_generated_config_paths(root, generated_ide_configs)


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
    # Derived, not restated — the same reason as _MODE_CHOICES above. Naming a
    # mode here is an explicit choice, so this flag needs no override gate the way
    # the form's grid does; it only needs to agree on where "unchosen" lands.
    default=DEFAULT_LEX_MCP_MODE,
    show_default=True,
    type=click.Choice(_MODE_CHOICES, case_sensitive=False),
    help="MCP workflow mode. Determines which agent payload is delivered and "
         "which mode the server runs in (written to LEX_MCP_MODE in .env and "
         "every MCP config). Defaults to brief, which interviews you and then "
         "switches to the mode the work needs.",
)
@click.option(
    "-e",
    "--environment",
    "environments",
    multiple=True,
    help=(
        "Agentic environment(s) to onboard: pycharm-copilot, vscode-copilot, "
        "copilot-cli, cursor, claude-code, codex, windsurf, or 'all'. "
        "Repeatable. When omitted, the browser form lets you pick (with "
        "detected tools pre-selected); with --no-browser the default is "
        "pycharm-copilot."
    ),
)
@click.option(
    "--list-environments",
    is_flag=True,
    help="Print the supported agentic environments and exit.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Skip the local setup page and prompt in the terminal instead.",
)
def setup_with_ai(
    project_root,
    github_token,
    remote_mcp_api_key,
    remote_mcp_url,
    mcp_mode,
    environments,
    list_environments,
    no_browser,
):
    from lex.tools.setup_with_ai import (
        describe_ai_environments,
        normalize_ai_environments,
        suggest_ai_environments,
    )

    if list_environments:
        click.echo("Supported agentic environments:")
        for entry in describe_ai_environments():
            aliases = entry.get("aliases") or []
            alias_text = f" (aliases: {', '.join(aliases)})" if aliases else ""
            click.echo(f"  {entry['key']:<16} {entry['display_name']}{alias_text}")
            summary = entry.get("summary")
            if summary:
                click.echo(f"                   {summary}")
        return

    # The LLM agent's working directory IS the project root for setup-with-ai.
    # Do not walk up to a git toplevel / marker file: the LLM often runs
    # inside a subdirectory of a larger checkout, and walking up causes
    # docs/ and .github/ to be written into an ancestor (or be skipped
    # entirely when that ancestor is the lex package itself).
    root = resolve_llm_working_directory(project_root)
    python_executable = resolve_active_python_executable(root)

    env_path, created = ensure_env_file(root.as_posix())
    generated_ide_configs = generate_configs(root.as_posix())
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    _echo_generated_config_paths(root.as_posix(), generated_ide_configs)

    # An explicit --environment wins outright; otherwise the browser form
    # offers what is installed here and the terminal path falls back to the
    # historically supported target.
    cli_environments = (
        normalize_ai_environments(list(environments)) if environments else ()
    )
    # Auto-detection only pre-selects checkboxes in the browser form. A
    # non-interactive run must never silently write into every tool installed
    # on the machine, so it falls back to the long-standing default.
    suggested = cli_environments or (
        () if no_browser else suggest_ai_environments(root)
    )
    credentials = _collect_setup_with_ai_credentials(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        project_root=root,
        env_file_path=Path(env_path),
        no_browser=no_browser,
        suggested_environments=suggested,
    )

    # The browser form lets the user pick forward/backward; use that choice
    # unless the CLI flag was explicitly set by the caller.
    effective_mcp_mode = credentials.mcp_mode if credentials.mcp_mode else mcp_mode
    effective_environments = cli_environments or normalize_ai_environments(
        credentials.environments
    )
    click.echo(f"MCP workflow mode: {effective_mcp_mode}")
    click.echo(f"Agentic environments: {', '.join(effective_environments)}")

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
            environments=effective_environments,
            python_executable=python_executable,
            verify_server=False,
        )
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc

    server_probe = None
    server_probe_warning: str | None = None
    copilot_state_db_path = None
    copilot_state_warning: str | None = None
    click.echo(f"Validating {artifacts.server_name} over a real MCP stdio session...")
    try:
        server_probe = probe_lex_mcp_local_server(
            project_root=root,
            python_executable=artifacts.python_executable,
            wrapper_script_path=artifacts.wrapper_script_path,
            server_name=artifacts.server_name,
            env_values=build_ai_env_values(
                github_token=credentials.github_token,
                remote_mcp_api_key=credentials.remote_mcp_api_key,
                remote_mcp_url=remote_mcp_url,
                mcp_mode=effective_mcp_mode,
                environments=effective_environments,
            ),
        )
    except SetupWithAIError as exc:
        server_probe_warning = str(exc)
    else:
        # Only JetBrains Copilot keeps a tools/list cache that has to be
        # primed; every other environment re-probes on its own.
        if "pycharm-copilot" in effective_environments:
            try:
                copilot_state_db_path = bootstrap_github_copilot_mcp_server_for_pycharm(
                    server_probe,
                    mcp_config_path=artifacts.mcp_config_path,
                    server_name=artifacts.server_name,
                )
            except SetupWithAIError as exc:
                copilot_state_warning = str(exc)

    click.echo(f"Updated .env with AI credentials: {artifacts.env_file_path}")
    if artifacts.payload_files_written:
        click.echo(
            f"Delivered agent payload: "
            f"{len(artifacts.payload_files_written)} file(s) written across "
            f"{len(artifacts.environments)} environment(s)."
        )
    for config_path in artifacts.mcp_config_paths:
        click.echo(f"Registered {artifacts.server_name} in: {config_path}")
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
        elif copilot_state_warning is not None:
            click.echo(f"Warning: {copilot_state_warning}")
            click.echo(
                "The Copilot tool-list cache could not be primed automatically; "
                "restarting the IDE will refresh it."
            )
    elif server_probe_warning is not None:
        click.echo(
            "Warning: "
            f"{server_probe_warning} Your IDE may still be able to launch the "
            "server from its MCP config on demand."
        )

    # Final asset sweep: make sure every required directory (docs, .github,
    # ...) is on disk and up to date. Catches the case where an earlier
    # plain `lex setup` left the project partially initialized, or where a
    # copy step silently no-op'd.
    click.echo("Verifying AI asset directories...")
    # Imported here, not at module scope: verification now ships with
    # lex-mcp-local, which the lines above have only just installed. A
    # top-level import would make every `lex` command fail on a machine that
    # has never run setup.
    verify_result = None
    try:
        # Resolved inside the try on purpose. Setup installs whatever the index
        # currently serves, and that can be older than this lex-app -- during a
        # release window it always is. Letting the lookup escape aborts setup
        # after the credentials, the server registration and the docs are
        # already in place, which leaves a project half configured over
        # something the very next command fixes.
        verify_ai_assets = _require_lex_mcp("ai_assets").verify_ai_assets
        verify_result = verify_ai_assets(
            project_root=root,
            mode=effective_mcp_mode,
            environments=effective_environments,
        )
    except (SetupWithAIError, click.ClickException) as exc:
        message = getattr(exc, "message", None) or str(exc)
        click.echo(f"Warning: AI asset verification was skipped: {message}")
        click.echo(
            "  Setup itself completed. Run `lex ai-update` to finish "
            "delivering the agent assets."
        )

    if verify_result is not None:
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

    click.echo("Setup complete. Next steps for each environment you selected:")
    for note in artifacts.environment_notes:
        click.echo(f"  - {note}")
    if not artifacts.environment_notes:
        click.echo("  - Open your AI assistant and write your first prompt.")


@lex.command(name="ai-update", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def ai_update(project_root):
    """Apply incremental updates to an existing LEX AI setup.

    A bootstrap only: upgrade lex-mcp-local, then hand off to
    ``python -m lex_mcp.ai_update`` in a fresh process so the migration steps
    that run are the ones the upgrade just installed. What an update actually
    does -- and what it reports -- lives in lex-mcp-local, which is why a new
    migration no longer needs a lex-app release to reach a customer.
    """
    # The directory given (or the cwd) IS the project, exactly as
    # setup-with-ai and ai-verify treat it. This used to walk up to a git
    # toplevel or marker file, which meant update delivered the agent
    # payload somewhere setup had never written -- a project without its
    # own marker got .github and docs copied into its parent.
    root = resolve_llm_working_directory(project_root)

    try:
        exit_code = run_ai_update_bootstrap(root, reporter=click.echo)
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Could not upgrade lex-mcp-local (pip exited {exc.returncode})."
        ) from exc

    if exit_code:
        raise click.ClickException(
            f"LEX AI update failed (exit code {exit_code}). See the output above."
        )




def _collect_setup_with_ai_credentials(
    *,
    github_token: str | None,
    remote_mcp_api_key: str | None,
    project_root: Path,
    env_file_path: Path,
    no_browser: bool,
    suggested_environments: tuple[str, ...] = (),
) -> SetupWithAICredentials:
    from lex.tools.setup_with_ai import (
        DEFAULT_AI_ENVIRONMENT,
        normalize_ai_environments,
    )

    fallback_environments = normalize_ai_environments(
        list(suggested_environments) or [DEFAULT_AI_ENVIRONMENT]
    )

    if github_token and remote_mcp_api_key:
        return SetupWithAICredentials(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
            environments=fallback_environments,
        )

    if not no_browser:
        click.echo("Opening the local AI setup page in your browser...")
        try:
            return launch_setup_with_ai_form(
                project_root=project_root,
                env_file_path=env_file_path,
                reporter=click.echo,
                suggested_environments=suggested_environments or None,
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
        environments=fallback_environments,
    )

# Commands that have dedicated handlers and do NOT need Django management
# command enumeration.  For these, _bootstrap_django() is skipped so that
# django.setup() (and every AppConfig.ready()) only fires once — inside
# the actual server process (uvicorn / celery worker / streamlit).
#: ai-update is named here rather than left to the `ai-` prefix rule below.
#: It is lex-app's own command and must stay skipped whatever happens to that
#: rule -- it is the recovery path when the installed lex-mcp-local is too old
#: for anything else here to work.
_SKIP_BOOTSTRAP_COMMANDS = frozenset(
    {"start", "celery", "celery-workers", "flower", "pytest", "pytest-groups", "setup", "setup-with-ai", "ai-update"}
)


def _should_skip_django_bootstrap(command_name: str | None) -> bool:
    """Return True when *command_name* is handled directly by Click.

    Every `ai-*` name skips, including ones this file has never heard of. This
    gate reads ``sys.argv[1]`` *before* click runs, so it cannot ask the group
    whether the name resolves -- and a command lex-mcp-local defines would
    otherwise fall through to ``django.setup()`` and fail in a directory that
    is not a Lex app yet, which is the situation most AI commands exist to fix.

    Both separators, because `ai_verify` and `ai_issue_report` are still
    spellings people type.
    """
    if command_name is None:
        return False

    if command_name.startswith(("ai-", "ai_")):
        return True

    normalized_names = {
        command_name,
        command_name.replace("_", "-"),
        command_name.replace("-", "_"),
    }
    return any(name in _SKIP_BOOTSTRAP_COMMANDS for name in normalized_names)


def _force_utf8_console() -> None:
    """Make our own output encodable, whatever the console code page is.

    On Windows a redirected or piped stdout is opened with the locale code page
    — cp1252 on a German or English image — and printing a path it cannot
    represent raises ``UnicodeEncodeError`` and exits 1. That is not exotic:
    ``ı ğ ş`` are absent from cp1252, so are Cyrillic and CJK, and the very
    first thing ``lex setup-with-ai`` does in a fresh project is print the list
    of files it restored. An IDE or a CI job captures stdout, which is exactly
    the case that gets the code page instead of a console.

    POSIX is already UTF-8, so this is a no-op there; ``errors="replace"``
    means a stray unencodable byte degrades to a visible placeholder rather
    than killing the command. Guarded because stdout is not always a
    reconfigurable text stream (pytest's capture, for one).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


def main():
    _force_utf8_console()

    argv = sys.argv[1:]
    first_arg = argv[0] if argv else None

    # Top-level help/version requests must never bootstrap Django — Django
    # setup can fail in directories whose name is not a valid Python
    # identifier (e.g. "release-smoke"), and `lex --help` should always
    # work regardless of CWD.
    if first_arg is None or first_arg in {"--help", "-h", "--version"}:
        return lex(prog_name="lex")

    if _should_skip_django_bootstrap(first_arg):
        # These commands have dedicated Click handlers registered above.
        # Do NOT call _install_dynamic_commands() — that would trigger
        # django.setup() in the CLI process, causing every AppConfig.ready()
        # to fire twice (once here, once when the real server starts).
        return lex(prog_name="lex")

    # All other commands (including init, migrate, makemigrations, …) need the full
    # set of Django management commands registered as Click sub-commands.
    _install_dynamic_commands()
    return lex(prog_name="lex")
