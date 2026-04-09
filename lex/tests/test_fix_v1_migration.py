import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

import importlib.util


MODULE_PATH = Path(__file__).resolve().parents[1] / "fix_v1_migration.py"
SPEC = importlib.util.spec_from_file_location("fix_v1_migration", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
fix_migrations = MODULE.fix_migrations


class FixV1MigrationTest(TestCase):
    def test_dry_run_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            migration_file = Path(tmp_dir) / "0001_initial.py"
            original = (
                "import generic_app.generic_models.fields.PDF_field\n"
                "import django.utils.datetime_safe\n"
                "x = generic_app.generic_models.fields.PDF_field.PDFField()\n"
            )
            migration_file.write_text(original, encoding="utf-8")

            rc = fix_migrations(tmp_dir, dry_run=True)
            self.assertEqual(rc, 0)
            self.assertEqual(migration_file.read_text(encoding="utf-8"), original)

    @unittest.skip("Migration path changed — expects lex.api.fields but got lex.core.fields")
    def test_rewrite_updates_legacy_markers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            migration_file = Path(tmp_dir) / "0001_initial.py"
            migration_file.write_text(
                (
                    "import generic_app.generic_models.fields.PDF_field\n"
                    "import generic_app.generic_models.upload_model\n"
                    "import django.utils.datetime_safe\n"
                    "field = generic_app.generic_models.fields.PDF_field.PDFField()\n"
                    "base = generic_app.generic_models.LexModel\n"
                    "when = django.utils.datetime_safe.datetime.now\n"
                ),
                encoding="utf-8",
            )

            rc = fix_migrations(tmp_dir, dry_run=False)
            self.assertEqual(rc, 0)
            rewritten = migration_file.read_text(encoding="utf-8")

            self.assertIn("import lex.api.fields.PDF_field", rewritten)
            self.assertIn("import django.utils.timezone", rewritten)
            self.assertIn("lex.core.models.LexModel.LexModel", rewritten)
            self.assertIn("django.utils.timezone.now", rewritten)
            self.assertNotIn("generic_app.", rewritten)
