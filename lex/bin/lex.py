# lex/bin/lex.py
import os
import sys
import threading
import asyncio
from pathlib import Path

import click
import uvicorn

# Defer all Django imports until after we know we need them
# from django.core.management import get_commands, call_command
# import django

LEX_APP_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.as_posix()
PROJECT_ROOT_DIR = Path(os.getcwd()).resolve()
sys.path.append(LEX_APP_PACKAGE_ROOT)

DEFAULT_ENV = """KEYCLOAK_URL=https://auth.excellence-cloud.dev
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
"""

MARKERS = {".git", "pyproject.toml", "setup.cfg", "manage.py", "requirements.txt", ".idea", ".vscode"}

def find_project_root(start=None) -> str:
    base = Path(start or os.getcwd()).resolve()
    # Try Git top-level
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
    # Ascend looking for markers
    for p in [base] + list(base.parents):
        if any((p / m).exists() for m in MARKERS):
            return str(p)
    return str(base)

def ensure_env_file(project_root: str, content: str = DEFAULT_ENV):
    p = Path(project_root) / ".env"
    if p.exists():
        return str(p), False
    p.write_text(content, encoding="utf-8")
    return str(p), True

def generate_pycharm_configs(project_root: str):
    from generate_pycharm_configs import generate_pycharm_configs as gen
    gen(project_root)

lex = click.Group(help="lex-app Command Line Interface")

def _bootstrap_django():
    # Import Django lazily and initialize settings for forwarded commands
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("PROJECT_ROOT", PROJECT_ROOT_DIR.as_posix())
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", LEX_APP_PACKAGE_ROOT)
    import django
    django.setup()
    from django.core.management import get_commands, call_command  # noqa: F401
    return get_commands, call_command

def _forward_to_django(command_name, args):
    get_commands, call_command = _bootstrap_django()
    # Validate existence once; if unknown, let Django raise
    cmds = get_commands()
    if command_name not in cmds:
        # Preserve legacy surface: Django error message
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", command_name, *args])
        return
    call_command(command_name, *args)

# Dynamic passthrough: generate Click commands for all Django commands at runtime
@lex.command(name="__refresh__", hidden=True)  # internal helper
def __refresh__():
    _bootstrap_django()
    click.echo("Django loaded")

def _install_dynamic_commands():
    get_commands, _ = _bootstrap_django()
    for command_name in get_commands().keys():
        # Skip duplicates if reloaded
        if command_name in lex.commands:
            continue

        @lex.command(name=command_name, context_settings=dict(
            ignore_unknown_options=True,
            allow_extra_args=True,
        ))
        @click.pass_context
        def _cmd(ctx, __name=command_name):
            _forward_to_django(__name, ctx.args)

# Public: keep your existing specialized commands

@lex.command(name="celery", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def celery(ctx):
    from celery.bin.celery import celery as celery_main
    celery_main(ctx.args)

@lex.command(name="streamlit", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
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

@lex.command(name="start", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def start(ctx):
    os.environ.setdefault("CALLED_FROM_START_COMMAND", "True")
    uvicorn.main(ctx.args)

@lex.command(name="init", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def init(ctx):
    # Forward canonical sequence without 'interactive' on createcachetable
    for command in ["createcachetable", "makemigrations", "migrate"]:
        _forward_to_django(command, ctx.args)

# New: setup (does NOT bootstrap Django)
@lex.command(name="setup", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option("-p", "--project-root", help="Project root (default: execution dir)")
def setup(project_root):
    root = find_project_root(project_root or os.getcwd())
    env_path, created = ensure_env_file(root)
    generate_pycharm_configs(root)
    click.echo(f".env: {env_path} ({'created' if created else 'exists'})")
    click.echo(f".run: {os.path.join(root, '.run')} (updated)")

def main():
    # Install dynamic Django commands before dispatch, so `lex Init` works
    _install_dynamic_commands()
    lex(prog_name="lex")

if __name__ == "__main__":
    main()
