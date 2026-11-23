"""What ``lex reflex check`` reports about lex.ui: handlers that leave the default path.

The analysis is static (``ast``): project code is never imported or run.

* Every public method (no leading ``_``) of a ``ui.State`` must be ``@ui.action``, ``@ui.job``,
  ``@ui.load`` or ``@ui.custom``; Reflex makes any other public method a handler the browser can
  call, so an undecorated one or a raw ``@rx.event`` is an error. Properties, static and class
  methods and computed vars are not handlers.
* Each file's imports are resolved first, so ``from lex import ui as u``, ``from lex.ui import
  State`` or ``from reflex import event`` are recognised like the plain spellings.
* A state is a ``ui.State`` when it has ``ui.State`` as a base, or a base that is a class of that
  kind anywhere in the files given. Bases are matched by their last name component, by name across
  the project: a class that shares the name of a ui state may be over-reported, never missed.
* A field, computed var or method of a ``ui.State`` named ``user``, ``api``, ``params``, ``can`` or
  ``state`` is an error: it would hide the body's helper of that name.
* ``self.<handler>(...)`` in a method of a ``ui.State``, where ``<handler>`` is a lex.ui handler of
  that state (its own, or a base's that the check follows), is an error: a handler called from
  another handler's body does not run. Shared work belongs in a method whose name starts with ``_``.
* A handler that reads ``.objects`` or ``._default_manager`` without ``requires=`` is a warning
  (best effort; the run-time block is the guarantee).

What it does not follow: a public attribute assigned a function (``refresh = lambda self: ...``)
is not seen as a method, and the methods a mixin brings in (a base that is not a ``ui.State``)
are not checked, nor are its handlers known when a state calls them.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

_UI_PACKAGES = ("lex.ui", "lex.lex_app.reflex.ui")
_UI_HANDLERS = {"ui.action", "ui.job", "ui.load", "ui.custom"}
_NOT_HANDLERS = {"property", "staticmethod", "classmethod", "reflex.var", "cached_property",
                 "functools.cached_property"}


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


def _imports(tree: ast.AST) -> dict[str, str]:
    """Local dotted name -> canonical name (``ui``, ``ui.State``, ``reflex``, ``reflex.event``...)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _UI_PACKAGES:
                    names[alias.asname or alias.name] = "ui"
                elif alias.name == "reflex" or alias.name.startswith("reflex."):
                    names[alias.asname or "reflex"] = "reflex" if alias.asname is None or alias.name == "reflex" \
                        else alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                local = alias.asname or alias.name
                if f"{node.module}.{alias.name}" in _UI_PACKAGES:
                    names[local] = "ui"
                elif node.module in _UI_PACKAGES:
                    names[local] = f"ui.{alias.name}"
                elif node.module == "reflex":
                    names[local] = f"reflex.{alias.name}"
    return names


def _resolve(node: ast.AST, names: dict[str, str]) -> str:
    """The canonical dotted name of ``node`` through the file's imports (unchanged when unknown)."""
    dotted = _dotted(node)
    parts = dotted.split(".")
    for size in range(len(parts), 0, -1):
        prefix = ".".join(parts[:size])
        if prefix in names:
            return ".".join([names[prefix], *parts[size:]])
    return dotted


def _decorator(node: ast.AST, names: dict[str, str]) -> tuple[str, list[ast.keyword]]:
    """A decorator's canonical name and keywords, called or not."""
    if isinstance(node, ast.Call):
        return _resolve(node.func, names), list(node.keywords)
    return _resolve(node, names), []


def _methods(cls: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _reserved(path: Path, cls: ast.ClassDef) -> tuple[list[Finding], set[str]]:
    """Fields, computed vars and methods of ``cls`` named like a body helper."""
    from lex.lex_app.reflex.ui.context import RESERVED

    out: list[Finding] = []
    found: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets = [node.name]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target.id] if isinstance(node.target, ast.Name) else []
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        else:
            targets = []
        for name in targets:
            if name in RESERVED:
                found.add(name)
                out.append(Finding("error", str(path), node.lineno,
                                   f"{cls.name}.{name}: '{name}' is reserved on a ui.State -- self.{name} is the "
                                   f"handler's helper, and a field or method of that name hides it. Rename it."))
    return out, found


def _handler_names(cls: ast.ClassDef, names: dict[str, str]) -> set[str]:
    """The methods of ``cls`` decorated with a lex.ui handler decorator."""
    return {fn.name for fn in _methods(cls)
            if any(_decorator(node, names)[0] in _UI_HANDLERS for node in fn.decorator_list)}


def _sibling_calls(path: Path, cls: ast.ClassDef, handlers: set[str]) -> list[Finding]:
    """``self.<handler>(...)`` inside a method of ``cls``."""
    out: list[Finding] = []
    for fn in _methods(cls):
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
                    and node.func.attr in handlers):
                attr = node.func.attr
                out.append(Finding("error", str(path), node.lineno,
                                   f"{cls.name}.{fn.name}: calls the handler self.{attr}(), which does not run "
                                   f"from a handler's body. Move the shared work into a method whose name starts "
                                   f"with '_' (self._{attr}()) and call it from both handlers."))
    return out


def _check_class(path: Path, cls: ast.ClassDef, names: dict[str, str], handlers: set[str] = frozenset()) -> list[Finding]:
    out, reserved = _reserved(path, cls)
    out.extend(_sibling_calls(path, cls, handlers))
    for fn in _methods(cls):
        if fn.name.startswith("_") or fn.name in reserved:
            continue
        name = f"{cls.name}.{fn.name}"
        decorators = [_decorator(node, names) for node in fn.decorator_list]
        if any(d in _NOT_HANDLERS or d.endswith((".setter", ".deleter", ".cached_property"))
               for d, _ in decorators):
            continue
        if not any(d in _UI_HANDLERS or d == "reflex.event" for d, _ in decorators):
            out.append(Finding("error", str(path), fn.lineno,
                               f"{name}: a public method with no lex.ui decorator. Reflex makes every public method "
                               "a handler the browser can call; decorate it with @ui.action, @ui.job, @ui.load or "
                               "@ui.custom, or, if it is a helper, rename it with a leading underscore."))
            continue
        for dotted, keywords in decorators:
            if dotted == "reflex.event":
                out.append(Finding("error", str(path), fn.lineno,
                                   f"{name}: a raw @rx.event on a ui.State. Use @ui.action, @ui.job or "
                                   "@ui.load, or mark it @ui.custom."))
            elif dotted == "ui.custom":
                out.append(Finding("note", str(path), fn.lineno, f"{name}: custom region (@ui.custom)."))
            elif dotted in _UI_HANDLERS:
                requires = next((k.value for k in keywords if k.arg == "requires"), None)
                if requires is None:
                    uses_manager = any(isinstance(n, ast.Attribute) and n.attr in ("objects", "_default_manager")
                                       for n in ast.walk(fn))
                    if uses_manager:
                        out.append(Finding("warning", str(path), fn.lineno,
                                           f"{name}: names a model manager without requires=. The query "
                                           "will be refused at run time; use self.api or declare requires=."))
                elif "ui.signed_in" in {_resolve(n, names) for n in ast.walk(requires)}:
                    out.append(Finding("note", str(path), fn.lineno,
                                       f"{name}: requires=ui.signed_in (any signed-in user)."))
    return out


def find(paths: list[str]) -> list[Finding]:
    """The lex.ui findings in the project files under ``paths``."""
    findings: list[Finding] = []
    parsed: list[tuple[Path, dict[str, str], list[tuple[ast.ClassDef, set[str]]]]] = []
    for path in _files(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = _imports(tree)
            classes = [(cls, {_resolve(b, names) for b in cls.bases})
                       for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)]
            parsed.append((path, names, classes))
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the check
            findings.append(_unreadable(path, exc))
    ui_states: set[str] = set()
    changed = True
    while changed:
        changed = False
        for _, _, classes in parsed:
            for cls, bases in classes:
                if cls.name not in ui_states and ("ui.State" in bases
                                                  or any(b.rsplit(".", 1)[-1] in ui_states for b in bases)):
                    ui_states.add(cls.name)
                    changed = True
    own_handlers: dict[str, set[str]] = {}
    bases_of: dict[str, set[str]] = {}
    for _, names, classes in parsed:
        for cls, bases in classes:
            if cls.name in ui_states:
                own_handlers.setdefault(cls.name, set()).update(_handler_names(cls, names))
                bases_of.setdefault(cls.name, set()).update(b.rsplit(".", 1)[-1] for b in bases)

    def handlers_of(name: str) -> set[str]:
        """The handlers of ``name`` and of every ui state it inherits, by class name."""
        seen: set[str] = set()
        out: set[str] = set()
        todo = [name]
        while todo:
            current = todo.pop()
            if current in seen or current not in ui_states:
                continue
            seen.add(current)
            out |= own_handlers.get(current, set())
            todo.extend(bases_of.get(current, ()))
        return out

    for path, names, classes in parsed:
        try:
            for cls, _ in classes:
                if cls.name in ui_states:
                    findings.extend(_check_class(path, cls, names, handlers_of(cls.name)))
        except Exception as exc:  # noqa: BLE001
            findings.append(_unreadable(path, exc))
    return findings


def _unreadable(path: Path, exc: Exception) -> Finding:
    return Finding("error", str(path), getattr(exc, "lineno", None) or 1,
                   f"{path.name}: cannot be analysed ({type(exc).__name__}); lex.ui handlers in it were not checked.")


def report(findings: list[Finding]) -> int:
    """Print the findings; 1 when any is an error, else 0."""
    for finding in sorted(findings, key=lambda f: (f.path, f.line)):
        print(f"[{finding.level:<7}] {finding.path}:{finding.line}  {finding.message}")
    errors = sum(1 for finding in findings if finding.level == "error")
    print(f"lex.ui check: {errors} error(s), {sum(f.level == 'warning' for f in findings)} warning(s), "
          f"{sum(f.level == 'note' for f in findings)} listed for review")
    return 1 if errors else 0
