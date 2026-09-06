import hashlib
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import pre_save, post_init, post_save
from django.dispatch import receiver
from django.utils import timezone
from admin_panel.fields import EncryptedCharField, EncryptedEmailField, encrypt_value, decrypt_value


class Transaction(models.Model):
    student_name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.student_name} - {self.amount}"


class FundCluster(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    bank_name = models.CharField(max_length=100, blank=True, default='')
    account_number = EncryptedCharField(max_length=255, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} — {self.name}"


class Supplier(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('blacklisted', 'Blacklisted'),
    )
    mr_or = models.CharField(max_length=60, blank=True, default='')
    mr = models.CharField(max_length=120, blank=True, default='')
    code_or = models.CharField(max_length=60, blank=True, default='')
    code_or2 = models.CharField(max_length=60, blank=True, default='')
    series_month = models.CharField(max_length=40, blank=True, default='')
    date = models.DateField(null=True, blank=True)
    or_number = models.CharField(max_length=60, blank=True, default='')
    series_day = models.CharField(max_length=40, blank=True, default='')
    account_number = EncryptedCharField(max_length=255, blank=True, default='')
    account_name = models.CharField(max_length=200)
    code_line = models.CharField(max_length=80, blank=True, default='')
    line = models.CharField(max_length=80, blank=True, default='')
    code_noc = models.CharField(max_length=80, blank=True, default='')
    nature_of_collections = models.CharField(max_length=255, blank=True, default='')
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    remarks = models.TextField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    tin = models.CharField(max_length=30, blank=True, default='', verbose_name='TIN')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='suppliers_created')
    raw_import = models.JSONField(blank=True, null=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.account_name

    class Meta:
        indexes = [
            models.Index(fields=['account_name']),
            models.Index(fields=['status']),
            models.Index(fields=['date']),
            models.Index(fields=['created_by']),
        ]


class Cheque(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('released', 'Released'),
        ('voided', 'Voided'),
        ('stale', 'Stale'),
    )
    cheque_number = models.CharField(max_length=50, blank=True, default='')
    payee = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    payee_name = models.CharField(max_length=200, blank=True, default='')
    fund_cluster = models.ForeignKey(FundCluster, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    date = models.DateField(default=timezone.now)
    purpose = models.TextField(blank=True, default='')
    bank_name = models.CharField(max_length=100, blank=True, default='')
    account_number = EncryptedCharField(max_length=255, blank=True, default='')
    # Report / disbursement fields (for Cheque Summary report)
    dv_payroll_no = models.CharField(max_length=60, blank=True, default='', verbose_name='DV/Payroll No.')
    ors_burs_no = models.CharField(max_length=60, blank=True, default='', verbose_name='ORS/BURS No.')
    responsibility_center = models.CharField(max_length=60, blank=True, default='', verbose_name='Responsibility Center Code')
    uacs_object_code = models.CharField(max_length=60, blank=True, default='', verbose_name='UACS Object Code')
    nature_of_payment = models.CharField(max_length=255, blank=True, default='', verbose_name='Nature of Payment')
    professional_tax = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Professional Tax')
    tax_5_3 = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Tax 5%/3%')
    tax_3_1 = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Tax 3%/1%')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='cheques_created')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cheques_updated')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    printed_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, default='')
    stale_after_days = models.IntegerField(null=True, blank=True)
    stale_at = models.DateField(null=True, blank=True)

    @property
    def stale_is_overdue(self):
        if self.status in ('released', 'voided'):
            return False
        if self.stale_at:
            from datetime import date as date_type
            return self.stale_at < date_type.today()
        return False

    @property
    def stale_days_remaining(self):
        if self.status in ('released', 'voided'):
            return 0
        if self.stale_at:
            from datetime import date as date_type
            delta = self.stale_at - date_type.today()
            return max(0, delta.days)
        return None

    @property
    def real_cheque_number(self):
        if self.payee and self.payee.mr_or:
            return self.payee.mr_or
        return self.cheque_number

    def __str__(self):
        return f"Cheque #{self.real_cheque_number or self.pk} — {self.payee_name} ₱{self.amount}"


    @classmethod
    def normalize_cheque_number(cls, value, width=6, fallback=None):
        raw_value = (value or "").strip()
        if not raw_value:
            if fallback:
                return str(fallback)
            return cls.next_cheque_number(width=width)

        # If purely numeric and shorter than width, keep prefix zero padding
        if raw_value.isdigit() and len(raw_value) < width:
            return raw_value.zfill(width)
        return raw_value

    @classmethod
    def next_cheque_number(cls, width=6):
        import re
        from admin_panel.models import SystemSetting

        settings_obj = SystemSetting.get_settings()
        # Find highest numeric cheque number present in DB
        highest_existing = 0
        for val in cls.objects.exclude(cheque_number='').values_list('cheque_number', flat=True):
            if not val:
                continue
            m = re.search(r"(\d+)$", val.strip())
            if m:
                try:
                    n = int(m.group(1))
                except Exception:
                    continue
                if n > highest_existing:
                    highest_existing = n

        stored = settings_obj.last_cheque_number.strip() if settings_obj.last_cheque_number else ''
        stored_num = int(stored) if stored.isdigit() else 0

        # If stored setting is higher than actual highest existing cheque,
        # revert it back to the real end (highest_existing) to prevent gaps.
        if stored_num > highest_existing:
            settings_obj.last_cheque_number = str(highest_existing).zfill(width)
            settings_obj.save(update_fields=['last_cheque_number'])
            current_num = highest_existing
        else:
            # Otherwise use the max of stored and existing
            current_num = max(stored_num, highest_existing)

        return str(current_num + 1).zfill(width)

    @classmethod
    def record_generated_cheque_number(cls, value, width=6):
        from admin_panel.models import SystemSetting

        normalized = cls.normalize_cheque_number(value, width=width)
        settings_obj = SystemSetting.get_settings()
        current = settings_obj.last_cheque_number.strip() if settings_obj.last_cheque_number else ''
        current_num = int(current) if current.isdigit() else 0
        try:
            new_num = int(normalized)
            if new_num > current_num:
                settings_obj.last_cheque_number = normalized
                settings_obj.save(update_fields=['last_cheque_number'])
        except ValueError:
            pass
        return normalized

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['date']),
            models.Index(fields=['cheque_number']),
            models.Index(fields=['payee']),
            models.Index(fields=['fund_cluster']),
            models.Index(fields=['created_by']),
        ]


ALL_PERMISSIONS = {
    "navigation": {
        "dashboard": "Dashboard",
        "user_management": "User Management",
        "cheques": "Cheques List",
        "cheque_create": "Create Cheque",
        "fund_clusters": "Fund Clusters",
        "suppliers": "Suppliers",
        "radai": "RADAI",
        "registry": "Registry",
        "reports": "Reports",
        "audit_log": "Audit Log",
        "manage_devices": "Manage Devices",
        "import_data": "Import Data",
        "export": "Export Data",
        "system_settings": "System Settings",
        "profile_settings": "Profile Settings",
        "backup_restore": "Backup & Restore",
        "remark_management": "Remark Management",
        "screen_timeout": "Screen Timeout",
        "account_titles_management": "Account Titles",
    },
    "actions": {
        "cheques": {"view": "View", "create": "Create", "edit": "Edit", "print": "Print", "void": "Void", "advance_status": "Advance Status"},
        "suppliers": {"view": "View", "create": "Add", "edit": "Edit", "delete": "Delete", "export": "Export"},
        "radai": {"view": "View", "create": "Add", "edit": "Edit", "delete": "Delete", "export": "Export"},
        "fund_clusters": {"view": "View", "create": "Add", "edit": "Edit", "delete": "Delete"},
        "reports": {"view": "View", "generate": "Generate"},
        "users": {"view": "View", "create": "Create", "edit": "Edit", "delete": "Delete"},
    },
}

DEFAULT_ROLE_PERMISSIONS = {
    "admin": {
        "navigation": list(ALL_PERMISSIONS["navigation"].keys()),
        "permissions": {mod: list(actions.keys()) for mod, actions in ALL_PERMISSIONS["actions"].items()},
    },
    "cashier": {
        "navigation": ["dashboard", "cheques", "cheque_create", "fund_clusters", "suppliers", "radai", "registry", "reports", "profile_settings", "system_settings", "account_titles_management"],
        "permissions": {
            "cheques": ["view", "create", "edit", "print", "void", "advance_status"],
            "suppliers": ["view", "create", "edit", "delete"],
            "radai": ["view", "create", "edit", "delete", "export"],
            "fund_clusters": ["view"],
            "reports": ["view", "generate"]
        },
    },
    # Guest: fixed permissions — can view and generate reports only, NO print
    "guest": {
        "navigation": ["dashboard", "reports", "profile_settings"],
        "permissions": {"reports": ["view", "generate"]},
    },
}


class RoleConfig(models.Model):
    role = models.CharField(max_length=20, unique=True)
    config = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"RoleConfig: {self.role}"

    @classmethod
    def get_for_role(cls, role):
        return DEFAULT_ROLE_PERMISSIONS.get(role, {"navigation": [], "permissions": {}})


class Report(models.Model):
    REPORT_TYPES = (
        ('cheque_summary', 'Cheque Summary'),
        ('radai_summary', 'RADAI Summary'),
        ('weekly_cheque', 'Weekly Cheque Report'),
        ('monthly_cheque', 'Monthly Cheque Report'),
        ('annual_cheque', 'Annual Cheque Report'),
        ('weekly_radai', 'Weekly RADAI Report'),
        ('monthly_radai', 'Monthly RADAI Report'),
        ('annual_radai', 'Annual RADAI Report'),
    )
    PERIOD_CHOICES = (
        ('', 'Custom Range'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annual', 'Annual'),
    )
    title = models.CharField(max_length=200)
    report_type = models.CharField(max_length=40, choices=REPORT_TYPES)
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES, blank=True, default='')
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    data_snapshot = models.JSONField(default=dict)
    notes = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.title} ({self.generated_at.date()})"

    @property
    def generated_by_name(self):
        if self.generated_by:
            return self.generated_by.get_full_name() or self.generated_by.username
        if self.data_snapshot and isinstance(self.data_snapshot, dict):
            snap_name = self.data_snapshot.get('generated_by_name') or self.data_snapshot.get('generated_by_username')
            if snap_name:
                return snap_name
        return "DEE JAY CRISTOBAL"


class Profile(models.Model):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('cashier', 'Cashier'),
        ('guest', 'Guest Viewer'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    employee_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    middle_initial = models.CharField(max_length=5, blank=True, default='')
    department = models.CharField(max_length=120, blank=True, default='')
    position = models.CharField(max_length=120, blank=True, default='')
    profile_picture = models.FileField(upload_to='profile_pictures/', blank=True, null=True)
    rfid_uid = models.CharField(max_length=128, blank=True, null=True, unique=True)
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('pending', 'Pending Activation'),
        ('resting', 'Resting'),
        ('disabled', 'Disabled'),
        ('suspended', 'Suspended'),
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')
    is_disbursing_officer = models.BooleanField(default=False)

    # Encrypted email + hash for lookups
    email_encrypted = EncryptedEmailField(max_length=255, blank=True, default='')
    email_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # MFA / 2FA fields
    totp_secret = models.CharField(max_length=64, blank=True, default='')
    totp_enabled = models.BooleanField(default=False)
    email_otp_enabled = models.BooleanField(default=False)
    mfa_recovery_codes = models.JSONField(default=list, blank=True,
        help_text="List of unused recovery codes for MFA fallback")

    # Force password change on next login
    must_change_password = models.BooleanField(default=False)

    # Per-user lockscreen wallpaper
    lockscreen_wallpaper = models.ImageField(upload_to="lockscreen_wallpapers/", blank=True, null=True)

    # Single-device enforcement: tracks the session key of the active login
    active_session_key = models.CharField(max_length=40, blank=True, default='')
    last_activity = models.DateTimeField(null=True, blank=True)
    login_attempt_blocked = models.BooleanField(default=False)
    blocked_login_time = models.DateTimeField(null=True, blank=True)
    blocked_login_ip = models.CharField(max_length=45, blank=True, default='')
    auto_lock_seconds = models.IntegerField(null=True, blank=True, help_text="Individual idle seconds before lock screen warning")
    auto_logout_seconds = models.IntegerField(null=True, blank=True, help_text="Individual idle seconds before logout")

    # Simple storage for biometric files (templates / embeddings). Integrations can
    # replace these fields with proper secure storage later.
    fingerprint_template = models.FileField(upload_to='biometrics/fingerprint/', blank=True, null=True)
    face_embedding = models.FileField(upload_to='biometrics/face/', blank=True, null=True)
    voice_sample = models.FileField(upload_to='biometrics/voice/', blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.role}"


class UserDevice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='devices')
    session_key = models.CharField(max_length=40, unique=True)
    device_name = models.CharField(max_length=255, blank=True, default='')
    windows_username = models.CharField(max_length=120, blank=True, default='')
    ip_address = models.CharField(max_length=45, blank=True, default='')
    location = models.CharField(max_length=255, blank=True, default='')
    user_agent = models.TextField(blank=True, default='')
    last_activity = models.DateTimeField(null=True, blank=True)
    login_time = models.DateTimeField(auto_now_add=True)
    is_terminated = models.BooleanField(default=False)
    is_blocked = models.BooleanField(default=False)
    screen_capture = models.TextField(blank=True, default='')  # Stored as base64 string for direct inline viewing
    current_url = models.CharField(max_length=255, blank=True, default='')

    def __str__(self):
        return f"{self.user.username} - {self.device_name or 'Unknown Device'} ({self.session_key[:8]})"


class RadaiSetting(models.Model):
    ors_burs_no = models.CharField(max_length=60, blank=True, default='', verbose_name='ORS/BURS No.')
    responsibility_center = models.CharField(max_length=60, blank=True, default='', verbose_name='Responsibility Center Code')

    class Meta:
        verbose_name = 'RADAI Setting'
        verbose_name_plural = 'RADAI Settings'

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Radai(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('blacklisted', 'Blacklisted'),
    )
    mr_or = models.CharField(max_length=60, blank=True, default='')
    mr = models.CharField(max_length=120, blank=True, default='')
    code_or = models.CharField(max_length=60, blank=True, default='')
    code_or2 = models.CharField(max_length=60, blank=True, default='')
    series_month = models.CharField(max_length=40, blank=True, default='')
    date = models.DateField(null=True, blank=True)
    or_number = models.CharField(max_length=60, blank=True, default='')
    series_day = models.CharField(max_length=40, blank=True, default='')
    account_number = EncryptedCharField(max_length=255, blank=True, default='')
    account_name = models.CharField(max_length=200)
    code_line = models.CharField(max_length=80, blank=True, default='')
    line = models.CharField(max_length=80, blank=True, default='')
    code_noc = models.CharField(max_length=80, blank=True, default='')
    nature_of_collections = models.CharField(max_length=255, blank=True, default='')
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    remarks = models.TextField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    tin = models.CharField(max_length=30, blank=True, default='', verbose_name='TIN')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    ors_burs_no = models.CharField(max_length=60, blank=True, default='', verbose_name='ORS/BURS No.')
    responsibility_center = models.CharField(max_length=60, blank=True, default='', verbose_name='Responsibility Center Code')
    reference_code = models.CharField(max_length=120, blank=True, default='', verbose_name='ORS/BURS No. & Responsibility Center')
    fund_cluster = models.ForeignKey('FundCluster', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Fund Cluster')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='radai_created')
    raw_import = models.JSONField(blank=True, null=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.account_name


# ── User email encryption: encrypt before DB write, decrypt after DB read ──

@receiver(pre_save, sender=User)
def encrypt_user_email(sender, instance, **kwargs):
    if instance.email and not instance.email.startswith('gAAAAA'):
        instance._email_hash_cache = hashlib.sha256(instance.email.lower().encode()).hexdigest()
        instance.email = encrypt_value(instance.email)

@receiver(post_init, sender=User)
def decrypt_user_email(sender, instance, **kwargs):
    if instance.email and instance.email.startswith('gAAAAA'):
        instance.email = decrypt_value(instance.email)


# Keep profile in sync with user role baseline.
@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    default_role = 'admin' if instance.is_superuser else 'cashier'
    profile, was_created = Profile.objects.get_or_create(
        user=instance,
        defaults={'role': default_role},
    )

    update_fields = []

    # Sync encrypted email + hash to Profile
    email_hash = getattr(instance, '_email_hash_cache', None)
    if email_hash and profile.email_hash != email_hash:
        profile.email_hash = email_hash
        update_fields.append('email_hash')

    if instance.email:
        if profile.email_encrypted != instance.email:
            profile.email_encrypted = instance.email
            update_fields.append('email_encrypted')
        # Also set the hash from the encrypted email value if cache wasn't set
        if not email_hash:
            h = hashlib.sha256(decrypt_value(instance.email).lower().encode()).hexdigest()
            if profile.email_hash != h:
                profile.email_hash = h
                update_fields.append('email_hash')

    if update_fields:
        profile.save(update_fields=update_fields)

    if not was_created and instance.is_superuser and profile.role != 'admin':
        profile.role = 'admin'
        profile.save(update_fields=['role'])