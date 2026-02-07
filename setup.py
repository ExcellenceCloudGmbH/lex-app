# setup.py
import os
import shutil
import sys
import subprocess
from pathlib import Path

from setuptools import setup, find_packages
from setuptools.command.install import install

with open("requirements.txt") as f:
    install_requires = f.read().splitlines()

MARKERS = {'.git', 'pyproject.toml', 'setup.cfg', 'manage.py', 'requirements.txt', '.idea', '.vscode'}


DEFAULT_ENV = """KEYCLOAK_URL=https://auth.excellence-cloud.dev
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
"""

class CustomInstallCommand(install):
    def find_project_root(self, start=None) -> str:
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

    def setup_project(self, project_root=None):
        root = self.find_project_root(project_root or os.getcwd())
        env_path, created = self.ensure_env_file(root)
        self.generate_configs(root)
        print(f".env: {env_path} ({'created' if created else 'exists'})")
        print(f".run: {os.path.join(root, '.run')} (updated)")

    def run(self):
        install.run(self)
        # self.move_other_directory()
        # self.generate_pycharm_configs()
        # self.generate_env_file()
        self.setup_project()

    def move_other_directory(self):
        source = os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')
        target = os.path.join(os.path.dirname(self.install_lib), 'generic_app')
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.move(source, target)
        print(f'Moved other_directory to {target}')


    def ensure_env_file(self, project_root: str, content: str = DEFAULT_ENV):
        p = Path(project_root) / ".env"
        if p.exists():
            return str(p), False
        p.write_text(content, encoding="utf-8")
        return str(p), True

    def generate_env_file(self):
        project_root = self._project_root()
        env_path = os.path.join(project_root, '.env')
        if os.path.exists(env_path):
            print(f'.env already exists at {env_path}, not modifying.')
            return
        content = """KEYCLOAK_URL=https://auth.excellence-cloud.de
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
"""
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def generate_configs(self, project_root: str):
        from generate_pycharm_configs import generate_pycharm_configs
        generate_pycharm_configs(project_root)
        (Path(project_root) / Path("migrations")).mkdir(exist_ok=True, parents=True)
        (Path(project_root) / Path("migrations") / Path("__init__.py")).touch(exist_ok=True)

setup(
    name="lex-app",
    version="2.0.0rc23",
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
    cmdclass={'install': CustomInstallCommand},
)
