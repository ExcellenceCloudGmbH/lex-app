from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_logging", "0004_remove_auditlog_audit_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="calculationlog",
            name="calculationId",
            field=models.TextField(default="test_id", db_index=True),
        ),
    ]
