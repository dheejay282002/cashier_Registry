import hashlib
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q
from cashier.models import Profile

User = get_user_model()

class EmailOrUsernameModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)

        # Try username lookup first
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            # Fallback to email hash lookup
            email_hash = hashlib.sha256(username.lower().encode()).hexdigest()
            try:
                profile = Profile.objects.get(email_hash=email_hash)
                user = profile.user
            except Profile.DoesNotExist:
                User().set_password(password)
                return None
        except User.MultipleObjectsReturned:
            user = User.objects.filter(username__iexact=username).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    @staticmethod
    def _find_user_by_email(email):
        """Find a user by email address."""
        from admin_panel.fields import decrypt_value

        # 1. Fastest: hashed lookup via Profile
        email_hash = hashlib.sha256(email.lower().encode()).hexdigest()
        try:
            profile = Profile.objects.get(email_hash=email_hash)
            return profile.user
        except Profile.DoesNotExist:
            pass

        # 2. Decrypt-and-compare all Profile.email_encrypted values
        for profile in Profile.objects.exclude(email_encrypted='').select_related('user'):
            try:
                decrypted = decrypt_value(profile.email_encrypted)
                if decrypted and decrypted.lower() == email.lower():
                    # Backfill hash for next time
                    h = hashlib.sha256(decrypted.lower().encode()).hexdigest()
                    if profile.email_hash != h:
                        profile.email_hash = h
                    if profile.email_encrypted != profile.user.email:
                        profile.email_encrypted = profile.user.email
                    profile.save(update_fields=['email_hash', 'email_encrypted'])
                    return profile.user
            except Exception:
                continue

        # 3. Direct User.email lookup (handles encrypted or plaintext)
        for user in User.objects.all():
            try:
                raw = user.email
                if not raw:
                    continue
                decrypted = decrypt_value(raw) if raw.startswith('gAAAAA') else raw
                if decrypted and decrypted.lower() == email.lower():
                    # Sync Profile
                    profile, _ = Profile.objects.get_or_create(user=user, defaults={'role': 'cashier'})
                    h = hashlib.sha256(decrypted.lower().encode()).hexdigest()
                    update = []
                    if profile.email_hash != h:
                        profile.email_hash = h
                        update.append('email_hash')
                    if profile.email_encrypted != decrypted:
                        profile.email_encrypted = decrypted
                        update.append('email_encrypted')
                    if update:
                        profile.save(update_fields=update)
                    return user
            except Exception:
                continue

        return None