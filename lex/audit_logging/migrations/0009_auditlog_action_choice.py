from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_logging", "0008_auditlog_object_ref"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete"), ("action", "Action")],
                max_length=10,
            ),
        ),
    ]
