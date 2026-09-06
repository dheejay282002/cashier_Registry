from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('admin_panel', '0006_systemsetting_entity_name_and_last_report_number'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManagementOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[('account_title', 'Account Title'), ('remark', 'Remark')], db_index=True, max_length=40)),
                ('value', models.CharField(max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['category', 'value'],
                'unique_together': {('category', 'value')},
            },
        ),
    ]
