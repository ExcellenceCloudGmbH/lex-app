from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_logging", "0006_alter_auditlog_calculation_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="calculationlog",
            name="heading",
            field=models.TextField(blank=True, null=True),
        ),
    ]
