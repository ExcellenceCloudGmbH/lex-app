# lex/bin/lex.py
import os
import sys
import threading
import asyncio
from pathlib import Path

import click
import uvicorn

# Defer Django imports and setup until needed (NOT at import time)
_DJANGO_READY = False
_GET_COMMANDS = None
_CALL_COMMAND = None

LEX_APP_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.as_posix()
PROJECT_ROOT_DIR = Path(os.getcwd()).resolve()
sys.path.append(LEX_APP_PACKAGE_ROOT)

# KEEP: do not set DJANGO_SETTINGS_MODULE or call django.setup() here

lex = click.Group(help="lex-app Command Line Interface")

# ---------- Project root and configs (no Django) ----------

MARKERS = {".git", "pyproject.toml", "setup.cfg", "manage.py", "requirements.txt", ".idea", ".vscode"}

def find_project_root(start=None) -> str:
    base = Path(start or os.getcwd()).resolve()
    # Git root
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(base),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        pass
    # Marker ascent
    for p in [base] + list(base.parents):
        if any((p / m).exists() for m in MARKERS):
            return str(p)
    return str(base)

DEFAULT_ENV = """KEYCLOAK_URL=https://auth.excellence-cloud.dev
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
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
    # Configure env only now
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("PROJECT_ROOT", PROJECT_ROOT_DIR.as_posix())
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", LEX_APP_PACKAGE_ROOT)
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

@lex.command(name="celery", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def celery(ctx):
    from celery.bin.celery import celery as celery_main
    celery_main(ctx.args)

@lex.command(name="streamlit", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def streamlit(ctx):
    from streamlit.web.cli import main as streamlit_main
    streamlit_args = ctx.args
    file_index = next((i for i, item in enumerate(streamlit_args) if 'streamlit_app.py' in item), None)
    if file_index is not None:
        streamlit_args[file_index] = f"{LEX_APP_PACKAGE_ROOT}/{streamlit_args[file_index]}"

    def run_uvicorn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run("proxy:app", host="0.0.0.0", port=8080, loop="asyncio")

    t = threading.Thread(target=run_uvicorn, daemon=True)
    t.start()
    streamlit_main(streamlit_args + ["--browser.serverPort", "8080"] or ["run", f"{LEX_APP_PACKAGE_ROOT}/streamlit_app.py"])

@lex.command(name="start", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def start(ctx):
    os.environ.setdefault("CALLED_FROM_START_COMMAND", "True")
    uvicorn.main(ctx.args)

@lex.command(context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
def init(ctx):
    for command in ["createcachetable", "makemigrations", "migrate"]:
        _forward_to_django(command, ctx.args)

# ---------- New: setup (never bootstraps Django) ----------

@lex.command(name="setup", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def setup(project_root):
    root = find_project_root(project_root or os.getcwd())
    env_path, created = ensure_env_file(root)
    generate_configs(root)
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    click.echo(f".run: {os.path.join(root, '.run')} (updated)")

def main():
    # Detect if the first arg is 'setup'; if so, register only setup and dispatch without Django
    argv = sys.argv[1:]
    if argv and argv[0] == "setup":
        # Register only built-ins (already registered above), do not install dynamic commands
        return lex(prog_name="lex")
    # Otherwise, install dynamic Django commands and dispatch
    _install_dynamic_commands()
    return lex(prog_name="lex")
