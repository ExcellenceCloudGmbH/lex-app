import os
import shutil
import sys
from pathlib import Path

from setuptools import setup, find_packages
from setuptools.command.install import install

with open("requirements.txt") as f:
    install_requires = f.read().splitlines()

class CustomInstallCommand(install):
    def run(self):
        # First, run the standard installation
        install.run(self)

        # Now handle the custom installation of other_directory
        self.move_other_directory()
        
        # Generate PyCharm run configurations
        self.generate_pycharm_configs()

    def move_other_directory(self):
        # Define the source and target paths
        source = os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')
        target = os.path.join(os.path.dirname(self.install_lib), 'generic_app')

        # Ensure the package_data entry points to the correct location
        if os.path.exists(target):
            shutil.rmtree(target)  # Remove the existing directory if it exists
        shutil.move(source, target)
        print(f'Moved other_directory to {target}')

    def generate_pycharm_configs(self):
        """Generate PyCharm run configurations in the project root"""
        # Find the project root (where pip install was run from)
        project_root = os.getcwd()
        
        # Check if we're in a virtual environment and adjust accordingly
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            # We're in a virtual environment, try to find the actual project root
            current_dir = Path(project_root)
            # Look for common project indicators
            for parent in [current_dir] + list(current_dir.parents):
                if any((parent / indicator).exists() for indicator in ['.env', 'manage.py', '.git', 'requirements.txt']):
                    project_root = str(parent)
                    break
        
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
            
            print(f'Generated PyCharm run configuration: {config_path}')
        
        print(f'PyCharm run configurations generated in: {runconfigs_dir}')
        if os.path.exists(env_file_path):
            print(f'Configurations will use .env file: {env_file_path}')
        else:
            print(f'No .env file found at {env_file_path}. Create one if needed for environment variables.')

setup(
    name="lex-app",
    version="2.0.0rc3",
    author="Melih Sünbül",
    author_email="m.sunbul@excellence-cloud.com",
    description="A Python / Django library to create business applications easily with complex logic",
    long_description_content_type="text/markdown",
    url="https://github.com/ExcellenceCloudGmbH/lex-app",
    packages=find_packages(),
    include_package_data=True,
    py_modules=['generate_pycharm_configs'],
    entry_points={
        "console_scripts": [
            "lex = lex.__main__:main",
            "lex-generate-configs = generate_pycharm_configs:generate_pycharm_configs",
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=install_requires,
    python_requires=">=3.6",
    cmdclass={
        'install': CustomInstallCommand,
    },
)
