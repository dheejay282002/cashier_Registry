from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0011_supplier_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='raw_import',
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]