"""Tests for ``lex.core.config.LexProjectConfig`` and the cached helper
``get_configured_default_serializer_name``.

Covers:
    • Loading ``DEFAULT_SERIALIZER_NAME`` / ``default_serializer_name`` from
      both ``lex_config.py`` and the legacy ``_authentication_settings.py``.
    • Backward compatibility (missing attribute -> ``"default"``).
    • The module-level cache is reset by
      :func:`reset_default_serializer_name_cache`.
"""

import os
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lex.core.config import (
    DEFAULT_SERIALIZER_NAME,
    LexProjectConfig,
    get_configured_default_serializer_name,
    reset_default_serializer_name_cache,
)


def _write_lex_config(directory: Path, body: str, filename: str = "lex_config.py") -> None:
    (directory / filename).write_text(textwrap.dedent(body))


class LexProjectConfigDefaultSerializerNameTests(unittest.TestCase):
    def setUp(self):
        reset_default_serializer_name_cache()

    def tearDown(self):
        reset_default_serializer_name_cache()

    def test_default_value_when_attribute_missing(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(Path(tmp), 'INITIAL_DATA = "x"\nPROJECT_GROUPS = []\n')
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                cfg = LexProjectConfig.load()
        self.assertEqual(cfg.default_serializer_name, DEFAULT_SERIALIZER_NAME)

    def test_uppercase_attribute_loaded(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(
                Path(tmp),
                'DEFAULT_SERIALIZER_NAME = "framework_default"\n',
            )
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                cfg = LexProjectConfig.load()
        self.assertEqual(cfg.default_serializer_name, "framework_default")

    def test_lowercase_attribute_loaded(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(
                Path(tmp),
                'default_serializer_name = "lex_default"\n',
            )
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                cfg = LexProjectConfig.load()
        self.assertEqual(cfg.default_serializer_name, "lex_default")

    def test_legacy_authentication_settings_supported(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(
                Path(tmp),
                'DEFAULT_SERIALIZER_NAME = "legacy_default"\n',
                filename="_authentication_settings.py",
            )
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                cfg = LexProjectConfig.load()
        self.assertEqual(cfg.default_serializer_name, "legacy_default")

    def test_blank_string_falls_back_to_default(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(Path(tmp), 'DEFAULT_SERIALIZER_NAME = "   "\n')
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                cfg = LexProjectConfig.load()
        self.assertEqual(cfg.default_serializer_name, DEFAULT_SERIALIZER_NAME)

    def test_cached_helper_uses_loaded_value(self):
        with TemporaryDirectory() as tmp:
            _write_lex_config(
                Path(tmp),
                'DEFAULT_SERIALIZER_NAME = "cached_alias"\n',
            )
            with patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                self.assertEqual(
                    get_configured_default_serializer_name(), "cached_alias"
                )

        # Cache hit: even without PROJECT_ROOT the previously-resolved value
        # is returned until ``reset_default_serializer_name_cache`` is called.
        self.assertEqual(
            get_configured_default_serializer_name(), "cached_alias"
        )
        reset_default_serializer_name_cache()
        # After reset, with no config available the helper falls back safely.
        with patch.object(LexProjectConfig, "load", side_effect=RuntimeError):
            self.assertEqual(
                get_configured_default_serializer_name(),
                DEFAULT_SERIALIZER_NAME,
            )


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    unittest.main()
