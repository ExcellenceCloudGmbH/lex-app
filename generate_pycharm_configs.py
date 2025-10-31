#!/usr/bin/env python3
"""
Standalone script to generate PyCharm run configurations for lex-app projects.
This can be run independently of the setup.py installation process.
"""

# generate_pycharm_configs.py (top additions)
import os
import subprocess
from pathlib import Path

MARKERS = {".git", "pyproject.toml", "setup.cfg", "manage.py", "requirements.txt", ".idea", ".vscode"}

def find_project_root(start=None):
    # 1) Explicit override for determinism
    env = os.environ.get("LEX_PROJECT_ROOT")
    if env:
        return str(Path(env).resolve())

    base = Path(start or os.getcwd()).resolve()

    # 2) Git repository root
    try:
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

    # 3) Ascend to nearest marker
    for p in [base] + list(base.parents):
        if any((p / m).exists() for m in MARKERS):
            return str(p)

    # 4) Fallback: caller's PWD if it has markers (useful under pip)
    pwd = os.environ.get("PWD")
    if pwd:
        P = Path(pwd).resolve()
        if any((P / m).exists() for m in MARKERS):
            return str(P)

    # Last resort
    return str(base)

def generate_pycharm_configs(project_root=None):
    """Generate PyCharm run configurations in the specified or current directory"""
    
    if project_root is None:
        project_root = os.getcwd()
    
    # Ensure we have the absolute path
    project_root = find_project_root(project_root)
    project_root = os.path.abspath(project_root)
    
    # Create .run directory for PyCharm configurations
    runconfigs_dir = os.path.join(project_root, '.run')
    os.makedirs(runconfigs_dir, exist_ok=True)
    
    # Get the project name from the root directory
    project_name = os.path.basename(project_root)
    
    # Path to .env file in project root
    env_file_path = os.path.join(project_root, '.env')
    env_files_option = f'<option name="ENV_FILES" value="{env_file_path}" />' if os.path.exists(env_file_path) else '<option name="ENV_FILES" value="" />'
    
    # Configuration templates
    configs = {
        'Init.run.xml': {
            'name': 'Init',
            'parameters': 'Init'
        },
        'Start.run.xml': {
            'name': 'Start',
            'parameters': 'start --reload --loop asyncio lex_app.asgi:application'
        },
        'Make_migrations.run.xml': {
            'name': 'Make migrations',
            'parameters': 'makemigrations'
        },
        'Migrate.run.xml': {
            'name': 'Migrate',
            'parameters': 'migrate'
        },
        'Streamlit.run.xml': {
            'name': 'Streamlit',
            'parameters': 'streamlit run streamlit_app.py'
        },
        'Create_DB.run.xml': {
            'name': 'Create DB',
            'parameters': 'test lex.lex_app.logging.create_db.create_db --keepdb'
        },
        'Flush_DB.run.xml': {
            'name': 'Flush DB',
            'parameters': 'flush'
        }
    }
    
    print(f"Generating PyCharm run configurations in: {runconfigs_dir}")
    print(f"Project name: {project_name}")
    print(f"Project root: {project_root}")
    
    for filename, config in configs.items():
        config_content = f'''<component name="ProjectRunConfigurationManager">
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
        
        config_path = os.path.join(runconfigs_dir, filename)
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        
        print(f'✓ Generated: {filename}')
    
    print(f'\nPyCharm run configurations generated successfully!')
    if os.path.exists(env_file_path):
        print(f'✓ Configurations will use .env file: {env_file_path}')
    else:
        print(f'⚠ No .env file found at {env_file_path}')
        print(f'  Create one if you need environment variables for your project.')
    
    print(f'\nTo use these configurations:')
    print(f'1. Open your project in PyCharm')
    print(f'2. The run configurations should appear in the run/debug dropdown')
    print(f'3. If not visible, go to Run > Edit Configurations and import from .run directory')


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate PyCharm run configurations for lex-app projects')
    parser.add_argument('--project-root', '-p', 
                       help='Project root directory (default: current directory)',
                       default=None)
    
    args = parser.parse_args()
    
    try:
        generate_pycharm_configs(args.project_root)
    except Exception as e:
        print(f"Error generating configurations: {e}")
        sys.exit(1)