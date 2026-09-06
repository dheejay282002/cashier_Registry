from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0013_fundcluster_bank_name_and_account_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='mr_or',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='supplier',
            name='mr',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='supplier',
            name='code_or',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='supplier',
            name='code_or2',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='supplier',
            name='series_month',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='supplier',
            name='date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='supplier',
            name='or_number',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='supplier',
            name='series_day',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='supplier',
            name='code_line',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='supplier',
            name='line',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='supplier',
            name='code_noc',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='supplier',
            name='nature_of_collections',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='supplier',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=18),
        ),
        migrations.AddField(
            model_name='supplier',
            name='remarks',
            field=models.TextField(blank=True, default=''),
        ),
    ]
