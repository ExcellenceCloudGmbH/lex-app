import json
import tempfile
from pathlib import Path
import sys
import types
from unittest import TestCase

# Minimal celery stub for test environments where celery is not installed.
if "celery" not in sys.modules:
    celery_stub = types.ModuleType("celery")

    class _DummyCelery:
        def __init__(self, *args, **kwargs):
            self.conf = types.SimpleNamespace(
                broker_url="redis://localhost:6379/0",
                result_backend="redis://localhost:6379/0",
            )

        def config_from_object(self, *args, **kwargs):
            return None

        def autodiscover_tasks(self, *args, **kwargs):
            return None

    celery_stub.Celery = _DummyCelery
    celery_stub.shared_task = lambda *args, **kwargs: (lambda fn: fn)
    sys.modules["celery"] = celery_stub

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from lex.process_admin.utils.model_registration import ModelRegistration
from lex.core.models.LexModel import LexModel
from django.db import models


class BackfillCommandModel(LexModel):
    name = models.CharField(max_length=100)
    amount = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"


class CaptureAndManifestCommandTest(TestCase):
    def test_capture_db_tables_outputs_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "tables.json"
            call_command("capture_db_tables", output=str(output))

            self.assertTrue(output.exists())
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("tables", data)
            self.assertIn("captured_at", data)
            self.assertIn("table_count", data)
            self.assertIsInstance(data["tables"], list)

    def test_generate_manifest_skips_no_pk_tables(self):
        table_with_pk = "zz_legacy_with_pk"
        table_without_pk = "zz_legacy_without_pk"

        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{table_with_pk}"')
            cursor.execute(f'DROP TABLE IF EXISTS "{table_without_pk}"')
            cursor.execute(
                f'CREATE TABLE "{table_with_pk}" (id INTEGER PRIMARY KEY, payload TEXT)'
            )
            cursor.execute(
                f'CREATE TABLE "{table_without_pk}" (payload TEXT)'
            )

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                before_file = Path(tmp_dir) / "before.json"
                manifest_file = Path(tmp_dir) / "manifest.json"
                before_file.write_text(
                    json.dumps(
                        {
                            "tables": [table_with_pk, table_without_pk],
                            "captured_at": "2026-02-18T00:00:00Z",
                        }
                    ),
                    encoding="utf-8",
                )

                call_command(
                    "generate_legacy_freeze_manifest",
                    before=str(before_file),
                    output=str(manifest_file),
                )

                payload = json.loads(manifest_file.read_text(encoding="utf-8"))
                self.assertIn(table_with_pk, payload["freeze_tables"])
                self.assertNotIn(table_without_pk, payload["freeze_tables"])
                self.assertIn(table_without_pk, payload["skipped_no_pk_tables"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{table_with_pk}"')
                cursor.execute(f'DROP TABLE IF EXISTS "{table_without_pk}"')


class BackfillBitemporalHistoryCommandTest(TransactionTestCase):
    def setUp(self):
        from simple_history.models import registered_models

        if BackfillCommandModel in registered_models:
            del registered_models[BackfillCommandModel]

        ModelRegistration._register_standard_model(BackfillCommandModel, [])
        self.HistoryModel = BackfillCommandModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [BackfillCommandModel, self.HistoryModel, self.MetaModel]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
                schema_editor.create_model(model)

    def tearDown(self):
        from simple_history.models import registered_models

        if BackfillCommandModel in registered_models:
            del registered_models[BackfillCommandModel]

        with connection.schema_editor() as schema_editor:
            for model in [self.MetaModel, self.HistoryModel, BackfillCommandModel]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    def test_backfill_creates_history_meta_with_single_timestamp(self):
        BackfillCommandModel.objects.bulk_create(
            [
                BackfillCommandModel(name="a", amount=1),
                BackfillCommandModel(name="b", amount=2),
            ],
            skip_history=True,
        )
        self.assertEqual(self.HistoryModel.objects.count(), 0)
        self.assertEqual(self.MetaModel.objects.count(), 0)

        ts = "2026-02-18T09:15:00+00:00"
        call_command(
            "backfill_bitemporal_history",
            timestamp=ts,
            reason="V1 migration snapshot",
            chunk_size=10,
        )

        h_rows = list(self.HistoryModel.objects.order_by("id"))
        m_rows = list(self.MetaModel.objects.order_by("history_object_id"))
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(len(m_rows), 2)
        for row in h_rows:
            self.assertEqual(row.history_type, "+")
            self.assertEqual(row.valid_from.isoformat(), ts)
            self.assertIsNone(row.valid_to)
        for row in m_rows:
            self.assertEqual(row.sys_from.isoformat(), ts)
            self.assertIsNone(row.sys_to)

        # Idempotent skip on rerun (history/meta already populated).
        call_command(
            "backfill_bitemporal_history",
            timestamp=ts,
            reason="V1 migration snapshot",
            chunk_size=10,
        )
        self.assertEqual(self.HistoryModel.objects.count(), 2)
        self.assertEqual(self.MetaModel.objects.count(), 2)

    def test_backfill_dry_run_does_not_write(self):
        BackfillCommandModel.objects.bulk_create(
            [BackfillCommandModel(name="dry", amount=1)],
            skip_history=True,
        )
        call_command("backfill_bitemporal_history", dry_run=True)
        self.assertEqual(self.HistoryModel.objects.count(), 0)
        self.assertEqual(self.MetaModel.objects.count(), 0)

    def test_backfill_repairs_missing_create_marker_and_fills_missing_rows(self):
        BackfillCommandModel.objects.bulk_create(
            [
                BackfillCommandModel(name="has_history", amount=1),
                BackfillCommandModel(name="missing_history", amount=2),
            ],
            skip_history=True,
        )
        first = BackfillCommandModel.objects.get(name="has_history")
        second = BackfillCommandModel.objects.get(name="missing_history")
        ts = timezone.now()

        # Simulate a partially migrated state: one object has only "~" history.
        self.HistoryModel.objects.create(
            id=first.id,
            name=first.name,
            amount=first.amount,
            valid_from=ts,
            valid_to=None,
            history_type="~",
            history_change_reason="legacy import",
            history_user_id=None,
        )

        call_command(
            "backfill_bitemporal_history",
            timestamp=ts.isoformat(),
            reason="V1 migration snapshot",
            chunk_size=10,
        )

        first_rows = list(
            self.HistoryModel.objects.filter(id=first.id).order_by(
                "valid_from", "history_id"
            )
        )
        self.assertTrue(first_rows)
        self.assertEqual(first_rows[0].history_type, "+")

        second_rows = list(self.HistoryModel.objects.filter(id=second.id))
        self.assertEqual(len(second_rows), 1)
        self.assertEqual(second_rows[0].history_type, "+")

    def test_backfill_with_future_timestamp_keeps_live_rows(self):
        BackfillCommandModel.objects.bulk_create(
            [BackfillCommandModel(name="future_case", amount=1)],
            skip_history=True,
        )
        obj = BackfillCommandModel.objects.get(name="future_case")
        future_ts = (timezone.now() + timezone.timedelta(days=1)).isoformat()

        call_command(
            "backfill_bitemporal_history",
            timestamp=future_ts,
            reason="V1 migration snapshot",
            chunk_size=10,
        )

        self.assertTrue(BackfillCommandModel.objects.filter(pk=obj.pk).exists())
