# lex/bin/lex.py
import os
import sys
import time
import platform
import subprocess
import threading
import asyncio
from pathlib import Path

import click
import uvicorn

from lex.tools.project_root import find_project_root
from lex.tools.setup_with_ai import (
    DEFAULT_REMOTE_MCP_URL,
    SetupWithAICredentials,
    SetupWithAIError,
    build_ai_env_values,
    configure_ai_integration,
    install_lex_mcp_local,
    launch_setup_with_ai_form,
    probe_lex_mcp_local_server_for_pycharm,
    resolve_active_python_executable,
)

# Defer Django imports and setup until needed (NOT at import time)
_DJANGO_READY = False
_GET_COMMANDS = None
_CALL_COMMAND = None

LEX_APP_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.as_posix()
PROJECT_ROOT_DIR = Path(find_project_root(os.getcwd())).resolve()
sys.path.append(LEX_APP_PACKAGE_ROOT)

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
@click.option("--gemini-api-key", help="Gemini API key exposed to lex-mcp-local.")
@click.option(
    "--remote-mcp-url",
    default=DEFAULT_REMOTE_MCP_URL,
    show_default=True,
    help="Remote MCP HTTP endpoint used by lex-mcp-local.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Skip the local setup page and prompt in the terminal instead.",
)
def setup_with_ai(project_root, github_token, remote_mcp_api_key, gemini_api_key, remote_mcp_url, no_browser):
    root = Path(find_project_root(project_root or os.getcwd())).resolve()
    python_executable = resolve_active_python_executable(root)

    env_path, created = ensure_env_file(root.as_posix())
    generate_configs(root.as_posix())
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    click.echo(f".run: {os.path.join(root.as_posix(), '.run')} (updated)")

    credentials = _collect_setup_with_ai_credentials(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        gemini_api_key=gemini_api_key,
        project_root=root,
        env_file_path=Path(env_path),
        no_browser=no_browser,
    )

    click.echo("Installing lex-mcp-local into the active virtual environment...")
    try:
        install_lex_mcp_local(python_executable, credentials.remote_mcp_api_key)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Failed to install lex-mcp-local into {python_executable}."
        ) from exc

    try:
        artifacts = configure_ai_integration(
            project_root=root,
            github_token=credentials.github_token,
            remote_mcp_api_key=credentials.remote_mcp_api_key,
            gemini_api_key=credentials.gemini_api_key,
            remote_mcp_url=remote_mcp_url,
            python_executable=python_executable,
            verify_server=False,
        )
    except SetupWithAIError as exc:
        raise click.ClickException(str(exc)) from exc

    server_probe = None
    server_probe_warning: str | None = None
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
                gemini_api_key=credentials.gemini_api_key,
                remote_mcp_url=remote_mcp_url,
            ),
        )
    except SetupWithAIError as exc:
        server_probe_warning = str(exc)

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
        click.echo(f"{artifacts.server_name} is configured for stdio launch from PyCharm on demand.")
    elif server_probe_warning is not None:
        click.echo(
            "Warning: "
            f"{server_probe_warning} GitHub Copilot may still be able to launch the server from mcp.json on demand."
        )
    click.echo(
        "Setup complete. Open GitHub Copilot in PyCharm and write your first prompt."
    )


def _collect_setup_with_ai_credentials(
    *,
    github_token: str | None,
    remote_mcp_api_key: str | None,
    gemini_api_key: str | None,
    project_root: Path,
    env_file_path: Path,
    no_browser: bool,
) -> SetupWithAICredentials:
    if github_token and remote_mcp_api_key and gemini_api_key:
        return SetupWithAICredentials(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
            gemini_api_key=gemini_api_key,
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
    final_gemini_api_key = gemini_api_key or click.prompt(
        "Gemini API key",
        hide_input=True,
    )
    return SetupWithAICredentials(
        github_token=final_github_token,
        remote_mcp_api_key=final_remote_mcp_api_key,
        gemini_api_key=final_gemini_api_key,
    )

# Commands that have dedicated handlers and do NOT need Django management
# command enumeration.  For these, _bootstrap_django() is skipped so that
# django.setup() (and every AppConfig.ready()) only fires once — inside
# the actual server process (uvicorn / celery worker / streamlit).
_SKIP_BOOTSTRAP_COMMANDS = frozenset(
    {"start", "celery", "celery-workers", "flower", "setup", "setup-with-ai"}
)


def main():
    argv = sys.argv[1:]
    first_arg = argv[0] if argv else None

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
