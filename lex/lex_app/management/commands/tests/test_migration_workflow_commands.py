import json
import tempfile
from pathlib import Path
import sys
import types
from unittest import TestCase
from unittest.mock import patch

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
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from lex.process_admin.utils.model_registration import ModelRegistration
from lex.core.models.LexModel import LexModel
from django.db import models


class BackfillCommandModel(LexModel):
    name = models.CharField(max_length=100)
    amount = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"


class BackfillParentModel(LexModel):
    parent_name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class BackfillChildModel(BackfillParentModel):
    child_amount = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"


class BackfillDbColumnModel(LexModel):
    name = models.CharField(max_length=100)
    percent_of_forfeited_award_percentages_allocated_to_reserve_account = (
        models.FloatField(default=0.5, db_column="percent_of_forf_awards_to_IR")
    )

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
    @staticmethod
    def _expected_command_timestamp(raw_timestamp: str):
        parsed = parse_datetime(raw_timestamp)
        current_tz = timezone.get_current_timezone()
        if settings.USE_TZ:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, current_tz)
            return parsed
        if timezone.is_aware(parsed):
            return timezone.make_naive(parsed, current_tz)
        return parsed

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
        expected_ts = self._expected_command_timestamp(ts)
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
            self.assertEqual(row.valid_from, expected_ts)
            self.assertIsNone(row.valid_to)
        for row in m_rows:
            self.assertEqual(row.sys_from, expected_ts)
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

    def test_backfill_tolerates_missing_live_audit_columns(self):
        BackfillCommandModel.objects.bulk_create(
            [BackfillCommandModel(name="no_audit_cols", amount=7)],
            skip_history=True,
        )

        with connection.schema_editor() as schema_editor:
            for field_name in ["created_by", "edited_by"]:
                schema_editor.remove_field(
                    BackfillCommandModel,
                    BackfillCommandModel._meta.get_field(field_name),
                )

        ts = "2026-02-18T09:15:00+00:00"
        expected_ts = self._expected_command_timestamp(ts)
        call_command(
            "backfill_bitemporal_history",
            timestamp=ts,
            reason="V1 migration snapshot",
            chunk_size=10,
        )

        history_row = self.HistoryModel.objects.get()
        meta_row = self.MetaModel.objects.get()

        self.assertEqual(history_row.history_type, "+")
        self.assertEqual(history_row.valid_from, expected_ts)
        self.assertIsNone(history_row.created_by)
        self.assertIsNone(history_row.edited_by)
        self.assertEqual(meta_row.history_object_id, history_row.history_id)

    def test_backfill_tolerates_columns_missing_from_source_and_history_tables(self):
        BackfillCommandModel.objects.bulk_create(
            [BackfillCommandModel(name="missing_both", amount=9)],
            skip_history=True,
        )

        with connection.schema_editor() as schema_editor:
            schema_editor.remove_field(
                BackfillCommandModel,
                BackfillCommandModel._meta.get_field("name"),
            )
            schema_editor.remove_field(
                self.HistoryModel,
                self.HistoryModel._meta.get_field("name"),
            )
            schema_editor.remove_field(
                self.MetaModel,
                self.MetaModel._meta.get_field("name"),
            )

        call_command(
            "backfill_bitemporal_history",
            timestamp="2026-02-18T09:15:00+00:00",
            reason="V1 migration snapshot",
            chunk_size=10,
        )

        self.assertEqual(self.HistoryModel.objects.count(), 1)
        self.assertEqual(self.MetaModel.objects.count(), 1)

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


class BackfillBitemporalHistoryMultiTableCommandTest(TransactionTestCase):
    def setUp(self):
        from simple_history.models import registered_models

        if BackfillChildModel in registered_models:
            del registered_models[BackfillChildModel]

        ModelRegistration._register_standard_model(BackfillChildModel, [])
        self.HistoryModel = BackfillChildModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [
                BackfillParentModel,
                BackfillChildModel,
                self.HistoryModel,
                self.MetaModel,
            ]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
            for model in [
                BackfillParentModel,
                BackfillChildModel,
                self.HistoryModel,
                self.MetaModel,
            ]:
                schema_editor.create_model(model)

    def tearDown(self):
        from simple_history.models import registered_models

        if BackfillChildModel in registered_models:
            del registered_models[BackfillChildModel]

        with connection.schema_editor() as schema_editor:
            for model in [
                self.MetaModel,
                self.HistoryModel,
                BackfillChildModel,
                BackfillParentModel,
            ]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    def test_backfill_uses_orm_for_multi_table_inheritance_models(self):
        obj = BackfillChildModel.objects.create(parent_name="parent", child_amount=4)
        self.HistoryModel.objects.all().delete()
        self.MetaModel.objects.all().delete()

        with patch(
            "lex_app.management.commands.backfill_bitemporal_history._iter_tracked_models",
            return_value=[(BackfillChildModel, self.HistoryModel, self.MetaModel)],
        ):
            call_command(
                "backfill_bitemporal_history",
                timestamp="2026-02-18T09:15:00+00:00",
                reason="V1 migration snapshot",
                chunk_size=10,
            )

        history_rows = list(self.HistoryModel.objects.filter(id=obj.pk))
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(history_rows[0].parent_name, "parent")
        self.assertEqual(history_rows[0].child_amount, 4)
        self.assertEqual(self.MetaModel.objects.count(), 1)


class BackfillBitemporalHistoryDbColumnCommandTest(TransactionTestCase):
    def setUp(self):
        from simple_history.models import registered_models

        if BackfillDbColumnModel in registered_models:
            del registered_models[BackfillDbColumnModel]

        ModelRegistration._register_standard_model(BackfillDbColumnModel, [])
        self.HistoryModel = BackfillDbColumnModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [BackfillDbColumnModel, self.HistoryModel, self.MetaModel]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
                schema_editor.create_model(model)

    def tearDown(self):
        from simple_history.models import registered_models

        if BackfillDbColumnModel in registered_models:
            del registered_models[BackfillDbColumnModel]

        with connection.schema_editor() as schema_editor:
            for model in [self.MetaModel, self.HistoryModel, BackfillDbColumnModel]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    def test_backfill_omits_missing_db_column_field_without_readding_default(self):
        BackfillDbColumnModel.objects.bulk_create(
            [BackfillDbColumnModel(name="db_column_case")],
            skip_history=True,
        )

        field_name = "percent_of_forfeited_award_percentages_allocated_to_reserve_account"
        with connection.schema_editor() as schema_editor:
            schema_editor.remove_field(
                BackfillDbColumnModel,
                BackfillDbColumnModel._meta.get_field(field_name),
            )
            schema_editor.remove_field(
                self.HistoryModel,
                self.HistoryModel._meta.get_field(field_name),
            )
            schema_editor.remove_field(
                self.MetaModel,
                self.MetaModel._meta.get_field(field_name),
            )

        with patch(
            "lex_app.management.commands.backfill_bitemporal_history._iter_tracked_models",
            return_value=[(BackfillDbColumnModel, self.HistoryModel, self.MetaModel)],
        ):
            call_command(
                "backfill_bitemporal_history",
                timestamp="2026-02-18T09:15:00+00:00",
                reason="V1 migration snapshot",
                chunk_size=10,
            )

        self.assertEqual(self.HistoryModel.objects.count(), 1)
        self.assertEqual(self.MetaModel.objects.count(), 1)
        self.assertEqual(
            self.HistoryModel.objects.values_list("name", flat=True).get(),
            "db_column_case",
        )
