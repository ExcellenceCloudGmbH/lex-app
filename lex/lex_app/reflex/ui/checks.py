"""What ``lex reflex check`` reports about lex.ui: handlers that leave the default path."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

_UI_DECORATORS = {"action", "job", "load"}


@dataclasses.dataclass(frozen=True)
class Finding:
    level: str          # "error", "warning" or "note"
    path: str
    line: int
    message: str


def _files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for item in paths:
        path = Path(item)
        out.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return out


def _dotted(node: ast.AST) -> str:
    """``ui.State`` / ``rx.event`` for an attribute chain, ``State`` for a name, '' otherwise."""
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else ""
    return node.id if isinstance(node, ast.Name) else ""


def _decorator(node: ast.AST) -> tuple[str, list[ast.keyword]]:
    """A decorator's dotted name and keywords, called or not."""
    if isinstance(node, ast.Call):
        return _dotted(node.func), list(node.keywords)
    return _dotted(node), []


def find(paths: list[str]) -> list[Finding]:
    """The lex.ui findings in the project files under ``paths``."""
    findings: list[Finding] = []
    for path in _files(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, UnicodeDecodeError, OSError) as exc:
            findings.append(Finding("error", str(path), getattr(exc, "lineno", None) or 1,
                                    f"{path.name}: cannot be analysed ({type(exc).__name__}); "
                                    "lex.ui handlers in it were not checked."))
            continue
        ui_states: set[str] = set()
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases = {_dotted(base) for base in cls.bases}
            if not (("ui.State" in bases) or (bases & ui_states)):
                continue
            ui_states.add(cls.name)
            for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                name = f"{cls.name}.{fn.name}"
                for node in fn.decorator_list:
                    dotted, keywords = _decorator(node)
                    if dotted in ("rx.event", "reflex.event"):
                        findings.append(Finding("error", str(path), fn.lineno,
                                                f"{name}: a raw @rx.event on a ui.State. Use @ui.action, @ui.job or "
                                                "@ui.load, or mark it @ui.custom."))
                    elif dotted == "ui.custom":
                        findings.append(Finding("note", str(path), fn.lineno, f"{name}: custom region (@ui.custom)."))
                    elif dotted.startswith("ui.") and dotted[3:] in _UI_DECORATORS:
                        requires = next((k.value for k in keywords if k.arg == "requires"), None)
                        if requires is None:
                            uses_manager = any(isinstance(n, ast.Attribute) and n.attr in ("objects", "_default_manager")
                                               for n in ast.walk(fn))
                            if uses_manager:
                                findings.append(Finding("warning", str(path), fn.lineno,
                                                        f"{name}: names a model manager without requires=. The query "
                                                        "will be refused at run time; use self.api or declare requires=."))
                        elif "ui.signed_in" in {_dotted(n) for n in ast.walk(requires)}:
                            findings.append(Finding("note", str(path), fn.lineno,
                                                    f"{name}: requires=ui.signed_in (any signed-in user)."))
    return findings


def report(findings: list[Finding]) -> int:
    """Print the findings; 1 when any is an error, else 0."""
    for finding in sorted(findings, key=lambda f: (f.path, f.line)):
        print(f"[{finding.level:<7}] {finding.path}:{finding.line}  {finding.message}")
    errors = sum(1 for finding in findings if finding.level == "error")
    print(f"lex.ui check: {errors} error(s), {sum(f.level == 'warning' for f in findings)} warning(s), "
          f"{sum(f.level == 'note' for f in findings)} listed for review")
    return 1 if errors else 0
