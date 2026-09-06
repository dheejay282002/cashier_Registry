import os
import getpass
import platform
import socket
import urllib.request
import json
import re

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from cashier.models import Profile, UserDevice, DEFAULT_ROLE_PERMISSIONS
from admin_panel.models import SystemSetting, AuditLog
from admin_panel.fields import decrypt_value
from .utils import (
    _is_admin, _get_profile, _page_context, _audit,
    _validate_password, _full_name_or_username
)


# ─────────────────────────────────────────────
# DEVICE / IP HELPERS
# ─────────────────────────────────────────────

def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip or '127.0.0.1'


def _get_client_device_and_os(request):
    ip = _get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    os_name = "Unknown OS"
    browser_name = "Unknown Browser"
    ua = user_agent.lower()

    if "windows" in ua:
        os_name = "Windows"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"

    if "chrome" in ua and "safari" in ua and "edge" not in ua and "opr" not in ua:
        browser_name = "Chrome"
    elif "firefox" in ua:
        browser_name = "Firefox"
    elif "safari" in ua and "chrome" not in ua:
        browser_name = "Safari"
    elif "edge" in ua or "edg" in ua:
        browser_name = "Edge"
    elif "opr" in ua or "opera" in ua:
        browser_name = "Opera"

    device_desc = f"{os_name} ({browser_name})"
    win_user = ""
    comp_name = ""

    if ip in ('127.0.0.1', 'localhost', '::1'):
        try:
            win_user = os.getlogin()
        except Exception:
            try:
                win_user = getpass.getuser()
            except Exception:
                win_user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
        try:
            comp_name = platform.node() or socket.gethostname() or ''
        except Exception:
            pass
    else:
        try:
            comp_name, _, _ = socket.gethostbyaddr(ip)
        except Exception:
            comp_name = f"Remote PC"

    return {
        'device_name': comp_name or f"{os_name} PC",
        'windows_username': win_user or "Network Client",
        'os_and_browser': device_desc
    }


def _get_location_from_ip(ip):
    if ip in ('127.0.0.1', 'localhost', '::1') or ip.startswith('192.168.') or ip.startswith('10.'):
        return "Local Network"
    try:
        url = f"http://ip-api.com/json/{ip}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            if data.get('status') == 'success':
                city = data.get('city', '')
                country = data.get('country', '')
                if city and country:
                    return f"{city}, {country}"
                return country or "Unknown Location"
    except Exception:
        pass
    return "Unknown Location"


def _is_device_blocked(ip, device_name, windows_username, user_agent):
    blocked_devices = UserDevice.objects.filter(is_blocked=True)
    for dev in blocked_devices:
        if dev.ip_address == ip and dev.user_agent == user_agent:
            return True
        if (dev.device_name == device_name and dev.windows_username == windows_username and
            device_name not in ('Remote PC', '', 'Unknown Device') and
            windows_username not in ('Network Client', '', 'Unknown')):
            return True
        if dev.ip_address == ip and dev.device_name == device_name and device_name not in ('Remote PC', '', 'Unknown Device'):
            return True
    return False


def _is_session_active(profile):
    from django.contrib.sessions.models import Session
    if not profile.active_session_key:
        return False

    session_exists = Session.objects.filter(
        session_key=profile.active_session_key,
        expire_date__gt=timezone.now()
    ).exists()
    if not session_exists:
        return False

    if not profile.last_activity:
        return False

    auto_logout = profile.auto_logout_seconds
    if auto_logout is None:
        try:
            settings_obj = SystemSetting.get_settings()
            auto_logout = getattr(settings_obj, 'auto_logout_seconds', 3600)
        except Exception:
            auto_logout = 3600

    try:
        threshold = max(60, min(86400, int(auto_logout)))
    except (TypeError, ValueError):
        threshold = 3600

    elapsed = (timezone.now() - profile.last_activity).total_seconds()
    return elapsed <= threshold


# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────

def _register_session(request, user):
    try:
        profile = user.profile
        if not request.session.session_key:
            request.session.create()
        old_key = profile.active_session_key
        new_key = request.session.session_key or ''

        if old_key and old_key != new_key:
            try:
                Session.objects.filter(session_key=old_key).delete()
            except Exception:
                pass
            UserDevice.objects.filter(session_key=old_key).update(is_terminated=True)

        profile.active_session_key = new_key
        profile.last_activity = timezone.now()
        if profile.status != 'active':
            profile.status = 'active'
            profile.save(update_fields=['active_session_key', 'last_activity', 'status'])
        else:
            profile.save(update_fields=['active_session_key', 'last_activity'])

        ip = _get_client_ip(request)
        dev_info = _get_client_device_and_os(request)
        loc = _get_location_from_ip(ip)

        UserDevice.objects.filter(session_key=new_key).delete()
        UserDevice.objects.create(
            user=user,
            session_key=new_key,
            device_name=dev_info['device_name'],
            windows_username=dev_info['windows_username'],
            ip_address=ip,
            location=loc,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            last_activity=timezone.now()
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
# LOGIN / LOGOUT
# ─────────────────────────────────────────────

def logout_view(request):
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.status == 'active':
                profile.status = 'resting'
                profile.save(update_fields=['status'])
        except Exception:
            pass
        try:
            from .models import ChatMessage
            ChatMessage.objects.filter(user=request.user).delete()
        except Exception:
            pass
        try:
            profile = request.user.profile
            UserDevice.objects.filter(session_key=request.session.session_key).update(is_terminated=True)
            if profile.active_session_key == request.session.session_key:
                profile.active_session_key = ''
                profile.save(update_fields=['active_session_key'])
        except Exception:
            pass
    logout(request)
    return render(request, "logout_loading.html")


def _mfa_redirect(user):
    try:
        role = user.profile.role
    except AttributeError:
        role = "cashier"
    if role == "admin":
        return redirect("admin_dashboard")
    return redirect("cashier_dashboard")


def login_view(request):
    error = None
    mfa_pick = False
    mfa_step = False
    mfa_method = None
    email_otp_sent = False
    change_pw = False
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    last_username = request.POST.get("username", "")

    # ── Step: Change password (must_change_password) ─────────────────
    if request.method == "POST" and request.POST.get("change_pw"):
        cp_user_id = request.session.get("change_pw_user_id")
        if not cp_user_id:
            error = "Session expired, please login again."
        else:
            try:
                cp_user = User.objects.get(pk=cp_user_id)
                profile = cp_user.profile
            except (User.DoesNotExist, AttributeError):
                error = "User not found."
                request.session.pop("change_pw_user_id", None)
                cp_user = None

            if cp_user:
                ip = _get_client_ip(request)
                dev_info = _get_client_device_and_os(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
                    error = "This device has been blocked by the system administrator and cannot be used to log in."
                    change_pw = True
                else:
                    new_password = request.POST.get("new_password", "")
                    confirm_password = request.POST.get("confirm_password", "")
                    if new_password != confirm_password:
                        error = "Passwords do not match."
                    else:
                        pw_err = _validate_password(new_password)
                        if pw_err:
                            error = pw_err
                        else:
                            cp_user.set_password(new_password)
                            cp_user.save()
                            profile.must_change_password = False
                            profile.save(update_fields=["must_change_password"])
                            request.session.pop("change_pw_user_id", None)
                            _audit(cp_user, "Changed temporary password")
                            if profile.totp_enabled or profile.email_otp_enabled:
                                request.session["mfa_user_id"] = cp_user.pk
                                request.session["mfa_after_change_pw"] = True
                                mfa_pick = True
                            else:
                                cp_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                login(request, cp_user)
                                _register_session(request, cp_user)
                                return _mfa_redirect(cp_user)

        if request.session.get("change_pw_user_id"):
            change_pw = True

    # ── Step 2: Pick MFA method ──────────────────────────────────────
    elif request.method == "POST" and request.POST.get("pick_mfa"):
        mfa_user_id = request.session.get("mfa_user_id")
        if not mfa_user_id:
            error = "Session expired, please login again."
        else:
            pick = request.POST.get("pick_mfa")
            mfa_method = pick
            request.session["mfa_method"] = pick
            profile = Profile.objects.get(user_id=mfa_user_id)

            if pick == "email" and profile.email_otp_enabled:
                from .mfa_utils import generate_email_otp, send_otp_email
                sys_set = SystemSetting.get_settings()
                otp = generate_email_otp()
                request.session["email_otp_code"] = otp
                request.session["email_otp_created"] = timezone.now().isoformat()
                try:
                    user_email = User.objects.get(pk=mfa_user_id).email
                    send_otp_email(user_email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo)
                    email_otp_sent = True
                except Exception:
                    error = "Failed to send OTP email. Try again."
                    mfa_pick = True
                else:
                    mfa_step = True
            elif pick == "totp" and profile.totp_enabled:
                mfa_step = True
            else:
                error = "Selected MFA method not available."
                mfa_pick = True

    # ── Step 3: Verify MFA code ──────────────────────────────────────
    elif request.method == "POST" and (request.POST.get("mfa_code") or request.POST.get("use_recovery")):
        mfa_user_id = request.session.get("mfa_user_id")
        if not mfa_user_id:
            error = "Session expired, please login again."
        else:
            try:
                mfa_user = User.objects.get(pk=mfa_user_id)
                profile = mfa_user.profile
            except (User.DoesNotExist, AttributeError):
                error = "User not found."
                request.session.pop("mfa_user_id", None)
                request.session.pop("mfa_method", None)
                request.session.pop("email_otp_code", None)
                request.session.pop("mfa_after_change_pw", None)
                mfa_user = None

            if mfa_user:
                ip = _get_client_ip(request)
                dev_info = _get_client_device_and_os(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
                    error = "This device has been blocked by the system administrator and cannot be used to log in."
                    mfa_step = True
                    mfa_method = request.session.get("mfa_method", "totp")
                else:
                    from .mfa_utils import verify_totp, verify_recovery_code
                    method = request.session.get("mfa_method", "totp")
                    use_recovery = request.POST.get("use_recovery")
                    code = request.POST.get("mfa_code", "").strip()
                    recovery_code = request.POST.get("recovery_code", "").strip()

                    if use_recovery and recovery_code:
                        recovery_codes = profile.mfa_recovery_codes or []
                        idx = verify_recovery_code(recovery_code, recovery_codes) if recovery_codes else -1
                        if idx >= 0:
                            recovery_codes.pop(idx)
                            profile.mfa_recovery_codes = recovery_codes
                            profile.save(update_fields=["mfa_recovery_codes"])
                            if _is_session_active(profile):
                                profile.login_attempt_blocked = True
                                profile.blocked_login_time = timezone.now()
                                profile.blocked_login_ip = _get_client_ip(request)
                                profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])
                                error = "This account is already logged in on another device. Concurrent logins are not allowed. The active session has been notified."
                                mfa_step = True
                                mfa_method = method
                            else:
                                mfa_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                login(request, mfa_user)
                                _register_session(request, mfa_user)
                                after_change_pw = request.session.pop("mfa_after_change_pw", None)
                                request.session.pop("mfa_user_id", None)
                                request.session.pop("mfa_method", None)
                                request.session.pop("email_otp_code", None)
                                request.session.pop("mfa_failed_attempts", None)
                                if next_url and not after_change_pw:
                                    return redirect(next_url)
                                return _mfa_redirect(mfa_user)
                        else:
                            error = "Invalid recovery code. Please check your code and try again."
                            mfa_step = True
                            mfa_method = method
                    elif method == "totp" and profile.totp_enabled:
                        if verify_totp(profile.totp_secret, code):
                            if _is_session_active(profile):
                                profile.login_attempt_blocked = True
                                profile.blocked_login_time = timezone.now()
                                profile.blocked_login_ip = _get_client_ip(request)
                                profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])
                                error = "This account is already logged in on another device. Concurrent logins are not allowed. The active session has been notified."
                                mfa_step = True
                                mfa_method = method
                            else:
                                mfa_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                login(request, mfa_user)
                                _register_session(request, mfa_user)
                                after_change_pw = request.session.pop("mfa_after_change_pw", None)
                                request.session.pop("mfa_user_id", None)
                                request.session.pop("mfa_method", None)
                                request.session.pop("email_otp_code", None)
                                request.session.pop("mfa_failed_attempts", None)
                                if next_url and not after_change_pw:
                                    return redirect(next_url)
                                return _mfa_redirect(mfa_user)
                        else:
                            recovery_codes = profile.mfa_recovery_codes or []
                            idx = verify_recovery_code(code, recovery_codes) if recovery_codes else -1
                            if idx >= 0:
                                recovery_codes.pop(idx)
                                profile.mfa_recovery_codes = recovery_codes
                                profile.save(update_fields=["mfa_recovery_codes"])
                                if _is_session_active(profile):
                                    profile.login_attempt_blocked = True
                                    profile.blocked_login_time = timezone.now()
                                    profile.blocked_login_ip = _get_client_ip(request)
                                    profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])
                                    error = "This account is already logged in on another device. Concurrent logins are not allowed. The active session has been notified."
                                    mfa_step = True
                                    mfa_method = method
                                else:
                                    mfa_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                    login(request, mfa_user)
                                    _register_session(request, mfa_user)
                                    after_change_pw = request.session.pop("mfa_after_change_pw", None)
                                    request.session.pop("mfa_user_id", None)
                                    request.session.pop("mfa_method", None)
                                    request.session.pop("email_otp_code", None)
                                    request.session.pop("mfa_failed_attempts", None)
                                    if next_url and not after_change_pw:
                                        return redirect(next_url)
                                    return _mfa_redirect(mfa_user)
                            else:
                                error = "Invalid authenticator code."
                                mfa_step = True
                                mfa_method = method
                                mfa_attempts = request.session.get("mfa_failed_attempts", 0) + 1
                                request.session["mfa_failed_attempts"] = mfa_attempts
                                if mfa_attempts >= 3 and mfa_user.email:
                                    from .mfa_utils import generate_recovery_codes, hash_recovery_code
                                    from django.core.mail import send_mail
                                    from django.conf import settings
                                    single_code = generate_recovery_codes(count=1)[0]
                                    recovery_codes = profile.mfa_recovery_codes or []
                                    recovery_codes.append(hash_recovery_code(single_code))
                                    profile.mfa_recovery_codes = recovery_codes
                                    profile.save(update_fields=["mfa_recovery_codes"])
                                    try:
                                        send_mail(
                                            subject="Your Recovery Code",
                                            message=f"Hello {mfa_user.first_name or mfa_user.username},\n\n"
                                                    f"You've entered an incorrect MFA code 3 times.\n\n"
                                                    f"Here is a one-time recovery code to access your account:\n\n"
                                                    f"{single_code}\n\n"
                                                    f"Use this code at the login screen to bypass the authenticator.\n\n"
                                                    f"If you did not attempt to log in, please contact your administrator.",
                                            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
                                            recipient_list=[mfa_user.email],
                                            fail_silently=True,
                                        )
                                        error += " A recovery code has been sent to your email."
                                    except Exception:
                                        pass
                                    request.session["mfa_failed_attempts"] = 0
                    elif method == "email" and profile.email_otp_enabled:
                        from datetime import timedelta
                        expected = request.session.get("email_otp_code")
                        otp_created = request.session.get("email_otp_created")
                        otp_expired = False
                        if otp_created:
                            try:
                                created_dt = timezone.datetime.fromisoformat(otp_created)
                                if timezone.is_naive(created_dt):
                                    created_dt = timezone.make_aware(created_dt)
                                otp_expired = (timezone.now() - created_dt) > timedelta(minutes=10)
                            except Exception:
                                otp_expired = True
                        if otp_expired:
                            error = "OTP code has expired. Please request a new code."
                            mfa_step = True
                            mfa_method = method
                            request.session.pop("email_otp_code", None)
                            request.session.pop("email_otp_created", None)
                        elif code == expected:
                            if _is_session_active(profile):
                                profile.login_attempt_blocked = True
                                profile.blocked_login_time = timezone.now()
                                profile.blocked_login_ip = _get_client_ip(request)
                                profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])
                                error = "This account is already logged in on another device. Concurrent logins are not allowed. The active session has been notified."
                                mfa_step = True
                                mfa_method = method
                            else:
                                mfa_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                login(request, mfa_user)
                                _register_session(request, mfa_user)
                                after_change_pw = request.session.pop("mfa_after_change_pw", None)
                                request.session.pop("mfa_user_id", None)
                                request.session.pop("mfa_method", None)
                                request.session.pop("email_otp_code", None)
                                if next_url and not after_change_pw:
                                    return redirect(next_url)
                                return _mfa_redirect(mfa_user)
                        else:
                            error = "Invalid OTP code. Please try again."
                            mfa_step = True
                            mfa_method = method
                    else:
                        error = "MFA method not available."
                        request.session.pop("mfa_user_id", None)
                        request.session.pop("mfa_method", None)
                        request.session.pop("email_otp_code", None)
                        request.session.pop("mfa_after_change_pw", None)
                        mfa_step = True
                        mfa_method = method

    # ── Step 1: Username / password ──────────────────────────────────
    elif request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            ip = _get_client_ip(request)
            dev_info = _get_client_device_and_os(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
                error = "This device has been blocked by the system administrator and cannot be used to log in."
            else:
                profile, created = Profile.objects.get_or_create(user=user, defaults={"role": "admin" if user.is_superuser else "cashier"})
                if user.is_superuser and profile.role != "admin":
                    profile.role = "admin"
                    profile.save(update_fields=["role"])

                if _is_session_active(profile):
                    profile.login_attempt_blocked = True
                    profile.blocked_login_time = timezone.now()
                    profile.blocked_login_ip = _get_client_ip(request)
                    profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])
                    error = "This account is already logged in on another device. Concurrent logins are not allowed. The active session has been notified."
                else:
                    needs_mfa = profile.totp_enabled or profile.email_otp_enabled
                    if profile.must_change_password:
                        request.session["change_pw_user_id"] = user.pk
                        change_pw = True
                    elif needs_mfa:
                        request.session["mfa_user_id"] = user.pk
                        mfa_pick = True
                    else:
                        user.backend = 'django.contrib.auth.backends.ModelBackend'
                        login(request, user)
                        _register_session(request, user)
                        _audit(user, "Logged in")
                        if next_url:
                            return redirect(next_url)
                        return _mfa_redirect(user)
        else:
            error = "Invalid username or password."

    mfa_has_totp = False
    mfa_has_email = False
    user_email = None
    mfa_user_id = request.session.get("mfa_user_id")
    if mfa_user_id:
        try:
            mfa_user_obj = User.objects.get(pk=mfa_user_id)
            user_email = mfa_user_obj.email
            mfa_profile = mfa_user_obj.profile
            mfa_has_totp = mfa_profile.totp_enabled
            mfa_has_email = mfa_profile.email_otp_enabled
        except (User.DoesNotExist, AttributeError):
            pass

    kicked = request.GET.get('kicked') == '1'
    context = {
        "error": error,
        "mfa_pick": mfa_pick,
        "mfa_step": mfa_step,
        "mfa_method": mfa_method,
        "email_otp_sent": email_otp_sent,
        "change_pw": change_pw,
        "user_email": user_email,
        "mfa_has_totp": mfa_has_totp,
        "mfa_has_email": mfa_has_email,
        "next": next_url,
        "kicked": kicked,
        "last_username": last_username,
    }
    return render(request, "login.html", context)


# ─────────────────────────────────────────────
# SESSION CHECK / DEVICE MANAGEMENT
# ─────────────────────────────────────────────

@login_required
def session_check(request):
    profile = _get_profile(request.user)
    return JsonResponse({
        "active": True,
        "session_key": request.session.session_key,
        "active_session_key": profile.active_session_key,
        "is_current": request.session.session_key == profile.active_session_key,
    })


@csrf_exempt
def update_screen_capture(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
        screenshot = data.get('screenshot', '')
        if request.user.is_authenticated:
            profile = _get_profile(request.user)
            profile.last_screen_capture = screenshot
            profile.save(update_fields=['last_screen_capture'])
            return JsonResponse({"ok": True})
    except Exception:
        pass
    return JsonResponse({"error": "Invalid"}, status=400)


@login_required
def manage_devices(request):
    is_admin = _is_admin(request.user)
    devices = UserDevice.objects.select_related('user').order_by('-last_activity')[:50]
    context = _page_context(request, is_admin, "Manage Devices", extra={
        "devices": devices,
    })
    return render(request, "admin_panel/manage_devices.html", context)


@login_required
def terminate_session(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    device_id = request.POST.get('device_id')
    try:
        device = UserDevice.objects.get(pk=device_id)
        device.is_terminated = True
        device.save(update_fields=['is_terminated'])
        return JsonResponse({"ok": True})
    except UserDevice.DoesNotExist:
        return JsonResponse({"error": "Device not found"}, status=404)


@login_required
def live_captures(request):
    if not _is_admin(request.user):
        return JsonResponse({"error": "Forbidden"}, status=403)
    captures = []
    profiles = Profile.objects.exclude(last_screen_capture='').select_related('user')
    for p in profiles:
        captures.append({
            "user": p.user.username,
            "screenshot": p.last_screen_capture,
            "updated": p.updated_at.isoformat() if p.updated_at else "",
        })
    return JsonResponse({"captures": captures})


@login_required
def toggle_device_block(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    device_id = request.POST.get('device_id')
    try:
        device = UserDevice.objects.get(pk=device_id)
        device.is_blocked = not device.is_blocked
        device.save(update_fields=['is_blocked'])
        return JsonResponse({"ok": True, "is_blocked": device.is_blocked})
    except UserDevice.DoesNotExist:
        return JsonResponse({"error": "Device not found"}, status=404)


@login_required
def unterminate_session(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    device_id = request.POST.get('device_id')
    try:
        device = UserDevice.objects.get(pk=device_id)
        device.is_terminated = False
        device.save(update_fields=['is_terminated'])
        return JsonResponse({"ok": True})
    except UserDevice.DoesNotExist:
        return JsonResponse({"error": "Device not found"}, status=404)


@login_required
def delete_device(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    device_id = request.POST.get('device_id')
    try:
        device = UserDevice.objects.get(pk=device_id)
        device.delete()
        return JsonResponse({"ok": True})
    except UserDevice.DoesNotExist:
        return JsonResponse({"error": "Device not found"}, status=404)


# ─────────────────────────────────────────────
# LOCKSCREEN
# ─────────────────────────────────────────────

@login_required
def lockscreen_view(request):
    return render(request, "lockscreen.html")


@login_required
def verify_password(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    password = request.POST.get('password', '')
    user = authenticate(request, username=request.user.username, password=password)
    if user:
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "error": "Incorrect password"}, status=401)


@login_required
def trigger_lock(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    cancel = request.POST.get('cancel', '0') == '1'
    if cancel:
        request.session['screen_locked'] = False
        request.session['screen_lock_hard'] = False
    else:
        hard = request.POST.get('hard', '0') == '1'
        request.session['screen_locked'] = True
        request.session['screen_lock_hard'] = hard
    request.session.modified = True
    return JsonResponse({"ok": True})


# ─────────────────────────────────────────────
# RFID
# ─────────────────────────────────────────────

@csrf_exempt
@require_POST
def rfid_lookup(request):
    rfid_uid = request.POST.get('rfid_uid', '').strip()
    if not rfid_uid:
        return JsonResponse({"error": "No RFID UID provided"}, status=400)
    try:
        profile = Profile.objects.get(rfid_uid=rfid_uid)
        return JsonResponse({
            "ok": True,
            "user_id": profile.user.pk,
            "username": profile.user.username,
            "full_name": _full_name_or_username(profile.user),
            "role": profile.role,
        })
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "RFID not recognized"}, status=404)


@csrf_exempt
@require_POST
def rfid_auth(request):
    rfid_uid = request.POST.get('rfid_uid', '').strip()
    if not rfid_uid:
        return JsonResponse({"error": "No RFID UID provided"}, status=400)
    try:
        profile = Profile.objects.get(rfid_uid=rfid_uid)
        user = profile.user
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "RFID not recognized"}, status=404)

    ip = _get_client_ip(request)
    dev_info = _get_client_device_and_os(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
        return JsonResponse({"ok": False, "error": "Device blocked"}, status=403)

    if _is_session_active(profile):
        return JsonResponse({"ok": False, "error": "Already logged in elsewhere"}, status=409)

    login(request, user)
    _register_session(request, user)
    _audit(user, "Logged in via RFID")
    return JsonResponse({"ok": True, "redirect": "/"})


# ─────────────────────────────────────────────
# ROLE PERMISSIONS
# ─────────────────────────────────────────────

@login_required
def role_permissions(request):
    if not _is_admin(request.user):
        return render(request, "access_denied.html")
    roles = DEFAULT_ROLE_PERMISSIONS
    return JsonResponse({"roles": roles})


@login_required
def save_role_permissions(request):
    if not _is_admin(request.user):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return JsonResponse({"ok": True})


# ─────────────────────────────────────────────
# BIOMETRIC ENROLLMENT
# ─────────────────────────────────────────────

@login_required
def start_fingerprint_enroll(request):
    return JsonResponse({"ok": True, "message": "Fingerprint enrollment initiated"})


@login_required
def start_face_enroll(request):
    return JsonResponse({"ok": True, "message": "Face enrollment initiated"})


@login_required
def face_enroll(request):
    return JsonResponse({"ok": True, "message": "Face enrollment completed"})


@login_required
def voice_enroll(request):
    return JsonResponse({"ok": True, "message": "Voice enrollment completed"})


# ─────────────────────────────────────────────
# STATIC PAGES
# ─────────────────────────────────────────────

@login_required
def admin_user_manual(request):
    return render(request, "admin_panel/admin_user_manual.html")


@login_required
def cashier_user_manual(request):
    return render(request, "cashier/cashier_user_manual.html")


@csrf_exempt
def check_user_exists(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    username = request.POST.get("username", "").strip()
    if not username:
        return JsonResponse({"exists": False})

    # Try username lookup first
    try:
        user = User.objects.get(username__iexact=username)
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"role": "admin" if user.is_superuser else "cashier"})
        display_name = user.get_full_name() or user.username
        return JsonResponse({
            "exists": True,
            "display_name": display_name,
            "username": user.username,
        })
    except User.DoesNotExist:
        pass

    # Try email lookup via backend
    from admin_panel.backends import EmailOrUsernameModelBackend
    user = EmailOrUsernameModelBackend._find_user_by_email(username)
    if user:
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"role": "admin" if user.is_superuser else "cashier"})
        display_name = user.get_full_name() or user.username
        return JsonResponse({
            "exists": True,
            "display_name": display_name,
            "username": user.username,
        })

    return JsonResponse({"exists": False})


def access_denied(request):
    return render(request, "access_denied.html")


def forgot_password(request):
    from admin_panel.utils import _validate_password

    error = None
    success = None
    step = 1
    identifier = ''
    masked_email = ''
    code_verified = False

    if request.method == 'POST':
        post_step = request.POST.get('step', '1')

        if post_step == '1':
            identifier = request.POST.get('identifier', '').strip()
            if not identifier:
                error = 'Please enter your email or username.'
            else:
                user = None
                if '@' in identifier:
                    from admin_panel.backends import EmailOrUsernameModelBackend
                    user = EmailOrUsernameModelBackend._find_user_by_email(identifier)
                else:
                    try:
                        user = User.objects.get(username__iexact=identifier)
                    except User.DoesNotExist:
                        user = None

                if user is None:
                    error = 'No account found with that email or username.'
                else:
                    profile, _ = Profile.objects.get_or_create(user=user, defaults={'role': 'cashier'})
                    email = decrypt_value(profile.email_encrypted) if profile.email_encrypted else (user.email or '')
                    if not email:
                        error = 'No email address is associated with this account. Please contact your administrator.'
                    else:
                        from .mfa_utils import generate_email_otp, send_otp_email
                        sys_set = SystemSetting.get_settings()
                        otp = generate_email_otp()
                        request.session['forgot_pw_user_id'] = user.pk
                        request.session['forgot_pw_code'] = otp
                        request.session['forgot_pw_code_created'] = timezone.now().isoformat()
                        request.session['forgot_pw_email'] = email
                        try:
                            send_otp_email(email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo,
                                           extra_subject=f'{sys_set.system_name} - Password Reset Code',
                                           extra_html=_password_reset_email_html(email, otp, sys_set.system_name, sys_set.system_logo))
                            step = 2
                            parts = email.split('@')
                            masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'
                            identifier = identifier
                        except Exception as e:
                            error = 'Failed to send reset code. Please try again.'

        elif post_step == 'resend_otp':
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            user_id = request.session.get('forgot_pw_user_id')
            saved_email = request.session.get('forgot_pw_email', '')

            if not user_id or not saved_email:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Session expired. Please start over.'})
                error = 'Session expired. Please start over.'
                step = 1
            else:
                try:
                    from .mfa_utils import generate_email_otp, send_otp_email
                    sys_set = SystemSetting.get_settings()
                    otp = generate_email_otp()
                    request.session['forgot_pw_code'] = otp
                    request.session['forgot_pw_code_created'] = timezone.now().isoformat()
                    send_otp_email(saved_email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo,
                                   extra_subject=f'{sys_set.system_name} - Password Reset Code',
                                   extra_html=_password_reset_email_html(saved_email, otp, sys_set.system_name, sys_set.system_logo))
                    if is_ajax:
                        return JsonResponse({'ok': True})
                    success = 'A new code has been sent to your email.'
                    step = 2
                    parts = saved_email.split('@')
                    masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'
                except Exception as e:
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Failed to send code. Please try again.'})
                    error = 'Failed to send code. Please try again.'
                    step = 2
                    parts = saved_email.split('@')
                    masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'

        elif post_step == 'verify_code':
            identifier = request.POST.get('identifier', '').strip()
            code = request.POST.get('reset_code', '').strip()

            user_id = request.session.get('forgot_pw_user_id')
            expected_code = request.session.get('forgot_pw_code')
            saved_email = request.session.get('forgot_pw_email', '')

            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

            if not user_id or not expected_code:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Session expired. Please start over.'})
                error = 'Session expired. Please start over.'
                step = 1
            else:
                from datetime import timedelta
                code_created = request.session.get('forgot_pw_code_created')
                otp_expired = False
                if code_created:
                    try:
                        created_dt = timezone.datetime.fromisoformat(code_created)
                        if timezone.is_naive(created_dt):
                            created_dt = timezone.make_aware(created_dt)
                        otp_expired = (timezone.now() - created_dt) > timedelta(minutes=10)
                    except Exception:
                        otp_expired = True
                if otp_expired:
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Reset code has expired. Please request a new code.'})
                    error = 'Reset code has expired. Please request a new code.'
                    step = 2
                    parts = saved_email.split('@')
                    masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'
                    request.session.pop('forgot_pw_code', None)
                    request.session.pop('forgot_pw_code_created', None)
                elif code != expected_code:
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Invalid reset code. Please try again.'})
                    error = 'Invalid reset code. Please try again.'
                    step = 2
                    parts = saved_email.split('@')
                    masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'
                else:
                    request.session['forgot_pw_code_verified'] = True
                    if is_ajax:
                        return JsonResponse({'ok': True})
                    code_verified = True
                    success = 'Code verified! Enter your new password below.'
                    step = 2
                    parts = saved_email.split('@')
                    masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'

        elif post_step == '2':
            identifier = request.POST.get('identifier', '').strip()
            new_password = request.POST.get('new_password', '')
            confirm_password = request.POST.get('confirm_password', '')

            user_id = request.session.get('forgot_pw_user_id')
            saved_email = request.session.get('forgot_pw_email', '')
            code_verified = request.session.get('forgot_pw_code_verified', False)

            step = 2
            parts = saved_email.split('@')
            masked_email = parts[0][:2] + '***@' + parts[1] if len(parts) == 2 else '***@***'

            if not user_id:
                error = 'Session expired. Please start over.'
                step = 1
            elif not code_verified:
                error = 'Please verify your reset code first.'
            elif new_password != confirm_password:
                error = 'Passwords do not match.'
            else:
                pw_err = _validate_password(new_password)
                if pw_err:
                    error = pw_err

            if not error:
                try:
                    user = User.objects.get(pk=user_id)
                    user.set_password(new_password)
                    user.save()
                    profile = user.profile
                    if profile.must_change_password:
                        profile.must_change_password = False
                        profile.save(update_fields=['must_change_password'])
                    request.session.pop('forgot_pw_user_id', None)
                    request.session.pop('forgot_pw_code', None)
                    request.session.pop('forgot_pw_email', None)
                    request.session.pop('forgot_pw_code_verified', None)
                    step = 3
                except (User.DoesNotExist, AttributeError):
                    error = 'User not found.'
                    step = 1

        elif post_step == '3':
            return redirect('login')

    elif request.method == 'GET' and request.GET.get('reset') == '1':
        step = 3

    return render(request, "forgot_password.html", {
        "error": error,
        "success": success,
        "step": step,
        "identifier": identifier,
        "masked_email": masked_email,
        "code_verified": request.session.get('forgot_pw_code_verified', False) if step == 2 else False,
    })


def _password_reset_email_html(email, otp_code, system_name, system_logo):
    from .mfa_utils import _logo_data_uri
    logo_data = _logo_data_uri(system_logo)
    logo_html = ''
    if logo_data:
        logo_html = f'<img src="{logo_data}" alt="{system_name}" style="max-width:120px; height:auto; margin-bottom:16px; display:block; margin-left:auto; margin-right:auto;">'
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f4f6f8; font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8; padding:40px 0;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px; text-align:center; background:#1a3a2e;">
{logo_html}
<h1 style="color:#fff; font-size:20px; font-weight:600; margin:0;">{system_name}</h1>
</td></tr>
<tr><td style="padding:32px 40px;">
<p style="color:#374151; font-size:15px; margin:0 0 8px;">Your password reset code:</p>
<div style="background:#eff6ff; border:1px solid #93c5fd; border-radius:10px; padding:18px; text-align:center; margin:16px 0;">
<span style="font-size:38px; font-weight:700; letter-spacing:8px; color:#2563eb;">{otp_code}</span>
</div>
<p style="color:#6b7280; font-size:13px; margin:0;">This code expires in <strong>5 minutes</strong>. If you did not request a password reset, you can safely ignore this email.</p>
</td></tr>
<tr><td style="padding:16px 40px; background:#f9fafb; text-align:center; border-top:1px solid #e5e7eb;">
<p style="color:#9ca3af; font-size:11px; margin:0;">&copy; {system_name}</p>
</td></tr>
</table></td></tr></table>
</body></html>'''
