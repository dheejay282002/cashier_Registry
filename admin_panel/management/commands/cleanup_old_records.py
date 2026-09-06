import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger("admin_panel.rotation")


class Command(BaseCommand):
    help = "Remove old audit log entries and stale user device records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--audit-log-days", type=int, default=90,
            help="Delete audit logs older than this many days (default: 90)",
        )
        parser.add_argument(
            "--device-inactive-days", type=int, default=30,
            help="Delete user device records inactive for this many days (default: 30)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        audit_days = options["audit_log_days"]
        device_days = options["device_inactive_days"]

        now = timezone.now()
        audit_cutoff = now - timedelta(days=audit_days)
        device_cutoff = now - timedelta(days=device_days)

        from admin_panel.models import AuditLog
        from cashier.models import UserDevice

        old_logs = AuditLog.objects.filter(timestamp__lt=audit_cutoff)
        old_log_count = old_logs.count()
        if old_log_count > 0:
            if dry_run:
                self.stdout.write(f"Would delete {old_log_count} audit log entries older than {audit_days} days")
            else:
                old_logs.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {old_log_count} old audit log entries"))

        stale_devices = UserDevice.objects.filter(
            last_activity__lt=device_cutoff,
            is_terminated=False,
        )
        stale_count = stale_devices.count()
        if stale_count > 0:
            if dry_run:
                self.stdout.write(f"Would delete {stale_count} stale device records older than {device_days} days")
            else:
                stale_devices.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {stale_count} stale device records"))

        terminated_devices = UserDevice.objects.filter(is_terminated=True)
        terminated_count = terminated_devices.count()
        if terminated_count > 0:
            if dry_run:
                self.stdout.write(f"Would delete {terminated_count} terminated device records")
            else:
                terminated_devices.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {terminated_count} terminated device records"))

        if old_log_count == 0 and stale_count == 0 and terminated_count == 0:
            self.stdout.write("No old records to clean up.")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete — no changes made."))
