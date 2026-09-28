"""A package that imports its API lazily still exports it.

`lex.lex_app.reflex` serves `run_orm`, `current_record` and the rest through a
module `__getattr__` (PEP 562): importing them eagerly would create Reflex
states while `rxconfig.py` loads, and in every Django process that imports the
`Reflex` report living beside them. The docs import them from the package, so
the gate has to count a module's `__all__` as its exports when a `__getattr__`
serves them -- and only then, or a typo in `__all__` would pass for an API.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location("check_doc_imports", SCRIPTS / "check_doc_imports.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_doc_imports"] = module
    spec.loader.exec_module(module)
    return module


def test_a_lazy_package_exports_what_its_all_lists(tmp_path):
    module = tmp_path / "lazy.py"
    module.write_text(
        "__all__ = ['run_orm', 'current_record']\n"
        "def __getattr__(name):\n"
        "    raise AttributeError(name)\n"
    )
    names, star = _checker().defined_names(module)
    assert {"run_orm", "current_record"} <= names
    assert not star


def test_all_without_getattr_exports_nothing_it_does_not_bind(tmp_path):
    """An eager module's `__all__` promises names; only bindings deliver them."""
    module = tmp_path / "eager.py"
    module.write_text("__all__ = ['promised']\n")
    names, _ = _checker().defined_names(module)
    assert "promised" not in names


def test_a_computed_all_is_not_read(tmp_path):
    """Only a literal list can be read without importing the module."""
    module = tmp_path / "computed.py"
    module.write_text(
        "_EXPORTS = {'hidden': 'somewhere'}\n"
        "__all__ = sorted(_EXPORTS)\n"
        "def __getattr__(name):\n"
        "    raise AttributeError(name)\n"
    )
    names, _ = _checker().defined_names(module)
    assert "hidden" not in names


def test_the_reflex_package_resolves_as_the_docs_import_it():
    checker = _checker()
    assert checker.check_statement(
        "from lex.lex_app.reflex import current_record, run_orm, has_permission"
    ) is None
    assert checker.check_statement("from lex.lex_app.reflex import no_such_name") is not None
