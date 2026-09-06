import admin_panel.fields
from django.db import migrations


def encrypt_existing_account_numbers(apps, schema_editor):
    FundCluster = apps.get_model('cashier', 'FundCluster')
    Supplier = apps.get_model('cashier', 'Supplier')
    Cheque = apps.get_model('cashier', 'Cheque')

    for model in [FundCluster, Supplier, Cheque]:
        for obj in model.objects.iterator():
            if obj.account_number:
                obj.save(update_fields=['account_number'])


def sync_existing_emails(apps, schema_editor):
    Profile = apps.get_model('cashier', 'Profile')
    for profile in Profile.objects.select_related('user').iterator():
        user_email = profile.user.email
        if user_email and profile.email_encrypted != user_email:
            profile.email_encrypted = user_email
            profile.save(update_fields=['email_encrypted'])


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0018_profile_email_otp_enabled_profile_totp_enabled_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='email_encrypted',
            field=admin_panel.fields.EncryptedEmailField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='cheque',
            name='account_number',
            field=admin_panel.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='fundcluster',
            name='account_number',
            field=admin_panel.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='account_number',
            field=admin_panel.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(
            encrypt_existing_account_numbers,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            sync_existing_emails,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
