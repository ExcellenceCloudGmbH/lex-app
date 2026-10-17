from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_logging", "0007_calculationlog_heading"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="object_ref",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]
