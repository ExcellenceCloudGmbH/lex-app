"""lex-app Command Line Interface."""
import os
import subprocess
import sys
import threading
import asyncio
from pathlib import Path

import click
import django
import uvicorn
from celery.bin.celery import celery as celery_main
from django.core.management import get_commands, call_command
from streamlit.web.cli import main as streamlit_main

LEX_APP_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.as_posix()
PROJECT_ROOT_DIR = Path(os.getcwd()).resolve()
sys.path.append(LEX_APP_PACKAGE_ROOT)

# The DJANGO_SETTINGS_MODULE has to be set to allow us to access django imports
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex_app.settings"
)
os.environ.setdefault(
    "PROJECT_ROOT", PROJECT_ROOT_DIR.as_posix()
)
os.environ.setdefault("LEX_APP_PACKAGE_ROOT", LEX_APP_PACKAGE_ROOT)

django.setup()

lex = click.Group()

def execute_django_command(command_name, args):
    """
    Generic handler to forward arguments and options to Django management commands.
    """
    # Forwarding the command to Django's call_command
    call_command(command_name, *args)


def add_click_command(command_name):
    """
    Dynamically creates a Click command that wraps a Django management command.
    """

    @lex.command(name=command_name, context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    ))
    @click.pass_context
    def command(ctx):
        # Passing all received arguments and options to the Django command
        execute_django_command(command_name, ctx.args)


# Retrieve and extend the list of Django management commands
commands = get_commands()

# Dynamically create and add a Click command for each Django management command
for command_name in commands.keys():
    add_click_command(command_name)

@lex.command(name="celery", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def celery(ctx):
    """Run the ASGI application with Uvicorn."""
    celery_args = ctx.args

    celery_main(celery_args)

@lex.command(name="streamlit", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def streamlit(ctx):
    """Run the ASGI application with Uvicorn."""
    streamlit_args = ctx.args
    file_index = next((i for i, item in enumerate(streamlit_args) if 'streamlit_app.py' in item), None)
    if file_index is not None:
        streamlit_args[file_index] = f"{LEX_APP_PACKAGE_ROOT}/{streamlit_args[file_index]}"

    def run_uvicorn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run(
            "proxy:app",
            host="0.0.0.0",
            port=8080,
            loop="asyncio",
        )

    t = threading.Thread(target=run_uvicorn, daemon=True)
    t.start()

    # Run Streamlit in main thread (required due to signal handlers)
    # Pass a sys.argv-style list to streamlit_main
    streamlit_main(streamlit_args + ["--browser.serverPort", "8080"] or ["run", f"{LEX_APP_PACKAGE_ROOT}/streamlit_app.py"])


    # uvicorn_args = '--host 0.0.0.0 --port 8080 proxy:app'.split()
    # 
    # print(streamlit_args)
    # command = ['run', '/home/syscall/LUND_IT/.venv/src/lex-app/lex/streamlit_app.py']
    # 
    # loop = asyncio.new_event_loop()
    # asyncio.set_event_loop(loop)
    # 
    # x = threading.Thread(target=uvicorn.main, args=(uvicorn_args,))
    # x.start()
    # 
    # streamlit_main(streamlit_args)

@lex.command(name="start", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def start(ctx):
    """Run the ASGI application with Uvicorn."""
    os.environ.setdefault(
        "CALLED_FROM_START_COMMAND", "True"
    )
    uvicorn_args = ctx.args
    uvicorn.main(uvicorn_args)


@lex.command(context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    ))
@click.pass_context
def init(ctx):
    for command in ["createcachetable", "makemigrations", "migrate"]:
        execute_django_command(command, ctx.args)


def main():
    lex(prog_name="lex")


if __name__ == "__main__":
    main()