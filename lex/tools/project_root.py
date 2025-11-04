# lex/tools/project_root.py
import os, subprocess
from pathlib import Path

MARKERS = {".git", "pyproject.toml", "setup.cfg", "manage.py", "requirements.txt", ".idea", ".vscode"}

def find_project_root(start=None) -> str:
    base = Path(start or os.getcwd()).resolve()
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
    for p in [base] + list(base.parents):
        if any((p / m).exists() for m in MARKERS):
            return str(p)
    return str(base)
