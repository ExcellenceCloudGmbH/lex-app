"""``lex reflex add`` -- page templates bound to a project's model, and ``lex reflex check``.

``lex reflex add <block> --model <Model>`` writes one dashboard module into the
project's ``_dashboards/`` package and imports it from ``_reflex_structure.py``,
so the page is part of the app the next time it compiles:

========================  ====================================================
``list_report``           the model's records in a server-paged table
``object_page``           one record's dashboard -- its title and lex-app's own
                          record page -- for the model's ``reflex_main``
``overview``              KPI tiles over the model, with its table
========================  ====================================================

Every template uses lex-app's surface (``lex_page``, ``lex_table``,
``LexRecordState``, ``permission``), so what it writes is signed-in, in
lex-app's chrome, and reads its data through lex-app's API as the user. The
folder starts with ``_`` so Django's model discovery never imports a dashboard
-- and with it Reflex -- into the Django process.

``lex reflex check`` runs RFDS's ``lint --strict`` over the project's
dashboards and ``doctor`` over the project, as CI does.

Nothing here imports Reflex or Django: ``lex reflex`` runs without either.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

#: Where ``lex reflex add`` writes: a package Django's discovery skips (``_``).
DASHBOARDS_DIR = "_dashboards"

BLOCKS = ("list_report", "object_page", "overview")

_HEADER = '''"""${title} -- written by `lex reflex add ${block} --model ${model}`; yours to change.

Imported by _reflex_structure.py, which registers its page when the app compiles.
"""

'''

_LIST_REPORT = _HEADER + '''import rfds
from django.apps import apps

from lex.lex_app.reflex import lex_page, lex_table
from lex.lex_app.settings import repo_name

ROUTE = "${route}"


def _model():
    return apps.get_model(repo_name, "${model}")


@lex_page(ROUTE, title="${title}", nav="${nav}", requires=f"{repo_name}.${model}:read")
def ${func}():
    """Every ${model} the user may see, paged, sorted and filtered on the server."""
    return rfds.page(
        rfds.page_header(title="${title}", path=ROUTE),
        lex_table(_model()),
    )
'''

_OBJECT_PAGE = _HEADER + '''import reflex as rx
import rfds
from django.apps import apps

from lex.lex_app.reflex import LexRecordState, lex_view
from lex.lex_app.settings import repo_name


def _model():
    return apps.get_model(repo_name, "${model}")


class ${state}(LexRecordState):
    """The ${model} the URL names, read through lex-app's API as the user."""

    @rx.event
    async def load(self):
        await self.record()


def ${func}() -> rx.Component:
    """The record dashboard. Return it from the model's hook::

        @classmethod
        def reflex_main(cls):
            from ._dashboards.${module} import ${func}
            return ${func}()
    """
    return rfds.page(
        rx.match(
            ${state}.record_state,
            ("ok", rx.fragment(
                rfds.page_header(title=${state}.record_title, path="/${model_lower}"),
                # lex-app's own record page: its fields, actions and history, as the user may see them.
                lex_view(f"${model_lower}/{${state}.pk}/show", height=600),
            )),
            ("denied", rfds.inline_message("You may not see this record.", tone="warning")),
            ("missing", rfds.inline_message("This record does not exist.", tone="neutral")),
            rfds.spinner(label="Loading"),
        ),
        on_mount=${state}.load,
    )
'''

_OVERVIEW = _HEADER + '''import reflex as rx
import rfds
from django.apps import apps

from lex.lex_app.reflex import has_permission, lex_page, lex_table, run_orm
from lex.lex_app.settings import repo_name

ROUTE = "${route}"


def _model():
    return apps.get_model(repo_name, "${model}")


def _count() -> int:
    return _model()._default_manager.count()


class ${state}(rx.State):
    """The overview's figures, computed in the database."""

    total: int = 0

    @rx.event(auth=has_permission(f"{repo_name}.${model}", "read"))
    async def load(self):
        self.total = await run_orm(_count)


@lex_page(ROUTE, title="${title}", nav="${nav}", requires=f"{repo_name}.${model}:read",
          on_load=${state}.load)
def ${func}():
    """KPIs over ${model}, then its records."""
    return rfds.page(
        rfds.page_header(title="${title}", path=ROUTE),
        rfds.grid(
            rfds.kpi_tile(label="${model} records", value=${state}.total, kind="count"),
            columns=3,
        ),
        lex_table(_model()),
    )
'''

_TEMPLATES = {"list_report": _LIST_REPORT, "object_page": _OBJECT_PAGE, "overview": _OVERVIEW}

_IMPORT_LINE = "from .{package} import {module}  # noqa: F401  (registers its page)\n"


def snake(name: str) -> str:
    """``CashPosition`` -> ``cash_position``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def render(block: str, model: str, *, route: str | None = None, title: str | None = None,
           nav: str = "Dashboards") -> tuple[str, str]:
    """``(module name, source)`` of ``block`` for ``model``."""
    if block not in _TEMPLATES:
        raise ValueError(f"unknown block {block!r}; blocks: {', '.join(BLOCKS)}")
    if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", model or ""):
        raise ValueError(f"--model is a model class name such as Fund, got {model!r}")
    base = snake(model)
    module = f"{base}_{block}"
    words = {"list_report": "list", "object_page": "record", "overview": "overview"}[block]
    title = title or f"{model} {words}"
    route = route or f"/{base.replace('_', '-')}" + ("" if block == "list_report" else f"-{words}")
    if not route.startswith("/"):
        raise ValueError(f"--route starts with '/', got {route!r}")
    values = {
        "block": block, "model": model, "model_lower": model.lower(), "title": title.replace('"', "'"),
        "route": route, "nav": nav.replace('"', "'"), "func": f"{base}_{words}_page",
        "state": f"{model}{words.capitalize()}State", "module": module,
    }
    return module, string.Template(_TEMPLATES[block]).substitute(values)


def add_block(project_root: Path, block: str, model: str, *, route: str | None = None, title: str | None = None,
              nav: str = "Dashboards", force: bool = False, register: bool = True) -> list[Path]:
    """Write ``block`` for ``model`` into the project; returns the files written or changed."""
    module, source = render(block, model, route=route, title=title, nav=nav)
    package = project_root / DASHBOARDS_DIR
    package.mkdir(exist_ok=True)
    touched = []
    init = package / "__init__.py"
    if not init.exists():
        init.write_text('"""The project\'s Reflex dashboards (lex reflex add)."""\n', encoding="utf-8")
        touched.append(init)
    target = package / f"{module}.py"
    if target.exists() and not force:
        raise FileExistsError(f"{target} exists; pass --force to replace it")
    target.write_text(source, encoding="utf-8")
    touched.append(target)
    if register and block != "object_page":
        structure = project_root / "_reflex_structure.py"
        line = _IMPORT_LINE.format(package=DASHBOARDS_DIR, module=module)
        existing = structure.read_text(encoding="utf-8") if structure.exists() else ""
        if line.strip() not in existing:
            if not existing:
                existing = ('"""The project\'s Reflex dashboards: main() is the home page."""\n\n'
                            "import rfds\n\n\n"
                            "def main():\n"
                            '    return rfds.page(rfds.page_header(title="Overview", path="/"))\n')
            structure.write_text(existing.rstrip("\n") + "\n\n" + line, encoding="utf-8")
            touched.append(structure)
    return touched


def check_paths(project_root: Path) -> list[str]:
    """What ``lex reflex check`` lints: the structure module and the dashboards package."""
    paths = []
    for name in ("_reflex_structure.py", DASHBOARDS_DIR):
        if (project_root / name).exists():
            paths.append(str(project_root / name))
    return paths


def run_check(project_root: Path) -> int:
    """RFDS's ``lint --strict`` over the dashboards, then ``doctor`` over the project; 0 = clean."""
    from rfds.cli import main as rfds_main

    paths = check_paths(project_root)
    status = 0
    if paths:
        status = rfds_main(["lint", "--strict", *paths]) or status
    return rfds_main(["doctor", "--app-root", str(project_root)]) or status
