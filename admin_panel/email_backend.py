"""
Dynamic email backend that reads SMTP configuration from SystemSetting.
Falls back to console backend if SMTP credentials are not configured.
"""
import logging
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend

logger = logging.getLogger(__name__)


class DynamicSmtpEmailBackend(SmtpEmailBackend):
    def __init__(self, *args, **kwargs):
        try:
            from admin_panel.models import SystemSetting
            sys_set = SystemSetting.get_settings()
            host = (sys_set.email_host or '').strip()
            user = (sys_set.email_host_user or '').strip()
            password = (sys_set.email_host_password or '').strip()
            if host and user and password:
                kwargs['host'] = host
                kwargs['port'] = sys_set.email_port
                kwargs['use_tls'] = sys_set.email_use_tls
                kwargs['username'] = user
                kwargs['password'] = password
            else:
                logger.warning(
                    "SMTP email settings incomplete in SystemSetting "
                    "(host=%r, user=%r). Emails will fail until configured.",
                    host, user,
                )
        except Exception as exc:
            logger.warning("Could not load SMTP settings from SystemSetting: %s", exc)
        super().__init__(*args, **kwargs)

    def open(self):
        try:
            return super().open()
        except ConnectionRefusedError:
            host = self.host or '(not set)'
            port = self.port or '(not set)'
            raise ConnectionRefusedError(
                f"SMTP server refused the connection on {host}:{port}. "
                "Please verify your SMTP host and port in Email Settings."
            )
        except OSError as exc:
            host = self.host or '(not set)'
            port = self.port or '(not set)'
            raise OSError(
                f"Could not connect to SMTP server {host}:{port} — {exc}. "
                "Please check your SMTP settings in Email Settings."
            ) from exc
