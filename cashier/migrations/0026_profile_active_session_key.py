from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0025_radai_ors_burs_no_radai_reference_code_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='active_session_key',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
