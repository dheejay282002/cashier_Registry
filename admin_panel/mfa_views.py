import json
import secrets

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from cashier.models import Profile
from admin_panel.models import SystemSetting, AuditLog
from .utils import _is_admin, _get_profile, _page_context, _audit


def _disable_email_otp_html(user, code):
    return f"""
    <html>
    <body>
    <h2>Disable Email OTP</h2>
    <p>Your OTP code to disable email verification is:</p>
    <h1 style="color: #1565c0; font-size: 32px; letter-spacing: 8px;">{code}</h1>
    <p>This code will expire in 10 minutes.</p>
    <p>If you did not request this, please ignore this email.</p>
    </body>
    </html>
    """


def _disable_totp_otp_html(user, code):
    return f"""
    <html>
    <body>
    <h2>Disable TOTP</h2>
    <p>Your OTP code to disable TOTP is:</p>
    <h1 style="color: #1565c0; font-size: 32px; letter-spacing: 8px;">{code}</h1>
    <p>This code will expire in 10 minutes.</p>
    <p>If you did not request this, please ignore this email.</p>
    </body>
    </html>
    """


@login_required
def mfa_setup(request):
    profile = _get_profile(request.user)
    is_admin = _is_admin(request.user)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'enable_totp':
            totp_secret = request.POST.get('totp_secret', '')
            totp_code = request.POST.get('totp_code', '')
            if totp_secret and totp_code:
                profile.totp_secret = totp_secret
                profile.totp_enabled = True
                profile.save(update_fields=['totp_secret', 'totp_enabled'])
                _audit(request.user, "Enabled TOTP MFA")
                return JsonResponse({"ok": True})
        elif action == 'enable_email':
            profile.email_otp_enabled = True
            profile.save(update_fields=['email_otp_enabled'])
            _audit(request.user, "Enabled Email MFA")
            return JsonResponse({"ok": True})
        elif action == 'disable':
            profile.totp_enabled = False
            profile.totp_secret = ''
            profile.email_otp_enabled = False
            profile.save(update_fields=['totp_enabled', 'totp_secret', 'email_otp_enabled'])
            _audit(request.user, "Disabled MFA")
            return JsonResponse({"ok": True})

    context = _page_context(request, is_admin, "MFA Setup", extra={
        "profile": profile,
    })
    return render(request, "admin_panel/mfa_setup.html", context)


@login_required
def mfa_regenerate_recovery_codes(request):
    profile = _get_profile(request.user)
    codes = [secrets.token_hex(4) for _ in range(8)]
    profile.mfa_recovery_codes = codes
    profile.save(update_fields=['mfa_recovery_codes'])
    _audit(request.user, "Regenerated MFA recovery codes")
    return JsonResponse({"ok": True, "codes": codes})


@login_required
def mfa_totp_qr(request):
    from .mfa_utils import get_totp_uri
    import qrcode.image.svg
    secret = request.GET.get("secret", "")
    if not secret:
        return JsonResponse({"error": "Missing secret"}, status=400)
    uri = get_totp_uri(secret, request.user.username)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgImage)
    from django.http import HttpResponse
    response = HttpResponse(content_type="image/svg+xml")
    img.save(response)
    return response


@csrf_exempt
def mfa_resend_email_otp(request):
    mfa_user_id = request.session.get("mfa_user_id")
    if not mfa_user_id:
        return JsonResponse({"ok": False, "error": "Session expired"}, status=400)
    try:
        user = User.objects.get(pk=mfa_user_id)
    except User.DoesNotExist:
        return JsonResponse({"ok": False, "error": "User not found"}, status=400)
    from .mfa_utils import generate_email_otp, send_otp_email
    sys_set = SystemSetting.get_settings()
    otp = generate_email_otp()
    request.session["email_otp_code"] = otp
    request.session["email_otp_created"] = timezone.now().isoformat()
    try:
        send_otp_email(user.email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo)
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@login_required
def mfa_send_disable_email_otp(request):
    from .mfa_utils import generate_email_otp, send_otp_email
    from admin_panel.fields import decrypt_value
    profile = _get_profile(request.user)
    otp = generate_email_otp()
    request.session["disable_email_otp_code"] = otp
    request.session["disable_email_otp_created"] = timezone.now().isoformat()
    sys_set = SystemSetting.get_settings()
    email = decrypt_value(profile.email_encrypted) if profile.email_encrypted else (request.user.email or '')
    if not email:
        return JsonResponse({"ok": False, "error": "No email address found."}, status=400)
    try:
        send_otp_email(email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo)
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@login_required
def mfa_send_disable_totp_otp(request):
    from .mfa_utils import generate_email_otp, send_otp_email
    from admin_panel.fields import decrypt_value
    profile = _get_profile(request.user)
    otp = generate_email_otp()
    request.session["disable_totp_otp_code"] = otp
    request.session["disable_totp_otp_created"] = timezone.now().isoformat()
    sys_set = SystemSetting.get_settings()
    email = decrypt_value(profile.email_encrypted) if profile.email_encrypted else (request.user.email or '')
    if not email:
        return JsonResponse({"ok": False, "error": "No email address found."}, status=400)
    try:
        send_otp_email(email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo)
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
