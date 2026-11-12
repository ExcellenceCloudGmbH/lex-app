from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_logging", "0007_calculationlog_heading"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("create", "Create"),
                    ("update", "Update"),
                    ("delete", "Delete"),
                    ("export", "Export"),
                    ("download", "Download"),
                ],
                max_length=10,
            ),
        ),
    ]
