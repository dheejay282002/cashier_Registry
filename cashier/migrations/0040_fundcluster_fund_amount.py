from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0039_profile_specific_role_alter_report_report_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='fundcluster',
            name='fund_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=20, null=True, default=None),
        ),
    ]
