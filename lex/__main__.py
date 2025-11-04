#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

from lex.tools.project_root import find_project_root
from generate_pycharm_configs import generate_pycharm_configs

# Legacy Django management command bridge
def run_legacy_management(cmd: str, argv: list[str]) -> int:
    # Defer import until needed to avoid Django setup when running pure CLI tasks
    from django.core.management import execute_from_command_line
    # Reconstruct argv as if called via "manage.py <cmd> <args...>"
    args = ["manage.py", cmd, *argv]
    return execute_from_command_line(args)

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

def cmd_setup(args):
    start = args.project_root or os.getcwd()
    root = find_project_root(start)
    env_path, created = ensure_env_file(root)
    generate_pycharm_configs(root)
    print(f".env: {env_path} ({'created' if created else 'exists'})")
    print(f".run: {os.path.join(root, '.run')} (updated)")

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Known first token subcommands
    known = {"setup"}

    # Back-compat: if first token is not a known subcommand, forward to Django
    if argv and argv[0] not in known:
        cmd = argv[0]
        rest = argv[1:]
        sys.exit(run_legacy_management(cmd, rest))

    parser = argparse.ArgumentParser(prog="lex", add_help=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="Generate .env and PyCharm run configs")
    p_setup.add_argument("-p", "--project-root", help="Project root (default: execution dir)")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()
