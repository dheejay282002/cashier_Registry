import pyotp
import secrets
import string
import base64
import hashlib
import mimetypes
from pathlib import Path
from django.core.mail import EmailMultiAlternatives
from django.conf import settings


def generate_recovery_codes(count=8):
    """Generate a list of unique recovery codes in XXXX-XXXX-XXXX format."""
    codes = set()
    while len(codes) < count:
        raw = secrets.token_hex(6).upper()
        formatted = f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"
        codes.add(formatted)
    return list(codes)


def hash_recovery_code(code):
    """Hash a recovery code using SHA-256 for secure storage."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def verify_recovery_code(code, hashed_codes):
    """Verify a recovery code against a list of hashed codes. Returns index if found, -1 otherwise."""
    code_hash = hash_recovery_code(code)
    for i, h in enumerate(hashed_codes):
        if secrets.compare_digest(code_hash, h):
            return i
    return -1

def generate_totp_secret():
    return pyotp.random_base32()

def get_totp_uri(secret, username, issuer="Registry"):
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)

def verify_totp(secret, code):
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)

def generate_email_otp(length=6):
    return ''.join(secrets.choice(string.digits) for _ in range(length))

def _logo_data_uri(logo_field):
    """Convert a Django ImageFieldFile to a base64 data URI for embedding in HTML email."""
    if not logo_field or not logo_field.name:
        return None
    try:
        path = logo_field.path
        if not Path(path).exists():
            return None
        with open(path, 'rb') as f:
            raw = f.read()
        mime, _ = mimetypes.guess_type(path)
        if not mime:
            mime = 'image/png'
        b64 = base64.b64encode(raw).decode('ascii')
        return f'data:{mime};base64,{b64}'
    except Exception:
        return None

def send_otp_email(to_email, otp_code, system_name=None, system_logo=None, extra_subject=None, extra_html=None):
    if extra_subject and extra_html:
        subject = extra_subject
        text_message = extra_html
        html_message = extra_html
    else:
        if not system_name:
            system_name = "Registry"
        subject = f"{system_name} – Your Login Code"

        logo_data = _logo_data_uri(system_logo)
        logo_html = ""
        if logo_data:
            logo_html = f'<img src="{logo_data}" alt="{system_name}" style="max-width:120px; height:auto; margin-bottom:16px; display:block; margin-left:auto; margin-right:auto;">'

        text_message = f"Your {system_name} login code: {otp_code}\n\nThis code expires in 5 minutes."

        html_message = f"""<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin:0; padding:0; background:#f4f6f8; font-family:'Segoe UI',Arial,sans-serif;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8; padding:40px 0;">
            <tr><td align="center">
                <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08);">
                    <tr><td style="padding:32px 40px; text-align:center; background:#1a3a2e;">
                        {logo_html}
                        <h1 style="color:#fff; font-size:20px; font-weight:600; margin:0;">{system_name}</h1>
                    </td></tr>
                    <tr><td style="padding:32px 40px;">
                        <p style="color:#374151; font-size:15px; margin:0 0 8px;">Your one-time login code:</p>
                        <div style="background:#f0fdf4; border:1px solid #86efac; border-radius:10px; padding:18px; text-align:center; margin:16px 0;">
                            <span style="font-size:38px; font-weight:700; letter-spacing:8px; color:#15803d;">{otp_code}</span>
                        </div>
                        <p style="color:#6b7280; font-size:13px; margin:0;">This code expires in <strong>5 minutes</strong>. If you did not request this, you can safely ignore this email.</p>
                    </td></tr>
                    <tr><td style="padding:16px 40px; background:#f9fafb; text-align:center; border-top:1px solid #e5e7eb;">
                        <p style="color:#9ca3af; font-size:11px; margin:0;">&copy; {system_name}</p>
                    </td></tr>
                </table>
            </td></tr>
        </table>
    </body>
    </html>"""

    from_email = settings.DEFAULT_FROM_EMAIL
    try:
        from admin_panel.models import SystemSetting
        sys_cfg = SystemSetting.get_settings()
        db_from = (sys_cfg.default_from_email or '').strip()
        db_user = (sys_cfg.email_host_user or '').strip()
        if db_from:
            from_email = db_from
        elif db_user:
            from_email = db_user
    except Exception:
        pass
    if system_name and from_email and '@' in from_email and 'localhost' not in from_email:
        from_email = f'"{system_name}" <{from_email}>'
    msg = EmailMultiAlternatives(subject, text_message, from_email, [to_email])
    msg.attach_alternative(html_message, "text/html")
    msg.send(fail_silently=False)
