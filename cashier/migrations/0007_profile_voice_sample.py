from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0006_profile_department_profile_employee_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='voice_sample',
            field=models.FileField(blank=True, null=True, upload_to='biometrics/voice/'),
        ),
    ]
