"""
Django management command to execute the automated 5-day system backup.

Can be run manually via:
    python manage.py run_auto_backup
Or forced via:
    python manage.py run_auto_backup --force
"""

from django.core.management.base import BaseCommand
from admin_panel.views import _run_auto_backup
from admin_panel.models import SystemSetting


class Command(BaseCommand):
    help = "Run automated 5-day system backup and copy to Downloads folder"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force backup creation even if 5 days have not elapsed yet.",
        )

    def handle(self, *args, **options):
        force = options.get("force", False)
        settings = SystemSetting.get_settings()

        self.stdout.write(self.style.MIGRATE_HEADING("=== Automated 5-Day System Backup ==="))
        self.stdout.write(f"Auto-backup enabled: {settings.auto_backup_enabled}")
        self.stdout.write(f"Interval: every {settings.auto_backup_interval_days} days")
        self.stdout.write(f"Copy to Downloads: {settings.auto_backup_to_downloads}")
        self.stdout.write(f"Last auto-backup: {settings.last_auto_backup or 'Never'}")

        res = _run_auto_backup(force=force)

        if res.get("executed"):
            self.stdout.write(self.style.SUCCESS(f"\n[OK] Backup successfully created: {res.get('filename')}"))
            self.stdout.write(f" - Stored on server: {res.get('server_path')}")
            if res.get("downloads_path"):
                self.stdout.write(self.style.SUCCESS(f" - Copied to Downloads: {res.get('downloads_path')}"))
            else:
                self.stdout.write(" - Downloads copy: Skipped (disabled or unavailable)")
        else:
            reason = res.get("reason", "unknown")
            if reason == "not_due":
                rem_sec = res.get("remaining_seconds", 0)
                rem_days = rem_sec / 86400.0
                self.stdout.write(
                    self.style.WARNING(
                        f"\n[SKIP] Backup is not due yet. {rem_days:.1f} days remaining until next scheduled backup. (Use --force to run anyway)"
                    )
                )
            elif reason == "auto_backup_disabled":
                self.stdout.write(
                    self.style.WARNING("\n[SKIP] Automated backup is disabled in System Settings. (Use --force to override)")
                )
            else:
                self.stdout.write(self.style.ERROR(f"\n[ERROR] Backup could not be executed: {reason}"))
