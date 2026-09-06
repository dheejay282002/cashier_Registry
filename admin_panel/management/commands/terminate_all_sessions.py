from django.core.management.base import BaseCommand
from django.contrib.sessions.models import Session
from cashier.models import Profile, UserDevice


class Command(BaseCommand):
    help = "Terminate all logged-in users by clearing sessions, devices, and active session keys."

    def handle(self, *args, **options):
        # 1. Mark all non-terminated UserDevice records as terminated
        updated_devices = UserDevice.objects.filter(is_terminated=False).update(is_terminated=True)

        # 2. Delete all Django sessions
        session_count = Session.objects.count()
        Session.objects.all().delete()

        # 3. Clear active_session_key on all profiles
        updated_profiles = Profile.objects.exclude(active_session_key='').update(active_session_key='')

        self.stdout.write(self.style.SUCCESS(
            f"Done. Terminated {updated_devices} device(s), "
            f"deleted {session_count} session(s), "
            f"cleared {updated_profiles} profile session key(s)."
        ))
