import hashlib
from django.db import migrations, models


def populate_email_hash(apps, schema_editor):
    Profile = apps.get_model('cashier', 'Profile')
    User = apps.get_model('auth', 'User')
    for profile in Profile.objects.select_related('user').iterator():
        email = profile.user.email
        if email:
            profile.email_hash = hashlib.sha256(email.lower().encode()).hexdigest()
            profile.save(update_fields=['email_hash'])


class Migration(migrations.Migration):

    dependencies = [
        ('cashier', '0019_encrypt_sensitive_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='email_hash',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.RunPython(populate_email_hash, reverse_code=migrations.RunPython.noop),
    ]
