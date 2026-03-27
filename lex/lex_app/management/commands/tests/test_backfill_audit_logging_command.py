from datetime import datetime
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog


class BackfillAuditLoggingCommandTest(TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def _expected_command_timestamp(raw_timestamp: datetime):
        current_tz = timezone.get_current_timezone()
        if settings.USE_TZ:
            if timezone.is_naive(raw_timestamp):
                return timezone.make_aware(raw_timestamp, current_tz)
            return raw_timestamp
        if timezone.is_aware(raw_timestamp):
            return timezone.make_naive(raw_timestamp, current_tz)
        return raw_timestamp

    def setUp(self):
        AuditLogStatus.objects.all().delete()
        CalculationLog.objects.all().delete()
        AuditLog.objects.all().delete()

        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS "generic_app_calculationlog"')
            cursor.execute('DROP TABLE IF EXISTS "generic_app_userchangelog"')
            cursor.execute(
                """
                CREATE TABLE "generic_app_calculationlog" (
                    "timestamp" timestamp NOT NULL,
                    "trigger_name" text NULL,
                    "message_type" text NOT NULL,
                    "calculationId" text NOT NULL,
                    "message" text NOT NULL,
                    "method" text NOT NULL,
                    "is_notification" boolean NOT NULL,
                    "calculation_record" text NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE "generic_app_userchangelog" (
                    "user_name" text NOT NULL,
                    "timestamp" timestamp NOT NULL,
                    "message" text NOT NULL,
                    "traceback" text NULL,
                    "calculationId" text NOT NULL,
                    "calculation_record" text NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO "generic_app_calculationlog"
                ("timestamp", "trigger_name", "message_type", "calculationId",
                 "message", "method", "is_notification", "calculation_record")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    "2026-02-20 12:00:00",
                    "",
                    "INFO",
                    "calc-1",
                    "legacy calc message",
                    "run",
                    True,
                    "auditlog_999",
                ],
            )
            cursor.execute(
                """
                INSERT INTO "generic_app_userchangelog"
                ("user_name", "timestamp", "message", "traceback",
                 "calculationId", "calculation_record")
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    "",
                    "2026-02-20 13:00:00",
                    "legacy user change",
                    "",
                    "calc-2",
                    "legacy_user_change",
                ],
            )

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS "generic_app_calculationlog"')
            cursor.execute('DROP TABLE IF EXISTS "generic_app_userchangelog"')

        AuditLogStatus.objects.all().delete()
        CalculationLog.objects.all().delete()
        AuditLog.objects.all().delete()

    def test_backfill_uses_sql_path_and_preserves_payload_shape(self):
        stdout = StringIO()

        call_command("backfill_audit_logging", chunk_size=1, stdout=stdout)

        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertEqual(AuditLogStatus.objects.count(), 2)
        self.assertEqual(CalculationLog.objects.count(), 1)

        expected_calc_ts = self._expected_command_timestamp(
            datetime(2026, 2, 20, 12, 0, 0)
        )
        calc_audit = AuditLog.objects.get(calculation_id="calc-1")
        self.assertEqual(calc_audit.author, "legacy_migration")
        self.assertEqual(calc_audit.resource, "auditlog")
        self.assertEqual(calc_audit.action, "update")
        self.assertEqual(calc_audit.payload["id"], 999)
        self.assertEqual(calc_audit.payload["message_type"], "INFO")
        self.assertEqual(calc_audit.payload["reason"], "V1 audit log migration snapshot")
        self.assertEqual(calc_audit.date, expected_calc_ts)
        self.assertEqual(calc_audit.created_at, expected_calc_ts)
        self.assertEqual(calc_audit.edited_at, expected_calc_ts)
        self.assertIsNotNone(calc_audit.content_type_id)
        self.assertEqual(calc_audit.object_id, 999)
        calc_log = CalculationLog.objects.get(audit_log=calc_audit)
        self.assertEqual(calc_log.timestamp, expected_calc_ts)
        self.assertEqual(
            calc_log.calculation_log,
            "[LEGACY:INFO] legacy calc message",
        )
        calc_status = AuditLogStatus.objects.get(audit_log=calc_audit)
        self.assertEqual(calc_status.created_at, expected_calc_ts)
        self.assertEqual(calc_status.updated_at, expected_calc_ts)
        self.assertEqual(calc_status.edited_at, expected_calc_ts)

        expected_user_ts = self._expected_command_timestamp(
            datetime(2026, 2, 20, 13, 0, 0)
        )
        user_audit = AuditLog.objects.get(calculation_id="calc-2")
        self.assertEqual(user_audit.author, "legacy_user")
        self.assertEqual(user_audit.resource, "legacy_user_change")
        self.assertEqual(user_audit.date, expected_user_ts)
        self.assertEqual(user_audit.created_at, expected_user_ts)
        self.assertEqual(user_audit.edited_at, expected_user_ts)
        self.assertNotIn("id", user_audit.payload)
        user_status = AuditLogStatus.objects.get(audit_log=user_audit)
        self.assertEqual(user_status.created_at, expected_user_ts)
        self.assertEqual(user_status.updated_at, expected_user_ts)
        self.assertEqual(user_status.edited_at, expected_user_ts)
        self.assertEqual(
            list(AuditLogStatus.objects.values_list("status", flat=True).distinct()),
            ["success"],
        )

        output = stdout.getvalue()
        self.assertIn("AUDIT_BACKFILL_SUMMARY_START", output)
        self.assertIn("AUDIT_BACKFILL_SUMMARY_END", output)
