#!/usr/bin/env python3
import os
from pathlib import Path
from xml.sax.saxutils import escape

from lex.tools.project_root import find_project_root  # shared utility


DEFAULT_CONFIG_ENVS = {"PYTHONUNBUFFERED": "1"}
CELERY_WORKER_COUNT_PROMPT = "$Prompt:Worker count:1$"


def _render_envs(envs):
    return "\n".join(
        f'      <env name="{escape(name)}" value="{escape(value)}" />'
        for name, value in envs.items()
    )


def _build_celery_workers_parameters():
    return f"celery-workers {CELERY_WORKER_COUNT_PROMPT}"


def generate_pycharm_configs(project_root=None):
    # Resolve against caller’s execution directory by default
    start = project_root or os.getcwd()
    project_root = os.path.abspath(find_project_root(start))

    runconfigs_dir = os.path.join(project_root, ".run")
    os.makedirs(runconfigs_dir, exist_ok=True)

    project_name = os.path.basename(project_root)
    env_file_path = os.path.join(project_root, ".env")
    env_files_option = (
        f'<option name="ENV_FILES" value="{env_file_path}" />'
        if os.path.exists(env_file_path) else
        '<option name="ENV_FILES" value="" />'
    )

    configs = {
        "Init.run.xml": {"name": "Init", "parameters": "init"},
        "Setup_With_AI.run.xml": {
            "name": "Setup With AI",
            "parameters": "setup-with-ai",
        },
        "Start.run.xml": {
            "name": "Start",
            "parameters": "start --reload --loop asyncio lex_app.asgi:application",
        },
        "Flower.run.xml": {
            "name": "Flower",
            "parameters": "flower",
        },
        "Celery_Worker.run.xml": {
            "name": "Celery Workers",
            "parameters": _build_celery_workers_parameters(),
            "envs": {
                "IS_RUNNING_IN_CELERY": "true",
                "CELERY_ACTIVE": "true",
            },
        },
        "Make_migrations.run.xml": {"name": "Make migrations", "parameters": "makemigrations"},
        "Migrate.run.xml": {"name": "Migrate", "parameters": "migrate"},
        "Streamlit.run.xml": {"name": "Streamlit", "parameters": "streamlit run streamlit_app.py"},
        "Create_DB.run.xml": {
            "name": "Create DB",
            "parameters": "create_db",
        },
        "Flush_DB.run.xml": {"name": "Flush DB", "parameters": "flush"},
        "Test_All.run.xml": {
            "name": "Test All (Coverage)",
            "parameters": "test lex.core.tests lex.audit_logging.tests lex.process_admin.tests lex.tests --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
        "Test_Core.run.xml": {
            "name": "Test Core (Coverage)",
            "parameters": "test lex.core.tests --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
        "Test_Audit.run.xml": {
            "name": "Test Audit (Coverage)",
            "parameters": "test lex.audit_logging.tests --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
        "Test_ProcessAdmin.run.xml": {
            "name": "Test ProcessAdmin (Coverage)",
            "parameters": "test lex.process_admin.tests --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
        "Test_Lex.run.xml": {
            "name": "Test Lex (Coverage)",
            "parameters": "test lex.tests --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
        "Test_Single.run.xml": {
            "name": "Test Single (Coverage)",
            "parameters": "test $Prompt:Test label (e.g. lex.core.tests.test_user_context.TestUserContext.test_empty_scopes):lex.tests$ --verbosity=2 --noinput --keepdb",
            "envs": {"CELERY_ACTIVE": "False"},
        },
    }

    print(f"Generating PyCharm run configurations in: {runconfigs_dir}")
    print(f"Project name: {project_name}")
    print(f"Project root: {project_root}")

    for filename, config in configs.items():
        envs = {**DEFAULT_CONFIG_ENVS, **config.get("envs", {})}
        content = f'''<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="{config['name']}" type="PythonConfigurationType" factoryName="Python">
    <module name="{project_name}" />
    {env_files_option}
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
{_render_envs(envs)}
    </envs>
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="{project_root}" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <EXTENSION ID="PythonCoverageRunConfigurationExtension" runner="coverage.py" />
    <option name="SCRIPT_NAME" value="lex" />
    <option name="PARAMETERS" value="{config['parameters']}" />
    <option name="SHOW_COMMAND_LINE" value="false" />
    <option name="EMULATE_TERMINAL" value="false" />
    <option name="MODULE_MODE" value="true" />
    <option name="REDIRECT_INPUT" value="false" />
    <option name="INPUT_FILE" value="" />
    <method v="2" />
  </configuration>
</component>'''
        path = os.path.join(runconfigs_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Generated: {filename}")

    print("\nPyCharm run configurations generated successfully!")
    if os.path.exists(env_file_path):
        print(f"[OK] Configurations will use .env file: {env_file_path}")
    else:
        print(f"[WARN] No .env file found at {env_file_path}")
        print("  Create one if you need environment variables for your project.")

if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(description="Generate PyCharm run configurations for lex-app projects")
    parser.add_argument("-p", "--project-root", help="Project root directory (default: execution directory)")
    args = parser.parse_args()
    try:
        generate_pycharm_configs(args.project_root)
    except Exception as e:
        print(f"Error generating configurations: {e}")
        sys.exit(1)
