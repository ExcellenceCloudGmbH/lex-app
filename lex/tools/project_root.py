# lex/tools/project_root.py
import os
import subprocess
from pathlib import Path

MARKERS = {".git", "pyproject.toml", "setup.cfg", "manage.py", "requirements.txt", ".idea", ".vscode"}


def _is_safe_root(candidate: Path, home: Path) -> bool:
    """A directory is only an acceptable "project root" if it lives strictly
    below the user's home directory.

    We refuse to return the home directory itself or any of its ancestors
    (e.g. ``/``, ``/home``, ``C:\\Users``). Otherwise an innocent marker
    such as a stray ``.git`` or ``.idea`` in ``~`` would cause ``lex
    setup-with-ai`` to write ``.env``/``.github`` into the user's home dir
    when the real project lacks a marker (see issue: setup-with-ai
    relocating files to ``~`` when run from a marker-less venv folder).
    """
    if candidate == home:
        return False
    if candidate in home.parents:
        return False
    return True


def find_project_root(start=None) -> str:
    base = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve()

    # 1) Prefer the git toplevel, but only if it's a safe project root.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(base),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        toplevel = out.stdout.strip()
        if toplevel:
            candidate = Path(toplevel).resolve()
            if _is_safe_root(candidate, home):
                return str(candidate)
    except Exception:
        pass

    # 2) Walk up looking for a project marker, but stop at the user's
    #    home directory so we never silently relocate the project to ``~``.
    for p in [base] + list(base.parents):
        if not _is_safe_root(p, home):
            break
        if any((p / m).exists() for m in MARKERS):
            return str(p)

    # 3) Fall back to the starting directory. The caller (e.g. setup-with-ai)
    #    will operate on the directory the user actually invoked the command
    #    from instead of escaping into ``~`` or ``/``.
    return str(base)


def resolve_llm_working_directory(explicit_path: str | None = None) -> Path:
    """Return the literal directory the LLM agent is working in.

    For ``lex setup-with-ai`` and ``lex ai-verify`` the rule is simple and
    intentional: the directory passed via ``--project-root`` (or the current
    working directory when omitted) **is** the project root, exactly as the
    LLM sees it. We must not walk up to a git toplevel or marker file the way
    :func:`find_project_root` does, because the LLM commonly operates inside
    a subdirectory of a larger checkout (or inside a freshly created folder
    that has no markers yet). Walking up causes asset directories such as
    ``docs/`` and ``.github/`` to be written into an ancestor — or to be
    skipped entirely when that ancestor happens to be the ``lex`` package's
    own checkout (self-copy guard).
    """
    base = explicit_path if explicit_path else os.getcwd()
    return Path(base).expanduser().resolve()
