#!/usr/bin/env python3
import os
from pathlib import Path

from lex.tools.project_root import find_project_root  # shared utility

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
        "Init.run.xml": {"name": "Init", "parameters": "Init"},
        "Start.run.xml": {
            "name": "Start",
            "parameters": "start --reload --loop asyncio lex_app.asgi:application",
        },
        "Make_migrations.run.xml": {"name": "Make migrations", "parameters": "makemigrations"},
        "Migrate.run.xml": {"name": "Migrate", "parameters": "migrate"},
        "Streamlit.run.xml": {"name": "Streamlit", "parameters": "streamlit run streamlit_app.py"},
        "Create_DB.run.xml": {
            "name": "Create DB",
            "parameters": "test lex.lex_app.logging.create_db.create_db --keepdb",
        },
        "Flush_DB.run.xml": {"name": "Flush DB", "parameters": "flush"},
    }

    print(f"Generating PyCharm run configurations in: {runconfigs_dir}")
    print(f"Project name: {project_name}")
    print(f"Project root: {project_root}")

    for filename, config in configs.items():
        content = f'''<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="{config['name']}" type="PythonConfigurationType" factoryName="Python">
    <module name="{project_name}" />
    {env_files_option}
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
      <env name="PYTHONUNBUFFERED" value="1" />
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
