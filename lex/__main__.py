#!/usr/bin/env python3
import os
import argparse
from pathlib import Path

from lex.tools.project_root import find_project_root  # shared utility
from generate_pycharm_configs import generate_pycharm_configs

DEFAULT_ENV = """KEYCLOAK_URL=https://auth.excellence-cloud.dev
KEYCLOAK_REALM=
OIDC_RP_CLIENT_ID=haze
OIDC_RP_CLIENT_SECRET=
OIDC_RP_CLIENT_UUID=
"""

def ensure_env_file(project_root: str, content: str = DEFAULT_ENV):
    p = Path(project_root) / ".env"
    if p.exists():
        return str(p), False
    p.write_text(content, encoding="utf-8")
    return str(p), True

def cmd_setup(args):
    # Start from execution directory by default; allow optional -p override
    start = args.project_root or os.getcwd()
    root = find_project_root(start)
    env_path, created = ensure_env_file(root)
    generate_pycharm_configs(root)
    print(f".env: {env_path} ({'created' if created else 'exists'})")
    print(f".run: {os.path.join(root, '.run')} (updated)")

def main(argv=None):
    parser = argparse.ArgumentParser(prog="lex", add_help=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="Generate .env and PyCharm run configs")
    p_setup.add_argument("-p", "--project-root", help="Project root (default: execution dir)")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()
