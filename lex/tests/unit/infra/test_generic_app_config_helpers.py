"""
Unit tests for helper functions in ``lex.utilities.config.generic_app_config``.

**What this tests (customer-visible behaviour)**

``_is_structure_yaml_file``, ``_is_structure_file``,
``GenericAppConfig._dir_filter``, and ``GenericAppConfig._is_valid_module``
control which files are discovered during model auto-loading.

**Why it matters**

If ``_is_valid_module`` lets a test file through, test models get
registered in Django's app registry and appear as real tables.
If ``_dir_filter`` skips a legitimate directory, user-defined models
are silently ignored.

**Methodology**

Pure string-matching functions — no DB.

Run::

    python manage.py test lex.tests.test_generic_app_config_helpers
"""

from django.test import SimpleTestCase

from lex.utilities.config.generic_app_config import (
    _is_structure_yaml_file,
    _is_structure_file,
    GenericAppConfig,
)


class TestIsStructureYamlFile(SimpleTestCase):
    """Prove ``_is_structure_yaml_file`` only matches the exact name."""

    def test_exact_match(self):
        self.assertTrue(_is_structure_yaml_file("model_structure.yaml"))

    def test_other_yaml(self):
        self.assertFalse(_is_structure_yaml_file("config.yaml"))

    def test_similar_name(self):
        self.assertFalse(_is_structure_yaml_file("model_structure.yml"))

    def test_empty(self):
        self.assertFalse(_is_structure_yaml_file(""))


class TestIsStructureFile(SimpleTestCase):
    """Prove ``_is_structure_file`` matches *_structure.py files."""

    def test_match(self):
        self.assertTrue(_is_structure_file("model_structure.py"))

    def test_another_match(self):
        self.assertTrue(_is_structure_file("widget_structure.py"))

    def test_non_structure(self):
        self.assertFalse(_is_structure_file("models.py"))

    def test_structure_yaml(self):
        self.assertFalse(_is_structure_file("model_structure.yaml"))


class _StubConfig:
    """Minimal stand-in carrying the class-level exclusion constants."""
    _EXCLUDED_FILES = GenericAppConfig._EXCLUDED_FILES
    _EXCLUDED_DIRS = GenericAppConfig._EXCLUDED_DIRS
    _EXCLUDED_PREFIXES = GenericAppConfig._EXCLUDED_PREFIXES
    _EXCLUDED_POSTFIXES = GenericAppConfig._EXCLUDED_POSTFIXES


_stub = _StubConfig()


class TestDirFilter(SimpleTestCase):
    """Prove ``_dir_filter`` excludes venv, hidden, and test dirs."""

    def _filter(self, dirname):
        return GenericAppConfig._dir_filter(_stub, dirname)

    def test_normal_dir(self):
        self.assertTrue(self._filter("models"))

    def test_venv_excluded(self):
        self.assertFalse(self._filter("venv"))

    def test_dot_venv_excluded(self):
        self.assertFalse(self._filter(".venv"))

    def test_build_excluded(self):
        self.assertFalse(self._filter("build"))

    def test_migrations_excluded(self):
        self.assertFalse(self._filter("migrations"))

    def test_hidden_dir_excluded(self):
        self.assertFalse(self._filter(".git"))

    def test_underscore_dir_excluded(self):
        self.assertFalse(self._filter("__pycache__"))

    def test_test_prefix_excluded(self):
        self.assertFalse(self._filter("test_utils"))


class TestIsValidModule(SimpleTestCase):
    """Prove ``_is_valid_module`` filters excluded files and prefixes."""

    def _valid(self, module_name, file):
        return GenericAppConfig._is_valid_module(_stub, module_name, file)

    def test_normal_module(self):
        self.assertTrue(self._valid("Invoice", "Invoice.py"))

    def test_non_python_rejected(self):
        self.assertFalse(self._valid("data", "data.csv"))

    def test_settings_excluded(self):
        self.assertFalse(self._valid("settings", "settings.py"))

    def test_asgi_excluded(self):
        self.assertFalse(self._valid("asgi", "asgi.py"))

    def test_wsgi_excluded(self):
        self.assertFalse(self._valid("wsgi", "wsgi.py"))

    def test_urls_excluded(self):
        self.assertFalse(self._valid("urls", "urls.py"))

    def test_setup_excluded(self):
        self.assertFalse(self._valid("setup", "setup.py"))

    def test_underscore_prefix_excluded(self):
        self.assertFalse(self._valid("_internal", "_internal.py"))

    def test_dot_prefix_excluded(self):
        self.assertFalse(self._valid(".hidden", ".hidden.py"))

    def test_test_prefix_excluded(self):
        self.assertFalse(self._valid("test_models", "test_models.py"))

    def test_trailing_underscore_excluded(self):
        self.assertFalse(self._valid("old_", "old_.py"))

    def test_create_db_excluded(self):
        self.assertFalse(self._valid("create_db", "create_db.py"))

    def test_calculation_ids_excluded(self):
        self.assertFalse(self._valid("CalculationIDs", "CalculationIDs.py"))
