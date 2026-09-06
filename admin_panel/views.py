import csv
import hashlib
import io
import json
import random
from django.db import transaction
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

PH_TZ_OFFSET = timedelta(hours=8)

def to_ph_time(dt):
    """Convert datetime to Philippines time (UTC+8)."""
    if dt is None:
        return None
    from django.utils import timezone as tz
    ph_tz = tz.get_fixed_timezone(480)
    return tz.localtime(dt, ph_tz)
from django.core.files.base import ContentFile
from django.db.models import Sum, Count, Q, Prefetch
from django.db import connection
from django.http import JsonResponse, HttpResponse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from io import BytesIO
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
import re

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai, RadaiSetting
from .models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup
from admin_panel.fields import decrypt_value


def _validate_password(password):
    """Validate password meets security requirements.
    Returns error message string if invalid, None if valid.
    Requirements: min 8 chars, uppercase, lowercase, digit, special char."""
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter."
    if not re.search(r'[a-z]', password):
        return "Password must contain at least one lowercase letter."
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number."
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\;\'`~]', password):
        return "Password must contain at least one special character (!@#$%^&* etc.)."
    return None


def _get_tax_rates(rate_str):
    vat_rate = Decimal('0.00')
    nv_rate = Decimal('0.00')
    s = str(rate_str or '').strip().replace('%', '')
    if not s:
        return vat_rate, nv_rate
    if '/' in s:
        parts = s.split('/')
        if len(parts) == 2:
            try:
                vat_rate = Decimal(parts[0].strip()) / Decimal('100')
            except (InvalidOperation, ValueError):
                pass
            try:
                nv_rate = Decimal(parts[1].strip()) / Decimal('100')
            except (InvalidOperation, ValueError):
                pass
    else:
        try:
            val = Decimal(s) / Decimal('100')
            vat_rate = val
            nv_rate = val
        except (InvalidOperation, ValueError):
            pass
    return vat_rate, nv_rate

def compute_bucket_taxes(net_amount, pro_rate=Decimal('0')):
    """
    Given the entered amount (treated as NET), derive GROSS for VAT so that:
      net = gross - (tax_5 + tax_2)
      where tax_5 and tax_2 are dynamically resolved based on database defaults.
    """
    net_amount = Decimal(str(net_amount or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    pro_rate = Decimal(pro_rate or '0')

    try:
        active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
        t1 = active_taxes[0] if len(active_taxes) > 0 else None
        t2 = active_taxes[1] if len(active_taxes) > 1 else None
        t1_rate = t1.uacs if t1 else '5%/3%'
        t2_rate = t2.uacs if t2 else '2%/1%'
    except Exception:
        t1_rate = '5%/3%'
        t2_rate = '2%/1%'

    t1_vat, t1_nv = _get_tax_rates(t1_rate)
    t2_vat, t2_nv = _get_tax_rates(t2_rate)

    denom = Decimal('1.12') - (t1_vat + t2_vat + pro_rate)
    if denom <= 0:
        denom = Decimal('1.12')

    gross = (net_amount * Decimal('1.12') / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    base = (gross / Decimal('1.12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    tax_5 = (base * t1_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tax_2 = (base * t2_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    professional_tax = (net_amount * pro_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'gross': gross,
        'tax_5': tax_5,
        'tax_2': tax_2,
        'professional_tax': professional_tax,
        'total_tax': (tax_5 + tax_2 + professional_tax).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
    }

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip or '127.0.0.1'


def _get_client_device_and_os(request):
    import os
    import getpass
    import platform
    import socket

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
    import urllib.request
    import json

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
    from cashier.models import UserDevice
    blocked_devices = UserDevice.objects.filter(is_blocked=True)
    for dev in blocked_devices:
        # 1. Matching IP AND User Agent
        if dev.ip_address == ip and dev.user_agent == user_agent:
            return True
        # 2. Matching computer name AND windows username (hardware signature)
        if (dev.device_name == device_name and dev.windows_username == windows_username and 
            device_name not in ('Remote PC', '', 'Unknown Device') and 
            windows_username not in ('Network Client', '', 'Unknown')):
            return True
        # 3. Match on IP and computer name
        if dev.ip_address == ip and dev.device_name == device_name and device_name not in ('Remote PC', '', 'Unknown Device'):
            return True
    return False


def _is_session_active(profile):
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    from admin_panel.models import SystemSetting

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

    # Get the auto-logout threshold for this user (or system default)
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
            from cashier.models import UserDevice
            UserDevice.objects.filter(session_key=request.session.session_key).update(is_terminated=True)
            if profile.active_session_key == request.session.session_key:
                profile.active_session_key = ''
                profile.save(update_fields=['active_session_key'])
        except Exception:
            pass
    logout(request)
    return render(request, "logout_loading.html")


def _register_session(request, user):
    try:
        from django.contrib.sessions.models import Session
        from cashier.models import UserDevice
        from django.utils import timezone

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
                # Check if this physical device is blocked
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
                            # If user has MFA enabled, proceed to MFA flow
                            if profile.totp_enabled or profile.email_otp_enabled:
                                request.session["mfa_user_id"] = cp_user.pk
                                request.session["mfa_after_change_pw"] = True
                                mfa_pick = True
                            else:
                                cp_user.backend = 'django.contrib.auth.backends.ModelBackend'
                                login(request, cp_user)
                                _register_session(request, cp_user)
                                return _mfa_redirect(cp_user)

        # Keep showing change-pw form if the session is still valid
        if request.session.get("change_pw_user_id"):
            change_pw = True

    # ── Step 2: Pick MFA method ──────────────────────────────────────
    if request.method == "POST" and request.POST.get("pick_mfa"):
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
                from .models import SystemSetting
                from django.utils import timezone as _tz
                sys_set = SystemSetting.get_settings()
                otp = generate_email_otp()
                request.session["email_otp_code"] = otp
                request.session["email_otp_created"] = _tz.now().isoformat()
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
                # Check if this physical device is blocked
                ip = _get_client_ip(request)
                dev_info = _get_client_device_and_os(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
                    error = "This device has been blocked by the system administrator and cannot be used to log in."
                    mfa_step = True
                    mfa_method = request.session.get("mfa_method", "totp")
                else:
                    from .mfa_utils import verify_totp
                method = request.session.get("mfa_method", "totp")
                use_recovery = request.POST.get("use_recovery")
                code = request.POST.get("mfa_code", "").strip()
                recovery_code = request.POST.get("recovery_code", "").strip()

                # If recovery code is provided, use it directly
                if use_recovery and recovery_code:
                    from .mfa_utils import verify_recovery_code
                    recovery_codes = profile.mfa_recovery_codes or []
                    idx = verify_recovery_code(recovery_code, recovery_codes) if recovery_codes else -1
                    if idx >= 0:
                        recovery_codes.pop(idx)
                        profile.mfa_recovery_codes = recovery_codes
                        profile.save(update_fields=["mfa_recovery_codes"])
                        if _is_session_active(profile):
                            from django.utils import timezone
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
                            success_msg = f"Logged in with recovery code. {len(recovery_codes)} recovery codes remaining."
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
                            from django.utils import timezone
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
                        from .mfa_utils import verify_recovery_code
                        recovery_codes = profile.mfa_recovery_codes or []
                        idx = verify_recovery_code(code, recovery_codes) if recovery_codes else -1
                        if idx >= 0:
                            recovery_codes.pop(idx)
                            profile.mfa_recovery_codes = recovery_codes
                            profile.save(update_fields=["mfa_recovery_codes"])
                            if _is_session_active(profile):
                                from django.utils import timezone
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
                                success_msg = f"Logged in with recovery code. {len(recovery_codes)} recovery codes remaining."
                                if next_url and not after_change_pw:
                                    return redirect(next_url)
                                return _mfa_redirect(mfa_user)
                        else:
                            error = "Invalid authenticator code."
                            mfa_step = True
                            mfa_method = method
                            # Track failed MFA attempts
                            mfa_attempts = request.session.get("mfa_failed_attempts", 0) + 1
                            request.session["mfa_failed_attempts"] = mfa_attempts
                            # On 3rd failed attempt, auto-send recovery code to email
                            if mfa_attempts >= 3 and mfa_user.email:
                                from .mfa_utils import generate_recovery_codes, hash_recovery_code
                                from django.core.mail import send_mail
                                from django.conf import settings
                                # Generate a single recovery code
                                single_code = generate_recovery_codes(count=1)[0]
                                # Add it to the user's recovery codes
                                recovery_codes = profile.mfa_recovery_codes or []
                                recovery_codes.append(hash_recovery_code(single_code))
                                profile.mfa_recovery_codes = recovery_codes
                                profile.save(update_fields=["mfa_recovery_codes"])
                                # Send the code to user's email
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
                    from django.utils import timezone as _tz
                    from datetime import timedelta
                    expected = request.session.get("email_otp_code")
                    otp_created = request.session.get("email_otp_created")
                    otp_expired = False
                    if otp_created:
                        try:
                            created_dt = _tz.datetime.fromisoformat(otp_created)
                            if _tz.is_naive(created_dt):
                                created_dt = _tz.make_aware(created_dt)
                            otp_expired = (_tz.now() - created_dt) > timedelta(minutes=10)
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
                            from django.utils import timezone
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
            # Check if this physical device is blocked
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
                    from django.utils import timezone
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
                        if next_url:
                            return redirect(next_url)
                        return _mfa_redirect(user)
        else:
            error = "Invalid username or password"

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
        except User.DoesNotExist:
            pass

    kicked = request.GET.get('kicked') == '1'
    return render(request, "login.html", {
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
    })


def _mfa_redirect(user):
    try:
        role = user.profile.role
    except AttributeError:
        role = "cashier"
    if role == "admin":
        return redirect(reverse("admin_dashboard") + "?welcome=1")
    return redirect(reverse("cashier_dashboard") + "?welcome=1")


@csrf_exempt
def check_user_exists(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    username = request.POST.get("username", "").strip()
    if not username:
        return JsonResponse({"exists": False})

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


def forgot_password(request):
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
                        from .models import SystemSetting
                        from django.utils import timezone as _tz
                        sys_set = SystemSetting.get_settings()
                        otp = generate_email_otp()
                        request.session['forgot_pw_user_id'] = user.pk
                        request.session['forgot_pw_code'] = otp
                        request.session['forgot_pw_code_created'] = _tz.now().isoformat()
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
                            error = f'Failed to send reset code. Please try again.'

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
                    from .models import SystemSetting
                    from django.utils import timezone as _tz
                    sys_set = SystemSetting.get_settings()
                    otp = generate_email_otp()
                    request.session['forgot_pw_code'] = otp
                    request.session['forgot_pw_code_created'] = _tz.now().isoformat()
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
                from django.utils import timezone as _tz
                from datetime import timedelta
                code_created = request.session.get('forgot_pw_code_created')
                otp_expired = False
                if code_created:
                    try:
                        created_dt = _tz.datetime.fromisoformat(code_created)
                        if _tz.is_naive(created_dt):
                            created_dt = _tz.make_aware(created_dt)
                        otp_expired = (_tz.now() - created_dt) > timedelta(minutes=10)
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


@login_required
def email_settings(request):
    if not _is_admin(request.user):
        return redirect("access_denied")

    error = None
    success = None
    sys_set = SystemSetting.get_settings()

    if request.method == 'POST':
        # Always save settings first (whether save or test)
        try:
            sys_set.email_host = request.POST.get('email_host', 'smtp.gmail.com').strip()
            try:
                sys_set.email_port = int(request.POST.get('email_port', 587))
            except (TypeError, ValueError):
                sys_set.email_port = 587
            sys_set.email_use_tls = request.POST.get('email_use_tls', '1') == '1'
            sys_set.email_host_user = request.POST.get('email_host_user', '').strip()
            pw = request.POST.get('email_host_password', '')
            if pw:
                sys_set.email_host_password = pw
            sys_set.default_from_email = request.POST.get('default_from_email', '').strip()
            sys_set.save()
        except Exception as e:
            error = f'Failed to save settings: {str(e)}'

        if not error and request.POST.get('test_email'):
            try:
                test_recipient = sys_set.email_host_user
                if not test_recipient:
                    error = 'No email address configured. Set one above first.'
                elif not sys_set.email_host or not sys_set.email_host_password:
                    error = 'SMTP host and password are required to send email.'
                else:
                    from django.core.mail import EmailMessage
                    from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
                    from_email = sys_set.default_from_email or sys_set.email_host_user
                    backend = SmtpBackend(
                        host=sys_set.email_host,
                        port=sys_set.email_port,
                        use_tls=sys_set.email_use_tls,
                        username=sys_set.email_host_user,
                        password=sys_set.email_host_password,
                    )
                    msg = EmailMessage(
                        subject=f'{sys_set.system_name or "System"} - Test Email',
                        body='This is a test email from your system.\n\nIf you received this, your SMTP settings are working correctly.',
                        from_email=from_email,
                        to=[test_recipient],
                        connection=backend,
                    )
                    msg.send()
                    backend.close()
                    success = f'Test email sent successfully to {test_recipient}.'
            except Exception as e:
                error = f'Failed to send test email: {str(e)}'
        elif not error:
            success = 'Email settings saved successfully.'

    context = _page_context(request, is_admin=True, page_title="Email Settings", extra={
        "email_host": sys_set.email_host,
        "email_port": sys_set.email_port,
        "email_use_tls": sys_set.email_use_tls,
        "email_host_user": sys_set.email_host_user,
        "email_host_password": sys_set.email_host_password,
        "default_from_email": sys_set.default_from_email,
        "success": success,
        "error": error,
    })
    return render(request, "admin_panel/email_settings.html", context)


@login_required
def chatbot_setup(request):
    if not _is_admin(request.user):
        return redirect("access_denied")

    from .models import ChatbotConfig, ChatMessage

    config = ChatbotConfig.get_config()
    error = None
    success = None
    test_result = None
    test_success = False

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'save':
            config.name = (request.POST.get('name') or config.name or 'AI Assistant').strip()
            api_key_val = (request.POST.get('api_key') or '').strip()
            if api_key_val:
                config.api_key = api_key_val
            new_provider = (request.POST.get('provider') or config.provider or 'openai').strip()
            if new_provider != config.provider:
                config.model_name = ''
            config.provider = new_provider
            config.base_url = (request.POST.get('base_url') or '').strip()
            saved_model = (request.POST.get('model_name') or '').strip()
            if saved_model:
                config.model_name = saved_model
            config.is_active = request.POST.get('is_active') == 'on'
            config.save()
            success = 'Chatbot settings saved successfully.'

        elif action == 'clear_history':
            ChatMessage.objects.all().delete()
            success = 'All chat history has been cleared.'

        elif action == 'test':
            try:
                test_api_key = (request.POST.get('api_key') or '').strip()
                test_base_url = (request.POST.get('base_url') or '').strip()
                test_model = (request.POST.get('model_name') or '').strip()
                test_provider = (request.POST.get('provider') or config.provider or '').strip()

                if not test_api_key:
                    test_result = "API key is not configured. Please enter an API key first."
                    test_success = False
                else:
                    import types
                    from django.utils import timezone as tz
                    now = tz.now()
                    today = now.date()
                    current_time = now.strftime('%I:%M %p')
                    current_date = today.strftime('%A, %B %d, %Y')
                    time_greeting = 'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'
                    test_sp = config.system_prompt or FINANCIAL_SYSTEM_PROMPT
                    test_sp += f"\n\nREAL-TIME CONTEXT:\n- Current date: {current_date}\n- Current time: {current_time}\n- Greeting: Good {time_greeting}\n- Timezone: {tz.get_current_timezone_name()}"
                    test_config = types.SimpleNamespace(
                        api_key=test_api_key,
                        base_url=test_base_url,
                        model_name=test_model,
                        provider=test_provider,
                        system_prompt=test_sp,
                    )
                    test_msg = (request.POST.get('test_message') or '').strip() or 'Say hello in one sentence.'
                    test_messages = [
                        {"role": "system", "content": test_config.system_prompt},
                        {"role": "user", "content": test_msg},
                    ]
                    test_result = _call_ai_provider(test_config, test_messages)
                    test_success = not any(test_result.startswith(p) for p in ["Error", "API key", "Could not reach", "AI service returned", "Something went wrong"])
            except Exception as e:
                test_result = f"Unexpected error: {str(e)[:300]}"
                test_success = False

    context = _page_context(request, is_admin=True, page_title="Chatbot Setup", extra={
        "config": config,
        "success": success,
        "error": error,
        "test_result": test_result,
        "test_success": test_success,
    })
    return render(request, "admin_panel/chatbot_setup.html", context)


@login_required
def chatbot_config_api(request):
    from .models import ChatbotConfig
    from .models import SystemSetting
    config = ChatbotConfig.get_config()

    if not config.is_active:
        return JsonResponse({"active": False})

    sys_name = SystemSetting.get_settings().system_name or 'Registry'

    return JsonResponse({
        "active": True,
        "name": config.name,
        "welcome_message": f"Welcome to {sys_name}. {config.welcome_message or 'How can I help you today?'}",
        "system_name": sys_name,
    })


@login_required
@csrf_exempt
def chatbot_message_api(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    from .models import ChatbotConfig, ChatMessage
    import json

    config = ChatbotConfig.get_config()
    if not config.is_active:
        return JsonResponse({"error": "Chatbot is not active"}, status=403)

    try:
        body = json.loads(request.body.decode())
        user_message = body.get('message', '').strip()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request body"}, status=400)

    if not user_message:
        return JsonResponse({"error": "Message cannot be empty"}, status=400)

    ChatMessage.objects.create(user=request.user, role='user', content=user_message)

    greetings = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening', 'welcome', 'start', 'help']
    is_greeting = user_message.strip().lower().rstrip('!.') in greetings or user_message.strip().lower().rstrip('!.').startswith(('hi ', 'hello ', 'hey '))

    if is_greeting:
        from .models import SystemSetting
        sys_name = SystemSetting.get_settings().system_name or 'Registry'
        greeting_reply = f"Welcome to {sys_name}. {config.welcome_message or 'How can I help you today?'} You can ask me about cheques, fund clusters, account titles, suppliers, RADAI entries, or financial reports."
        ChatMessage.objects.create(user=request.user, role='assistant', content=greeting_reply)
        return JsonResponse({"ok": True, "reply": greeting_reply})

    try:
        max_msgs = min(config.max_history, 8)
        history = list(ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:max_msgs])
        history.reverse()

        live_data = _fetch_live_financial_data(user_message)
        if live_data and len(live_data) > 2000:
            live_data = live_data[:2000] + "\n... (truncated)"

        system_prompt = config.system_prompt or FINANCIAL_SYSTEM_PROMPT

        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        today = now.date()
        current_time = now.strftime('%I:%M %p')
        current_date = today.strftime('%A, %B %d, %Y')
        time_greeting = 'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'
        system_prompt += f"\n\nREAL-TIME CONTEXT (always use this — never guess the date or time):\n- Current date: {current_date}\n- Current time: {current_time}\n- Greeting: Good {time_greeting}\n- Timezone: {timezone.get_current_timezone_name()}\n- If the user asks for the time, date, day, or greeting, answer using THIS exact information. If the user asks about deadlines, durations, or relative dates (e.g. 'yesterday', 'next week'), calculate from this date/time."
        if live_data:
            system_prompt += "\n\nLIVE DATA FROM DATABASE (USE ONLY THIS DATA — DO NOT INVENT ANYTHING):\n" + live_data
        else:
            system_prompt += "\n\nLIVE DATA FROM DATABASE: No records found in the database."

        if len(system_prompt) > 4000:
            system_prompt = system_prompt[:4000]

        messages_payload = [{"role": "system", "content": system_prompt}]
        for msg in history:
            content = msg.content[:300] if len(msg.content) > 300 else msg.content
            messages_payload.append({"role": msg.role, "content": content})

        ai_reply = _call_ai_provider(config, messages_payload)

        if ai_reply:
            ChatMessage.objects.create(user=request.user, role='assistant', content=ai_reply)
            return JsonResponse({"ok": True, "reply": ai_reply})
        else:
            return JsonResponse({"error": "Failed to get response from AI provider"}, status=502)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": f"Something went wrong: {str(e)[:200]}"}, status=500)


def _fetch_live_financial_data(message):
    from django.utils import timezone
    from django.db.models import Sum
    from datetime import timedelta

    msg = message.lower()
    today = timezone.now().date()

    keywords = [
        'report', 'summary', 'total', 'balance', 'how much', 'cheque', 'radai',
        'fund', 'supplier', 'account', 'this month', 'this week', 'this year',
        'last month', 'monthly', 'weekly', 'annual', 'financial', 'data',
        'record', 'transaction', 'payment', 'amount', 'list', 'show', 'what is',
        'how many', 'overview', 'status', 'pending', 'released', 'voided',
    ]
    if not any(k in msg for k in keywords):
        return ""

    from cashier.models import Cheque, FundCluster, Supplier, Radai

    sections = []
    sections.append(f"Date: {today.strftime('%B %d, %Y')} ({today.strftime('%A')})")

    fund_clusters = FundCluster.objects.filter(is_active=True)

    today_str = today.strftime('%Y-%m-%d')
    if any(k in msg for k in ['week', 'weekly']):
        period_start = today - timedelta(days=today.weekday())
        period_end = period_start + timedelta(days=6)
        period_label = f"Week: {period_start.strftime('%B %d')} - {period_end.strftime('%B %d, %Y')}"
    elif any(k in msg for k in ['last month']):
        month_start = today.replace(day=1)
        last_month_end = month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        period_start, period_end = last_month_start, last_month_end
        period_label = f"Last Month: {period_start.strftime('%B %Y')}"
    elif any(k in msg for k in ['year', 'annual', 'annually', 'this year']):
        period_start = today.replace(month=1, day=1)
        period_end = today.replace(month=12, day=31)
        period_label = f"Year: {today.year}"
    else:
        period_start = today.replace(day=1)
        if today.month == 12:
            period_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            period_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        period_label = f"Month: {today.strftime('%B %Y')}"

    cheque_qs = Cheque.objects.filter(date__gte=period_start, date__lte=period_end).select_related('payee', 'fund_cluster')
    cheque_total = cheque_qs.aggregate(total=Sum('amount'))['total'] or 0
    cheque_count = cheque_qs.count()

    cheque_by_status = {}
    for s in ['draft', 'pending', 'released', 'voided', 'stale']:
        c = cheque_qs.filter(status=s).count()
        if c:
            cheque_by_status[s] = c

    cheque_by_fund = {}
    for fc in fund_clusters:
        fc_total = cheque_qs.filter(fund_cluster=fc).aggregate(total=Sum('amount'))['total'] or 0
        fc_count = cheque_qs.filter(fund_cluster=fc).count()
        if fc_count:
            cheque_by_fund[fc.code] = {'total': fc_total, 'count': fc_count}

    cheque_lines = [f"Period: {period_label}", f"Total Cheques: {cheque_count}", f"Total Amount: ₱{cheque_total:,.2f}"]
    if cheque_by_status:
        cheque_lines.append("Status: " + ", ".join([f"{s.title()}: {c}" for s, c in cheque_by_status.items()]))
    if cheque_by_fund:
        for code, data in cheque_by_fund.items():
            cheque_lines.append(f"  Fund {code}: ₱{data['total']:,.2f} ({data['count']} cheques)")
    if cheque_count == 0:
        cheque_lines.append("No cheque records found for this period.")
    sections.append("CHEQUES:\n" + "\n".join(cheque_lines))

    recent_cheques = cheque_qs.order_by('-date', '-id')[:10]
    if recent_cheques:
        rows = []
        for i, c in enumerate(recent_cheques, 1):
            payee = c.payee_name or (c.payee.account_name if c.payee else 'N/A')
            fc = c.fund_cluster.code if c.fund_cluster else 'N/A'
            rows.append(f"  {i}. Cheque #{c.cheque_number or 'N/A'} | Payee: {payee} | ₱{c.amount:,.2f} | Fund: {fc} | Status: {c.status.title()} | Date: {c.date.strftime('%b %d, %Y')}")
        sections.append("CHEQUE RECORDS:\n" + "\n".join(rows))

    radai_qs = Radai.objects.filter(date__gte=period_start, date__lte=period_end).select_related('fund_cluster')
    radai_total = radai_qs.aggregate(total=Sum('amount'))['total'] or 0
    radai_count = radai_qs.count()

    radai_by_fund = {}
    for fc in fund_clusters:
        fc_total = radai_qs.filter(fund_cluster=fc).aggregate(total=Sum('amount'))['total'] or 0
        fc_count = radai_qs.filter(fund_cluster=fc).count()
        if fc_count:
            radai_by_fund[fc.code] = {'total': fc_total, 'count': fc_count}

    radai_lines = [f"Total RADAI: {radai_count}", f"Total Amount: ₱{radai_total:,.2f}"]
    if radai_by_fund:
        for code, data in radai_by_fund.items():
            radai_lines.append(f"  Fund {code}: ₱{data['total']:,.2f} ({data['count']} entries)")
    if radai_count == 0:
        radai_lines.append("No RADAI records found for this period.")
    sections.append("RADAI:\n" + "\n".join(radai_lines))

    recent_radai = radai_qs.order_by('-date', '-id')[:10]
    if recent_radai:
        rows = []
        for i, r in enumerate(recent_radai, 1):
            fc = r.fund_cluster.code if r.fund_cluster else 'N/A'
            rows.append(f"  {i}. {r.account_name} | ₱{r.amount:,.2f} | Fund: {fc} | ORS/BURS: {r.ors_burs_no or 'N/A'} | Status: {r.status.title()} | Date: {r.date.strftime('%b %d, %Y') if r.date else 'N/A'}")
        sections.append("RADAI RECORDS:\n" + "\n".join(rows))

    supplier_active = Supplier.objects.filter(status='active').count()
    supplier_inactive = Supplier.objects.filter(status='inactive').count()
    supplier_blacklisted = Supplier.objects.filter(status='blacklisted').count()
    total_suppliers = supplier_active + supplier_inactive + supplier_blacklisted
    if total_suppliers == 0:
        sections.append("SUPPLIERS: No supplier records found.")
    else:
        sections.append(f"SUPPLIERS: Active: {supplier_active} | Inactive: {supplier_inactive} | Blacklisted: {supplier_blacklisted}")

    grand_total = cheque_total + radai_total
    sections.append(f"GRAND TOTAL (Cheques + RADAI): ₱{grand_total:,.2f}")

    return "\n\n".join(sections)


FINANCIAL_SYSTEM_PROMPT = """You are the AI Assistant for this financial registry system. You help users manage 6 modules and answer questions using LIVE DATABASE DATA when provided.

CRITICAL RULES — YOU MUST FOLLOW THESE:
- ONLY use the numbers and records shown in "LIVE DATA" below.
- If the LIVE DATA shows "No records found" or is empty, your answer MUST be "No records found for this period."
- NEVER generate, estimate, guess, or make up ANY numbers. EVER.
- If you don't have the data, say you don't have it. Period.
- Respond in plain conversational sentences. Do NOT use markdown formatting like ** or ## or tables with pipes. Just talk naturally like a real assistant.
- You have REAL-TIME access to the current date and time (provided in the REAL-TIME CONTEXT section). Use it to answer questions about time, dates, deadlines, greetings, and relative references like "today", "yesterday", "this week", etc.

MODULES:
1. CHEQUES — cheque_number, payee, fund_cluster, amount, date, purpose, status (draft/pending/released/voided/stale)
2. FUND CLUSTERS — code (RA/OA/CO), name, balance, is_active
3. ACCOUNT TITLES — name, uacs code, with sub-options
4. SUPPLIERS — account_name, amount, tin, status (active/inactive/blacklisted)
5. RADAI — account_name, ors_burs_no, fund_cluster, amount, status
6. REPORTS — cheque_summary, radai_summary, weekly/monthly/annual variants

ROLES: Admin=full. Cashier=cheques(R/w/print), suppliers(R), radai(CRUD), reports(R/generate). Guest=reports only.

HOW TO RESPOND:
- Talk like a real person, not a robot. Use plain sentences.
- Example: "Based on the data, here is the summary for January. There are no cheque records found for this period. There are also no RADAI records found. The grand total is ₱0.00."
- If there ARE records, list them naturally: "I found 5 cheques this month totaling ₱45,000. The breakdown by fund cluster is: Fund RA has 3 cheques worth ₱25,000, and Fund OA has 2 cheques worth ₱20,000."
- Use ₱ for peso amounts.
- Never use markdown, asterisks, pipes, or table formatting. Just sentences.
"""


def _call_ai_provider(config, messages):
    import urllib.request
    import json

    api_key = config.api_key
    if not api_key:
        return "API key is not configured. Please ask the admin to set up the chatbot."

    try:
        base_url = (config.base_url or '').strip().rstrip('/')
        if not base_url:
            base_url = 'https://api.openai.com/v1'
        if not base_url.startswith('http'):
            base_url = 'https://' + base_url
        if not base_url.endswith('/chat/completions'):
            base_url = base_url + '/chat/completions'

        DEFAULT_MODELS = {
            'api.openai.com': 'gpt-4o-mini',
            'api.groq.com': 'llama-3.1-8b-instant',
            'api.deepseek.com': 'deepseek-chat',
            'api.mistral.ai': 'mistral-small-latest',
            'api.x.ai': 'grok-2',
            'openrouter.ai': 'meta-llama/llama-3.1-8b-instant:free',
            'api.together.xyz': 'meta-llama/Llama-3.1-8B-Instruct-Turbo',
            'api-inference.huggingface.co': 'meta-llama/Llama-3.1-8B-Instruct',
            'generativelanguage.googleapis.com': 'gemini-2.0-flash',
            'localhost:11434': 'llama3.1',
        }

        model = (config.model_name or '').strip()
        provider = (config.provider or '').lower()

        PROVIDER_MODELS = {
            'openai': 'gpt-4o-mini',
            'groq': 'llama-3.1-8b-instant',
            'deepseek': 'deepseek-chat',
            'mistral': 'mistral-small-latest',
            'x': 'grok-2',
            'openrouter': 'meta-llama/llama-3.1-8b-instant:free',
            'together': 'meta-llama/Llama-3.1-8B-Instruct-Turbo',
            'huggingface': 'meta-llama/Llama-3.1-8B-Instruct',
            'google': 'gemini-2.0-flash',
            'ollama': 'llama3.1',
        }

        if provider and provider != 'custom' and provider in PROVIDER_MODELS:
            model = PROVIDER_MODELS[provider]
        elif not model:
            for domain, default_model in DEFAULT_MODELS.items():
                if domain in base_url:
                    model = default_model
                    break

        if not model:
            model = 'gpt-4o-mini'

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result['choices'][0]['message']['content'].strip()

    except urllib.error.URLError as e:
        return f"Could not reach the AI service. Please check your internet connection or API settings."
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode()[:200]
        except Exception:
            err_body = str(e.code)
        return f"AI service returned an error (HTTP {e.code}): {err_body}"
    except Exception as e:
        return f"Error connecting to AI service: {str(e)[:300]}"


@login_required
def chatbot_clear_history(request):
    if request.method != 'POST':
        return redirect('chatbot_setup')
    from .models import ChatMessage
    ChatMessage.objects.filter(user=request.user).delete()
    return JsonResponse({"ok": True})


@login_required
def chatbot_history_api(request):
    from .models import ChatMessage
    messages = list(ChatMessage.objects.filter(user=request.user).order_by('created_at').values('role', 'content'))
    return JsonResponse({"messages": messages})


@login_required
def admin_user_manual(request):
    if not _is_admin(request.user):
        return redirect("access_denied")
    context = _page_context(request, is_admin=True, page_title="Admin User Manual")
    return render(request, "admin_panel/admin_user_manual.html", context)


@login_required
def cashier_user_manual(request):
    context = _page_context(request, is_admin=False, page_title="Cashier User Manual")
    return render(request, "cashier/cashier_user_manual.html", context)


def access_denied(request):
    return render(request, "access_denied.html")


@require_GET
def session_check(request):
    """
    Lightweight polling endpoint.  Returns JSON:
      { "ok": true }   - session is still valid for this user
      { "kicked": true }  - this session has been superseded or terminated; client should redirect to login
    Used by the JS heartbeat in base.html to detect concurrent login on another device.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"kicked": True}, status=200)

    try:
        profile = request.user.profile
        active_key = profile.active_session_key or ''
        current_key = request.session.session_key or ''

        # Update last activity dynamically (rate-limited to every 10s)
        from django.utils import timezone
        now = timezone.now()
        if not profile.last_activity or (now - profile.last_activity).total_seconds() > 10:
            profile.last_activity = now
            profile.save(update_fields=['last_activity'])
            
            from cashier.models import UserDevice
            UserDevice.objects.filter(session_key=current_key).update(last_activity=now)

        # Check if this device has been terminated or is blocked
        from cashier.models import UserDevice
        try:
            device = UserDevice.objects.get(session_key=current_key)
            if device.is_terminated or (active_key and current_key and active_key != current_key):
                return JsonResponse({"kicked": True}, status=200)
            if device.is_blocked:
                return JsonResponse({"blocked": True}, status=200)
        except UserDevice.DoesNotExist:
            if active_key and current_key and active_key != current_key:
                return JsonResponse({"kicked": True}, status=200)

        # Check if there is a blocked login attempt on this user's account
        if profile.login_attempt_blocked:
            time_str = profile.blocked_login_time.strftime("%I:%M:%S %p") if profile.blocked_login_time else "recently"
            ip_str = profile.blocked_login_ip or "unknown IP"

            # Clear block indicators
            profile.login_attempt_blocked = False
            profile.blocked_login_time = None
            profile.blocked_login_ip = ''
            profile.save(update_fields=['login_attempt_blocked', 'blocked_login_time', 'blocked_login_ip'])

            return JsonResponse({
                "ok": True,
                "blocked_attempt": {
                    "time": time_str,
                    "ip": ip_str
                }
            }, status=200)
    except Exception:
        pass

    return JsonResponse({"ok": True}, status=200)


@login_required
def update_screen_capture(request):
    """
    Saves the live screen capture base64 JPEG data to the user's UserDevice record.
    """
    if request.method == "POST":
        try:
            import json
            data = json.loads(request.body.decode())
            img_data = data.get('image', '')
            url_data = data.get('url', '')
            if img_data:
                from cashier.models import UserDevice
                current_key = request.session.session_key or ''
                UserDevice.objects.filter(session_key=current_key).update(screen_capture=img_data, current_url=url_data)
                
                # Also update last activity
                from django.utils import timezone
                now = timezone.now()
                profile = request.user.profile
                if not profile.last_activity or (now - profile.last_activity).total_seconds() > 10:
                    profile.last_activity = now
                    profile.save(update_fields=['last_activity'])
                    UserDevice.objects.filter(session_key=current_key).update(last_activity=now)
                return JsonResponse({"status": "success"}, status=200)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"error": "Invalid request"}, status=400)


from django.views.decorators.cache import never_cache

@never_cache
@login_required
def manage_devices(request):
    """
    Renders a live dashboard of active and historic logged-in sessions (admin-only).
    """
    if not _is_admin(request.user):
        return redirect("access_denied")

    from cashier.models import UserDevice
    from django.utils import timezone

    # De-duplicate to show only the latest device session per user (exclude admins)
    devices_qs = UserDevice.objects.select_related('user').order_by('-login_time').exclude(user__is_superuser=True)
    seen_users = set()
    devices = []
    for device in devices_qs:
        if device.user_id not in seen_users:
            seen_users.add(device.user_id)
            devices.append(device)
            
    devices = devices[:30]
    now = timezone.now()

    for device in devices:
        if device.is_terminated:
            device.status_label = "Terminated"
            device.status_class = "terminated"
            device.is_active_online = False
        elif device.is_blocked:
            device.status_label = "Blocked"
            device.status_class = "blocked"
            device.is_active_online = False
        elif device.last_activity and (now - device.last_activity).total_seconds() <= 35:
            device.status_label = "Online"
            device.status_class = "online"
            device.is_active_online = True
        else:
            device.status_label = "Offline"
            device.status_class = "offline"
            device.is_active_online = False

    context = _page_context(request, is_admin=True, page_title="Manage Devices", extra={
        'devices': devices
    })
    return render(request, "admin_panel/manage_devices.html", context)


@login_required
def terminate_session(request, pk):
    """
    Forcefully terminates a user's active session and logs them out (admin-only).
    """
    if not _is_admin(request.user):
        return redirect("access_denied")

    if request.method == "POST":
        from cashier.models import UserDevice
        from django.contrib.sessions.models import Session
        try:
            device = UserDevice.objects.get(pk=pk)
            device.is_terminated = True
            device.save(update_fields=['is_terminated'])

            # Evict from Django Session store
            Session.objects.filter(session_key=device.session_key).delete()

            # Clear active key on Profile
            profile = device.user.profile
            if profile.active_session_key == device.session_key:
                profile.active_session_key = ''
                profile.save(update_fields=['active_session_key'])

            from django.contrib import messages
            messages.success(request, f"Session for {device.user.username} terminated successfully.")
        except UserDevice.DoesNotExist:
            pass

    return redirect("manage_devices")


@login_required
def live_captures(request):
    """
    JSON API returning active devices' status, activity, and live screenshot captures.
    """
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    from cashier.models import UserDevice
    from django.utils import timezone

    # De-duplicate to show only the latest active session per user (exclude admins)
    devices_qs = UserDevice.objects.filter(is_terminated=False).order_by('-login_time').exclude(user__is_superuser=True)
    seen_users = set()
    devices = []
    for d in devices_qs:
        if d.user_id not in seen_users:
            seen_users.add(d.user_id)
            devices.append(d)

    now = timezone.now()
    data = {}

    for d in devices:
        is_online = d.last_activity and (now - d.last_activity).total_seconds() <= 35
        status_label = 'Blocked' if d.is_blocked else ('Online' if is_online else 'Offline')
        status_class = 'blocked' if d.is_blocked else ('online' if is_online else 'offline')
        data[d.pk] = {
            'screen_capture': d.screen_capture or '',
            'is_online': is_online,
            'is_blocked': d.is_blocked,
            'last_activity_str': d.last_activity.strftime("%I:%M:%S %p") if d.last_activity else 'Never',
            'status_label': status_label,
            'status_class': status_class,
            'current_url': d.current_url or '/'
        }

    return JsonResponse({"devices": data}, status=200)


@login_required
def toggle_device_block(request, pk):
    """
    Toggles the is_blocked status of a user device (admin-only).
    """
    if not _is_admin(request.user):
        return redirect("access_denied")

    if request.method == "POST":
        from cashier.models import UserDevice
        try:
            device = UserDevice.objects.get(pk=pk)
            if not device.is_terminated:
                device.is_blocked = not device.is_blocked
                device.save(update_fields=['is_blocked'])

                from django.contrib import messages
                action_str = "blocked" if device.is_blocked else "unblocked"
                messages.success(request, f"Device session for {device.user.username} has been {action_str} successfully.")
        except UserDevice.DoesNotExist:
            pass

    return redirect("manage_devices")


@login_required
def unterminate_session(request, pk):
    """
    Clears the is_terminated status of a user device (admin-only).
    """
    if not _is_admin(request.user):
        return redirect("access_denied")

    if request.method == "POST":
        from cashier.models import UserDevice
        try:
            device = UserDevice.objects.get(pk=pk)
            if device.is_terminated:
                device.is_terminated = False
                device.save(update_fields=['is_terminated'])

                from django.contrib import messages
                messages.success(request, f"Device session for {device.user.username} has been unterminated successfully.")
        except UserDevice.DoesNotExist:
            pass

    return redirect("manage_devices")


@login_required
def delete_device(request, pk):
    """
    Deletes a device session record from the database (admin-only).
    """
    if not _is_admin(request.user):
        return redirect("access_denied")

    if request.method == "POST":
        from cashier.models import UserDevice
        try:
            device = UserDevice.objects.get(pk=pk)
            # If the device is active (not terminated), evict the session first
            if not device.is_terminated:
                from django.contrib.sessions.models import Session
                Session.objects.filter(session_key=device.session_key).delete()
                profile = device.user.profile
                if profile.active_session_key == device.session_key:
                    profile.active_session_key = ''
                    profile.save(update_fields=['active_session_key'])
            
            device.delete()
            from django.contrib import messages
            messages.success(request, f"Device record deleted successfully.")
        except UserDevice.DoesNotExist:
            pass

    return redirect("manage_devices")


@login_required
def send_user_message(request):
    """Send a message between users (admin to user or user reply)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    import json
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    recipient_id = body.get("recipient_id")
    content = (body.get("content") or "").strip()

    if not recipient_id or not content:
        return JsonResponse({"error": "recipient_id and content are required"}, status=400)

    from django.contrib.auth.models import User
    try:
        recipient = User.objects.get(pk=recipient_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    from admin_panel.models import UserMessage
    msg = UserMessage.objects.create(
        sender=request.user,
        recipient=recipient,
        content=content,
    )

    return JsonResponse({
        "ok": True,
        "message": {
            "id": msg.pk,
            "sender": request.user.get_full_name() or request.user.username,
            "content": msg.content,
            "created_at": to_ph_time(msg.created_at).strftime("%I:%M %p"),
        }
    })


@login_required
def poll_messages(request):
    """Return only unread messages for the current user, then mark them read."""
    from admin_panel.models import UserMessage
    from django.db.models import Q

    unread = UserMessage.objects.filter(
        recipient=request.user, read=False
    ).select_related('sender')

    result = [
        {
            "id": m.pk,
            "sender": m.sender.get_full_name() or m.sender.username,
            "sender_username": m.sender.username,
            "sender_id": m.sender.pk,
            "sender_photo": m.sender.profile.profile_picture.url if hasattr(m.sender, 'profile') and m.sender.profile and m.sender.profile.profile_picture else '',
            "content": m.content,
            "created_at": to_ph_time(m.created_at).strftime("%I:%M %p"),
            "read": False,
            "is_mine": False,
        }
        for m in unread
    ]

    unread.update(read=True)
    return JsonResponse({"messages": result})


@login_required
def get_conversation(request):
    """Return all messages between current user and another user."""
    from admin_panel.models import UserMessage
    from django.contrib.auth.models import User
    from django.db.models import Q

    other_id = request.GET.get('user_id')
    if not other_id:
        return JsonResponse({"error": "user_id required"}, status=400)

    try:
        other = User.objects.get(pk=other_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    msgs = UserMessage.objects.filter(
        Q(sender=request.user, recipient=other) |
        Q(sender=other, recipient=request.user)
    ).select_related('sender').order_by('created_at')

    result = [
        {
            "id": m.pk,
            "sender_id": m.sender.pk,
            "sender": m.sender.get_full_name() or m.sender.username,
            "sender_photo": m.sender.profile.profile_picture.url if hasattr(m.sender, 'profile') and m.sender.profile and m.sender.profile.profile_picture else '',
            "content": m.content,
            "created_at": to_ph_time(m.created_at).strftime("%b %d, %I:%M %p"),
            "is_mine": m.sender_id == request.user.pk,
        }
        for m in msgs
    ]

    # Mark unread messages from this user as read
    msgs.filter(sender=other, recipient=request.user, read=False).update(read=True)

    return JsonResponse({"ok": True, "messages": result, "other_user": other.get_full_name() or other.username})


@login_required
def get_all_users(request):
    """Return all non-admin users for admin messaging inbox."""
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    from django.contrib.auth.models import User
    from admin_panel.models import UserMessage as UMsg
    from django.db.models import Q

    users = User.objects.filter(is_superuser=False, is_active=True).select_related('profile').order_by('first_name', 'username')

    result = []
    for u in users:
        last_msg = UMsg.objects.filter(
            Q(sender=request.user, recipient=u) |
            Q(sender=u, recipient=request.user)
        ).order_by('-created_at').first()

        unread_count = UMsg.objects.filter(sender=u, recipient=request.user, read=False).count()

        result.append({
            "id": u.pk,
            "name": u.get_full_name() or u.username,
            "username": u.username,
            "role": u.profile.role if hasattr(u, 'profile') else '',
            "photo": u.profile.profile_picture.url if hasattr(u, 'profile') and u.profile and u.profile.profile_picture else '',
            "last_message": last_msg.content[:60] if last_msg else '',
            "last_message_time": to_ph_time(last_msg.created_at).strftime("%b %d, %I:%M %p") if last_msg else '',
            "unread_count": unread_count,
            "has_conversation": last_msg is not None,
        })

    return JsonResponse({"ok": True, "users": result})


@login_required
def get_my_conversations(request):
    """Return conversation partners for the current user (non-admin cashier view)."""
    from admin_panel.models import UserMessage
    from django.db.models import Q, Max, Count

    user = request.user

    # Get all unique conversation partners
    sent_to = UserMessage.objects.filter(sender=user).values_list('recipient_id', flat=True)
    received_from = UserMessage.objects.filter(recipient=user).values_list('sender_id', flat=True)
    partner_ids = set(list(sent_to) + list(received_from))

    from django.contrib.auth.models import User
    partners = User.objects.filter(pk__in=partner_ids).select_related('profile')

    result = []
    for p in partners:
        last_msg = UserMessage.objects.filter(
            Q(sender=user, recipient=p) |
            Q(sender=p, recipient=user)
        ).order_by('-created_at').first()

        unread_count = UserMessage.objects.filter(sender=p, recipient=user, read=False).count()

        result.append({
            "id": p.pk,
            "name": p.get_full_name() or p.username,
            "username": p.username,
            "role": p.profile.role if hasattr(p, 'profile') else '',
            "photo": p.profile.profile_picture.url if hasattr(p, 'profile') and p.profile and p.profile.profile_picture else '',
            "last_message": last_msg.content[:60] if last_msg else '',
            "last_message_time": to_ph_time(last_msg.created_at).strftime("%b %d, %I:%M %p") if last_msg else '',
            "unread_count": unread_count,
        })

    return JsonResponse({"ok": True, "users": result})


@login_required
def about_view(request):
    is_admin = _is_admin(request.user)
    profiles_qs = Profile.objects.select_related('user').exclude(status__in=['disabled', 'suspended'])
    admins = [p for p in profiles_qs if p.role == 'admin']
    cashiers = [p for p in profiles_qs if p.role == 'cashier']
    context = _page_context(request, is_admin=is_admin, page_title="About", extra={
        'admins': admins,
        'cashiers': cashiers,
    })
    return render(request, "admin_panel/about.html", context)


@login_required
def about_cashier_view(request):
    return redirect('about_system')


def _is_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile, _ = Profile.objects.get_or_create(user=user, defaults={"role": "cashier"})
    return profile.role == "admin"


def _get_profile(user):
    profile, _ = Profile.objects.get_or_create(
        user=user,
        defaults={"role": "admin" if user.is_superuser else "cashier"},
    )
    return profile


from django.core.exceptions import PermissionDenied

def _enforce_permission(request, nav_key=None, module=None, action=None):
    if not request.user.is_authenticated:
        raise PermissionDenied("You must be logged in.")
    if request.user.is_superuser:
        return
    profile = _get_profile(request.user)
    if profile.role == "admin":
        return

    from cashier.models import DEFAULT_ROLE_PERMISSIONS
    role_config = DEFAULT_ROLE_PERMISSIONS.get(profile.role, {"navigation": [], "permissions": {}})

    if nav_key:
        allowed_nav = role_config.get("navigation", [])
        if nav_key not in allowed_nav:
            raise PermissionDenied("You do not have access to this page.")

    if module and action:
        allowed_actions = role_config.get("permissions", {}).get(module, [])
        if action not in allowed_actions:
            raise PermissionDenied("You do not have permission to perform this action.")


def _page_context(request, is_admin, page_title, extra=None):
    profile = _get_profile(request.user)
    from cashier.models import DEFAULT_ROLE_PERMISSIONS
    role_permissions = {}
    if profile:
        role_permissions = DEFAULT_ROLE_PERMISSIONS.get(profile.role, {"navigation": [], "permissions": {}})
    context = {
        "is_admin": is_admin,
        "page_title": page_title,
        "current_profile": profile,
        "role_permissions": role_permissions,
    }
    if extra:
        context.update(extra)
    return context


def _full_name_or_username(user):
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username


def _number_to_words(n: int) -> str:
    # Simple converter for numbers up to 999,999,999
    ones = ['', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
            'TEN', 'ELEVEN', 'TWELVE', 'THIRTEEN', 'FOURTEEN', 'FIFTEEN', 'SIXTEEN', 'SEVENTEEN', 'EIGHTEEN', 'NINETEEN']
    tens = ['', '', 'TWENTY', 'THIRTY', 'FORTY', 'FIFTY', 'SIXTY', 'SEVENTY', 'EIGHTY', 'NINETY']

    def below1000(x):
        if x == 0:
            return ''
        if x < 20:
            return ones[x] + ' '
        if x < 100:
            return tens[x // 10] + ((' ' + ones[x % 10]) if x % 10 else '') + ' '
        return ones[x // 100] + ' HUNDRED ' + below1000(x % 100)

    if n == 0:
        return 'ZERO'
    parts = []
    if n >= 1000000000000:
        parts.append(below1000(n // 1000000000000) + 'TRILLION')
        n = n % 1000000000000
    
    if n >= 1000000000:
        parts.append(below1000(n // 1000000000) + 'BILLION')
        n = n % 1000000000

    if n >= 1000000:
        parts.append(below1000(n // 1000000) + 'MILLION')
        n = n % 1000000
    if n >= 1000:
        parts.append(below1000(n // 1000) + 'THOUSAND')
        n = n % 1000
    if n > 0:
        parts.append(below1000(n))
    return ' '.join(p.strip() for p in parts).replace('  ', ' ').strip()


def _amount_to_words_py(amount: Decimal) -> str:
    try:
        amt = Decimal(amount).quantize(Decimal('0.01'))
    except Exception:
        return ''
    pesos = int(amt // 1)
    centavos = int((amt - pesos) * 100)
    pesos_text = _number_to_words(pesos) + ' PESOS'
    return f"{pesos_text} & {centavos:02d}/100 ONLY".upper()


def _compute_auto_supplier_defaults(user, global_scope=False):
    """Compute supplier auto defaults for a given user (series, code_line, or_number, etc.).

    If global_scope is True, compute against all suppliers (admin-style global flow).
    Otherwise compute per-user.
    """
    today = timezone.localdate()

    def _series_value(text):
        txt = str(text or '').strip()
        if not txt or not txt.isdigit():
            return None
        try:
            return int(txt)
        except Exception:
            return None

    series_qs = Supplier.objects.all() if global_scope else Supplier.objects.filter(created_by=user)
    existing_series = [_series_value(v) for v in series_qs.exclude(series_month='').values_list('series_month', flat=True)]
    existing_numbers = [v for v in existing_series if v is not None]
    next_series_month = (max(existing_numbers) if existing_numbers else series_qs.count()) + 1
    existing_count = series_qs.filter(created_at__date=today).count()

    auto_supplier_series_month = str(next_series_month)
    auto_supplier_series_day = str(existing_count + 1)
    today_supplier_date = today.isoformat()

    def _split_noc_pair(code_value, nature_value=''):
        code_text = str(code_value or '').strip()
        nature_text = str(nature_value or '').strip()
        if code_text and '-' in code_text:
            prefix, suffix = code_text.split('-', 1)
            prefix = prefix.strip()
            suffix = suffix.strip()
            if prefix.isdigit():
                code_text = prefix
                if suffix and not nature_text:
                    nature_text = suffix
        return code_text, nature_text

    def _latest_non_empty(field_name):
        return series_qs.exclude(**{field_name: ''}).order_by('-created_at', '-pk').values_list(field_name, flat=True).first() or ''

    def _next_trailing_number(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        parts = txt.split('-')
        if len(parts) == 3:
            a, b, c = parts
            if b.strip().isdigit():
                try:
                    return f"{a}-{int(b.strip()) + 1}-{c}"
                except Exception:
                    pass
        if '-' in txt:
            prefix, suffix = txt.rsplit('-', 1)
            if suffix.isdigit():
                try:
                    return f"{prefix}-{int(suffix) + 1}"
                except Exception:
                    pass
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            return f"{prefix}{num + 1}"
        return txt + '-1'

    def _next_or_number_value():
        existing_or_numbers = [str(v).strip() for v in series_qs.exclude(or_number='').values_list('or_number', flat=True) if str(v).strip()]
        if not existing_or_numbers:
            return ''

        def _or_sort_key(value):
            text = str(value or '').strip()
            digits = re.findall(r'\d+', text)
            if digits:
                try:
                    return (0, int(digits[-1]), text)
                except Exception:
                    pass
            return (1, text.lower(), text)

        highest_or_number = max(existing_or_numbers, key=_or_sort_key)
        return _next_trailing_number(highest_or_number)

    latest_noc_supplier = series_qs.filter(code_noc__gt='').order_by('-created_at', '-pk').first()
    default_code_noc = ''
    default_nature_of_collections = ''
    if latest_noc_supplier:
        default_code_noc, default_nature_of_collections = _split_noc_pair(latest_noc_supplier.code_noc, latest_noc_supplier.nature_of_collections)
        if not default_nature_of_collections:
            default_nature_of_collections = latest_noc_supplier.nature_of_collections or ''
        default_code_noc = _next_trailing_number(default_code_noc)

    # compute code_or2 for today
    date_qs = Supplier.objects.filter(date=today) if global_scope else Supplier.objects.filter(created_by=user, date=today)
    day_count = date_qs.count()
    code_or2_default = f"{today.month}-{today.day}-{day_count + 1}"

    auto_supplier_defaults = {
        'mr_or': _next_trailing_number(_latest_non_empty('mr_or')) if series_qs.exists() else '',
        'mr': today.strftime('%Y-%b-%d'),
        'code_or': _next_trailing_number(_latest_non_empty('code_or')) if series_qs.exists() else '',
        'code_or2': code_or2_default,
        'or_number': _next_or_number_value(),
        'code_line': _latest_non_empty('code_line'),
        'line': _latest_non_empty('line'),
        'code_noc': default_code_noc,
        'nature_of_collections': default_nature_of_collections,
    }

    # increment code_line/line defaults when numeric/trailing
    last_code_line = auto_supplier_defaults.get('code_line', '')
    next_code_line = _next_trailing_number(last_code_line) if last_code_line else ''
    auto_supplier_defaults['code_line'] = next_code_line or last_code_line

    last_line = auto_supplier_defaults.get('line', '')
    try:
        next_line = str(int(last_line) + 1) if str(last_line).strip().isdigit() else last_line
    except Exception:
        next_line = last_line
    auto_supplier_defaults['line'] = next_line

    # Auto-migrate any old Tax (3%/1%) to Tax (2%/1%)
    ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, value='Tax (3%/1%)').update(value='Tax (2%/1%)', uacs='2%/1%')
    active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
    t1 = active_taxes[0] if len(active_taxes) > 0 else None
    t2 = active_taxes[1] if len(active_taxes) > 1 else None

    tax_1_rate = t1.uacs if t1 else '5%/3%'
    tax_2_rate = t2.uacs if t2 else '2%/1%'
    tax_1_label = t1.value if t1 else 'Tax (5%/3%)'
    tax_2_label = t2.value if t2 else 'Tax (2%/1%)'

    return {
        'auto_supplier_series_month': auto_supplier_series_month,
        'auto_supplier_series_day': auto_supplier_series_day,
        'today_supplier_date': today_supplier_date,
        'auto_supplier_defaults': auto_supplier_defaults,
        'tax_1_rate': tax_1_rate,
        'tax_2_rate': tax_2_rate,
        'tax_1_label': tax_1_label,
        'tax_2_label': tax_2_label,
    }


@login_required
def cheque_pdf(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)

    # Create PDF sized 8in x 3in
    buf = BytesIO()
    w = 8 * inch
    h = 3 * inch
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFillColorRGB(0, 0, 0)

    # margins
    left = 18
    right = w - 18
    top = h - 18

    # Bank name
    c.setFont('Courier-Bold', 12)
    c.drawString(left, top - 6, (cheque.bank_name or 'Bank Name').upper())
    # Account No label
    c.setFont('Courier', 8)
    c.drawString(left, top - 20, 'Account No.:')

    # Cheque no (top-right)
    c.setFont('Courier', 8)
    c.drawRightString(right, top - 6, 'CHEQUE NO.')
    c.setFont('Courier-Bold', 12)
    c.drawRightString(right, top - 20, str(cheque.real_cheque_number or '—'))

    # Date (right area)
    c.setFont('Courier', 8)
    c.drawRightString(right - 4*mm, top - 36, 'Date:')
    c.setFont('Courier', 9)
    date_str = cheque.date.strftime('%b %d, %Y') if cheque.date else ''
    c.drawRightString(right, top - 36, date_str)

    # Payee line
    c.setFont('Courier', 9)
    payee_y = top - 60
    c.drawString(left, payee_y, 'PAY TO THE ORDER OF')
    # underline for payee
    c.line(left, payee_y - 6, right - 160, payee_y - 6)
    c.drawString(left + 120, payee_y, (cheque.payee_name or '').upper())

    # Amount box (right)
    box_w = 110
    box_h = 22
    box_x = right - box_w
    box_y = payee_y - 6 - box_h
    c.rect(box_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFont('Courier-Bold', 10)
    amt_text = f'₱ {cheque.amount:.2f}' if cheque.amount is not None else '₱ 0.00'
    c.drawRightString(box_x + box_w - 6, box_y + box_h/2 - 4, amt_text)

    # Words line (uppercase)
    words = _amount_to_words_py(cheque.amount or Decimal('0.00'))
    c.setFont('Courier-Bold', 9)
    words_y = box_y - 18
    c.drawString(left, words_y, words)
    # underline under words
    c.line(left, words_y - 4, right - 30, words_y - 4)

    # Signature block
    sig_x = right - 180
    sig_y = words_y - 28
    c.line(sig_x, sig_y + 18, sig_x + 160, sig_y + 18)
    c.setFont('Courier-Bold', 8)
    c.drawString(sig_x + 10, sig_y + 6, 'Authorized personnel')
    c.setFont('Courier', 7)
    c.drawString(sig_x + 10, sig_y - 6, 'Authorize Signature over printed name')

    # MICR dashed line bottom
    micr_y = 12
    c.setDash(3, 3)
    c.setLineWidth(0.8)
    c.line(left, micr_y + 6, right, micr_y + 6)
    c.setDash()
    # MICR numbers (placeholder)
    c.setFont('Courier', 8)
    c.drawCentredString(w/2, micr_y - 2, f'⑆ {cheque.real_cheque_number or "0000000"} ⑆ 000000000 ⑆ 000000 ⑆')

    c.showPage()
    c.save()
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type='application/pdf')


def _audit(user, action, details=None):
    try:
        AuditLog.objects.create(admin=user, action=action, details=details or {})
    except Exception:
        pass


def _db_path():
    db_settings = settings.DATABASES["default"]
    if db_settings.get("ENGINE") != "django.db.backends.sqlite3":
        return None
    return Path(db_settings["NAME"]).resolve()


def _media_root():
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if media_root:
        return Path(media_root).resolve()
    return (Path(settings.BASE_DIR) / "media").resolve()


def _count_backup_stats():
    return {
        "users": User.objects.count(),
        "profiles": Profile.objects.count(),
        "transactions": Transaction.objects.count(),
        "cheques": Cheque.objects.count(),
        "fund_clusters": FundCluster.objects.count(),
        "suppliers": Supplier.objects.count(),
        "reports": Report.objects.count(),
        "audit_logs": AuditLog.objects.count(),
        "system_settings": SystemSetting.objects.count(),
    }


def _write_sqlite_snapshot(source_db_path: Path, dest_db_path: Path):
    source_conn = sqlite3.connect(str(source_db_path))
    try:
        dest_conn = sqlite3.connect(str(dest_db_path))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _pack_backup_archive(created_by):
    timestamp = timezone.now().strftime("%Y_%m_%d_%H%M%S")
    backup_name = f"system_backup_{timestamp}_FULL.bak"
    temp_dir = Path(tempfile.mkdtemp(prefix="finalproject_backup_"))
    snapshot_db = temp_dir / "db.sqlite3"
    db_json_path = temp_dir / "db_data.json"
    manifest_path = temp_dir / "manifest.json"
    archive_path = temp_dir / backup_name

    try:
        db_path = _db_path()
        has_sqlite = False
        if db_path and db_path.exists():
            _write_sqlite_snapshot(db_path, snapshot_db)
            has_sqlite = True

        from django.core import serializers
        from django.contrib.auth.models import User
        from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, RoleConfig
        from admin_panel.models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup

        restore_models = [
            User, Profile, RoleConfig, SystemSetting, AccountTitleGroup, ManagementOption,
            FundCluster, Supplier, Cheque, Report, AuditLog, Transaction
        ]

        objects = []
        for model in restore_models:
            objects.extend(list(model.objects.all()))

        json_data = serializers.serialize("json", objects, indent=2)
        db_json_path.write_text(json_data, encoding="utf-8")

        manifest = {
            "version": 1,
            "backup_type": "FULL",
            "created_at": timezone.now().isoformat(),
            "created_by": getattr(created_by, "id", None),
            "created_by_username": getattr(created_by, "username", ""),
            "database": str(db_path.name) if db_path else "mysql",
            "counts": _count_backup_stats(),
            "has_sqlite": has_sqlite,
            "sections": [
                "database",
                "config",
                "auth",
                "financial",
                "system",
                "audit",
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if has_sqlite:
                zf.write(snapshot_db, arcname="database/db.sqlite3")
            zf.write(db_json_path, arcname="database/db_data.json")
            zf.write(manifest_path, arcname="manifest.json")

            media_root = _media_root()
            if media_root.exists():
                for file_path in media_root.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, arcname=f"media/{file_path.relative_to(media_root).as_posix()}")

            profile_root = Path(settings.BASE_DIR) / "profile_pictures"
            if profile_root.exists() and profile_root.is_dir():
                for file_path in profile_root.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, arcname=f"profile_pictures/{file_path.relative_to(profile_root).as_posix()}")

        return archive_path, backup_name, manifest
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _get_backup_storage_dir():
    """Get or create the server-side backup storage directory."""
    backup_dir = Path(settings.MEDIA_ROOT) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _store_backup(archive_path, backup_name):
    """Store a backup copy in the server-side backup directory."""
    import shutil as _shutil
    backup_dir = _get_backup_storage_dir()
    dest = backup_dir / backup_name
    _shutil.copy2(str(archive_path), str(dest))
    return dest


def _enforce_backup_retention():
    """Delete old backups based on retention policy. Returns number of backups deleted."""
    settings_obj = SystemSetting.get_settings()
    if not settings_obj.retention_enabled or not settings_obj.retention_auto_delete:
        return 0

    backup_dir = _get_backup_storage_dir()
    if not backup_dir.exists():
        return 0

    # Get all backup files sorted by modification time (newest first)
    backup_files = sorted(
        [f for f in backup_dir.iterdir() if f.is_file() and f.suffix == '.bak'],
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    deleted = 0
    now = timezone.now()

    # Enforce max count
    if settings_obj.retention_max_count > 0:
        for backup_file in backup_files[settings_obj.retention_max_count:]:
            try:
                backup_file.unlink()
                deleted += 1
            except Exception:
                pass

    # Re-list after count enforcement
    if settings_obj.retention_max_age_days > 0:
        backup_files = [f for f in backup_dir.iterdir() if f.is_file() and f.suffix == '.bak']
        max_age_seconds = settings_obj.retention_max_age_days * 86400
        for backup_file in backup_files:
            try:
                file_age = now.timestamp() - backup_file.stat().st_mtime
                if file_age > max_age_seconds:
                    backup_file.unlink()
                    deleted += 1
            except Exception:
                pass

    return deleted


def _inspect_backup_archive(archive_path: Path):
    if not archive_path.exists():
        raise FileNotFoundError("Backup file not found")
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                raise ValueError("Backup manifest missing")
            if "database/db_data.json" not in names and "database/db.sqlite3" not in names:
                raise ValueError("Database snapshot missing")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            result = {
                "backup_type": manifest.get("backup_type", "FULL"),
                "created_at": manifest.get("created_at"),
                "created_by_username": manifest.get("created_by_username", ""),
                "counts": manifest.get("counts", {}),
                "sections": manifest.get("sections", []),
                "files": names[:40],
            }
            data_preview = {
                "fund_clusters": [],
                "suppliers": [],
                "users": [],
                "account_titles": [],
                "reports": [],
                "cheques": [],
            }
            profiles_by_user = {}
            if "database/db_data.json" in names:
                raw = json.loads(zf.read("database/db_data.json").decode("utf-8"))
                model_map = {
                    "admin_panel.fundcluster": "fund_clusters",
                    "cashier.fundcluster": "fund_clusters",
                    "admin_panel.supplier": "suppliers",
                    "cashier.supplier": "suppliers",
                    "auth.user": "users",
                    "cashier.profile": "profiles",
                    "admin_panel.accounttitlegroup": "account_title_groups",
                    "cashier.accounttitlegroup": "account_title_groups",
                    "admin_panel.managementoption": "account_titles",
                    "cashier.managementoption": "account_titles",
                    "admin_panel.report": "reports",
                    "cashier.report": "reports",
                    "admin_panel.cheque": "cheques",
                    "cashier.cheque": "cheques",
                }
                for item in raw:
                    pk = item.get("pk")
                    model = (item.get("model") or "").lower()
                    fields = item.get("fields", {})
                    target = model_map.get(model)
                    if not target or pk is None:
                        continue
                    if target == "users":
                        data_preview[target].append({
                            "id": pk,
                            "username": fields.get("username", ""),
                            "email": fields.get("email", ""),
                            "first_name": fields.get("first_name", ""),
                            "last_name": fields.get("last_name", ""),
                            "is_staff": fields.get("is_staff", False),
                            "is_superuser": fields.get("is_superuser", False),
                        })
                    elif target == "profiles":
                        user_fk = fields.get("user")
                        for u in data_preview["users"]:
                            if u["id"] == user_fk:
                                u["employee_id"] = fields.get("employee_id", "")
                                u["full_name"] = " ".join(filter(None, [
                                    fields.get("first_name", "") or "",
                                    fields.get("middle_initial", ""),
                                    fields.get("last_name", "") or "",
                                ])).strip() or u["username"]
                                u["role"] = fields.get("role", "")
                                u["department"] = fields.get("department", "")
                                u["position"] = fields.get("position", "")
                                u["status"] = fields.get("status", "")
                                break
                    elif target == "fund_clusters":
                        data_preview[target].append({
                            "id": pk,
                            "code": fields.get("code", ""),
                            "name": fields.get("name", ""),
                            "description": fields.get("description", ""),
                            "balance": fields.get("balance", "0"),
                            "bank_name": fields.get("bank_name", ""),
                            "is_active": fields.get("is_active", True),
                        })
                    elif target == "suppliers":
                        data_preview[target].append({
                            "id": pk,
                            "account_name": fields.get("account_name", ""),
                            "account_number": fields.get("account_number", ""),
                            "code_line": fields.get("code_line", ""),
                            "line": fields.get("line", ""),
                            "code_noc": fields.get("code_noc", ""),
                            "nature_of_collections": fields.get("nature_of_collections", ""),
                            "amount": fields.get("amount", "0"),
                            "remarks": fields.get("remarks", ""),
                            "address": fields.get("address", ""),
                            "tin": fields.get("tin", ""),
                            "status": fields.get("status", "active"),
                            "mr_or": fields.get("mr_or", ""),
                            "or_number": fields.get("or_number", ""),
                            "date": fields.get("date", ""),
                        })
                    elif target == "account_title_groups":
                        data_preview["account_titles"].append({
                            "id": pk,
                            "value": fields.get("name", ""),
                            "uacs": fields.get("uacs", ""),
                            "group_name": fields.get("name", ""),
                            "is_active": True,
                        })
                    elif target == "reports":
                        data_preview[target].append({
                            "id": pk,
                            "title": fields.get("title", ""),
                            "report_type": fields.get("report_type", ""),
                            "generated_by": fields.get("generated_by", ""),
                        })
                    elif target == "cheques":
                        data_preview[target].append({
                            "id": pk,
                            "cheque_number": fields.get("cheque_number", ""),
                            "payee": fields.get("payee", ""),
                            "payee_name": fields.get("payee_name", ""),
                            "fund_cluster": fields.get("fund_cluster", ""),
                            "amount": fields.get("amount", "0"),
                            "date": fields.get("date", ""),
                            "purpose": fields.get("purpose", ""),
                            "bank_name": fields.get("bank_name", ""),
                            "dv_payroll_no": fields.get("dv_payroll_no", ""),
                            "ors_burs_no": fields.get("ors_burs_no", ""),
                            "responsibility_center": fields.get("responsibility_center", ""),
                            "uacs_object_code": fields.get("uacs_object_code", ""),
                            "nature_of_payment": fields.get("nature_of_payment", ""),
                            "professional_tax": fields.get("professional_tax", "0"),
                            "tax_5_3": fields.get("tax_5_3", "0"),
                            "tax_3_1": fields.get("tax_3_1", "0"),
                            "status": fields.get("status", ""),
                        })
                for u in data_preview["users"]:
                    if "full_name" not in u:
                        u["full_name"] = " ".join(filter(None, [
                            u.get("first_name", ""),
                            u.get("last_name", ""),
                        ])).strip() or u["username"]
                    u.setdefault("employee_id", "")
                    u.setdefault("role", "")
                    u.setdefault("department", "")
                    u.setdefault("position", "")
                    u.setdefault("status", "")
            result["data_preview"] = data_preview
            return result
    except zipfile.BadZipFile:
        raise ValueError("Invalid backup file format")


def _restore_media_tree(extract_root: Path):
    media_root = _media_root()
    media_source = extract_root / "media"
    profile_source = extract_root / "profile_pictures"

    if media_source.exists():
        if media_root.exists():
            shutil.rmtree(media_root, ignore_errors=True)
        media_root.mkdir(parents=True, exist_ok=True)
        for file_path in media_source.rglob("*"):
            if file_path.is_file():
                target = media_root / file_path.relative_to(media_source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, target)

    if profile_source.exists():
        profile_root = Path(settings.BASE_DIR) / "profile_pictures"
        if profile_root.exists():
            shutil.rmtree(profile_root, ignore_errors=True)
        profile_root.mkdir(parents=True, exist_ok=True)
        for file_path in profile_source.rglob("*"):
            if file_path.is_file():
                target = profile_root / file_path.relative_to(profile_source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, target)


def _apply_restore_archive(archive_path: Path, user):
    if not archive_path.exists():
        raise FileNotFoundError("Backup file not found")

    temp_dir = Path(tempfile.mkdtemp(prefix="finalproject_restore_"))
    emergency_backup_path = None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(temp_dir)

        manifest = json.loads((temp_dir / "manifest.json").read_text(encoding="utf-8"))
        snapshot_db = temp_dir / "database" / "db.sqlite3"
        snapshot_json = temp_dir / "database" / "db_data.json"

        if not snapshot_db.exists() and not snapshot_json.exists():
            raise ValueError("Database snapshot missing in backup archive")

        emergency_backup_path, _, _ = _pack_backup_archive(user)

        from django.db import transaction, models
        from django.contrib.auth.models import User
        from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, RoleConfig
        from admin_panel.models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup

        restore_models = [
            User, Profile, RoleConfig, SystemSetting, AccountTitleGroup, ManagementOption,
            FundCluster, Supplier, Cheque, Report, AuditLog, Transaction
        ]

        with transaction.atomic():
            for model in reversed(restore_models):
                if model == User:
                    model.objects.exclude(id=user.id).delete()
                elif model == Profile:
                    model.objects.exclude(user_id=user.id).delete()
                else:
                    model.objects.all().delete()

            if snapshot_json.exists():
                from django.core import serializers
                with open(snapshot_json, "r", encoding="utf-8") as f:
                    for deserialized_object in serializers.deserialize("json", f):
                        obj = deserialized_object.object
                        if isinstance(obj, User) and obj.id == user.id:
                            user.username = obj.username
                            user.first_name = obj.first_name
                            user.last_name = obj.last_name
                            user.email = obj.email
                            user.password = obj.password
                            user.is_staff = obj.is_staff
                            user.is_active = obj.is_active
                            user.is_superuser = obj.is_superuser
                            user.save()
                        elif isinstance(obj, Profile) and obj.user_id == user.id:
                            p = Profile.objects.filter(user_id=user.id).first()
                            if p:
                                p.role = obj.role
                                p.employee_id = obj.employee_id
                                p.middle_initial = obj.middle_initial
                                p.department = obj.department
                                p.position = obj.position
                                p.rfid_uid = obj.rfid_uid
                                p.status = obj.status
                                p.save()
                            else:
                                deserialized_object.save()
                        else:
                            deserialized_object.save()
            else:
                import sqlite3
                conn = sqlite3.connect(str(snapshot_db))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                try:
                    for model in restore_models:
                        table_name = model._meta.db_table
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                        if not cursor.fetchone():
                            continue

                        cursor.execute(f"SELECT * FROM {table_name}")
                        rows = cursor.fetchall()
                        for r in rows:
                            row_dict = dict(r)
                            if model == User and row_dict['id'] == user.id:
                                u = User.objects.get(id=user.id)
                                for field in model._meta.fields:
                                    if field.name != 'id':
                                        setattr(u, field.name, row_dict.get(field.column))
                                u.save()
                            elif model == Profile and row_dict['user_id'] == user.id:
                                p = Profile.objects.filter(user_id=user.id).first()
                                if p:
                                    for field in model._meta.fields:
                                        if field.name != 'id' and field.name != 'user':
                                            setattr(p, field.name, row_dict.get(field.column))
                                    p.save()
                                else:
                                    inst = model()
                                    for field in model._meta.fields:
                                        setattr(inst, field.name, row_dict.get(field.column))
                                    inst.save()
                            else:
                                inst = model()
                                for field in model._meta.fields:
                                    val = row_dict.get(field.column)
                                    if isinstance(field, models.JSONField) and isinstance(val, str):
                                        try:
                                            val = json.loads(val)
                                        except Exception:
                                            pass
                                    setattr(inst, field.name, val)
                                inst.save()
                finally:
                    conn.close()

        _restore_media_tree(temp_dir)
        _audit(user, "Backup restored", {"created_at": manifest.get("created_at"), "backup_type": manifest.get("backup_type", "FULL")})
        return manifest
    except Exception as e:
        if emergency_backup_path and emergency_backup_path.exists():
            try:
                with zipfile.ZipFile(emergency_backup_path, "r") as zf:
                    zf.extractall(temp_dir / "rollback")
                _restore_media_tree(temp_dir / "rollback")
                _audit(user, "Emergency rollback completed after failed restore", {
                    "original_error": str(e)[:200],
                    "rollback_file": str(emergency_backup_path),
                })
            except Exception as rollback_err:
                import logging
                logger = logging.getLogger("admin_panel.restore")
                logger.critical(
                    "CRITICAL: Both restore and emergency rollback failed. "
                    "Original error: %s | Rollback error: %s | Emergency backup: %s",
                    str(e), str(rollback_err), str(emergency_backup_path),
                )
                e = RuntimeError(
                    f"Restore failed ({e}) and emergency rollback also failed "
                    f"({rollback_err}). Manual intervention required. "
                    f"Emergency backup: {emergency_backup_path}"
                )
        raise e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



# ─────────────────────────────────────────────
# backup
# ─────────────────────────────────────────────












# ─────────────────────────────────────────────
# DASHBOARDS
# ─────────────────────────────────────────────

@login_required
def admin_dashboard(request):
    if not _is_admin(request.user):
        return redirect("cashier_dashboard")

    from django.utils import timezone
    from datetime import timedelta
    import calendar

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    total_cheques = Cheque.objects.count()
    pending_cheques = Cheque.objects.filter(status='pending').count()
    draft_cheques = Cheque.objects.filter(status='draft').count()
    unreleased_cheques = draft_cheques + pending_cheques
    released_cheques = Cheque.objects.filter(status='released').count()
    voided_cheques = Cheque.objects.filter(status='voided').count()
    stale_cheques = Cheque.objects.filter(status='stale').count()
    total_amount = Cheque.objects.filter(status='released').aggregate(t=Sum('amount'))['t'] or 0
    total_users = User.objects.count()
    total_suppliers = Supplier.objects.filter(status='active').count()
    total_funds = FundCluster.objects.filter(is_active=True).count()

    # Today's activity
    today_created = Cheque.objects.filter(created_at__gte=today_start, created_at__lt=today_end).count()
    today_released = Cheque.objects.filter(status='released', updated_at__gte=today_start, updated_at__lt=today_end).count()
    today_amount = Cheque.objects.filter(status='released', updated_at__gte=today_start, updated_at__lt=today_end).aggregate(t=Sum('amount'))['t'] or 0

    # RADAI stats
    radai_total = Radai.objects.count()
    radai_amount = Radai.objects.aggregate(t=Sum('amount'))['t'] or 0

    # All fund clusters by released amount
    top_funds_raw = (
        Cheque.objects
        .filter(status='released', fund_cluster__isnull=False)
        .values('fund_cluster__code', 'fund_cluster__name')
        .annotate(total_released=Sum('amount'))
        .order_by('-total_released')
    )
    top_funds = [
        {'code': f['fund_cluster__code'], 'name': f['fund_cluster__name'], 'released_amount': f['total_released']}
        for f in top_funds_raw
    ]

    # Cheque processing speed (avg days from creation to last update for released cheques)
    released_cheques_qs = Cheque.objects.filter(status='released').exclude(updated_at__isnull=True)
    if released_cheques_qs.exists():
        from django.db.models import Avg, F, ExpressionWrapper, DurationField
        processing_speed = released_cheques_qs.annotate(
            processing_time=ExpressionWrapper(F('updated_at') - F('created_at'), output_field=DurationField())
        ).aggregate(avg_speed=Avg('processing_time'))['avg_speed']
        if processing_speed:
            avg_processing_days = round(processing_speed.total_seconds() / 86400, 1)
        else:
            avg_processing_days = 0
    else:
        avg_processing_days = 0

    # Monthly trend (last 12 months) for sparkline
    month_labels_12 = []
    month_counts_12 = []
    for i in range(11, -1, -1):
        year = now.year
        month = now.month - i
        if month <= 0:
            month += 12
            year -= 1
        month_name = calendar.month_abbr[month]
        month_labels_12.append(f"{month_name}")
        month_start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
        if month == 12:
            month_end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
        else:
            month_end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
        count = Cheque.objects.filter(created_at__gte=month_start, created_at__lt=month_end).count()
        month_counts_12.append(count)

    # Stale cheques (pending/draft for more than 7 days)
    stale_threshold = now - timedelta(days=7)
    stale_cheques_list = Cheque.objects.filter(
        status__in=['pending', 'draft'],
        created_at__lt=stale_threshold
    ).select_related('payee', 'fund_cluster', 'created_by').order_by('created_at')[:5]
    stale_cheques_count = Cheque.objects.filter(
        status__in=['pending', 'draft'],
        created_at__lt=stale_threshold
    ).count()

    # Cashier performance (top 5 by cheques created this month)
    month_start_current = timezone.datetime(now.year, now.month, 1, tzinfo=timezone.get_current_timezone())
    cashier_performance = User.objects.filter(
        cheques_created__created_at__gte=month_start_current
    ).annotate(
        cheque_count=Count('cheques_created')
    ).order_by('-cheque_count')[:5]

    # System alerts
    alerts = []
    # Low fund balances (balance < 10000)
    low_funds = FundCluster.objects.filter(is_active=True, balance__lt=10000).exclude(balance=0)
    for fund in low_funds:
        alerts.append({
            'type': 'warning',
            'icon': 'fas fa-exclamation-triangle',
            'message': f'Low balance on {fund.code}: ₱{fund.balance:,.2f}',
            'color': '#f59e0b'
        })
    # Stale cheques alert
    if stale_cheques_count > 0:
        alerts.append({
            'type': 'danger',
            'icon': 'fas fa-clock',
            'message': f'{stale_cheques_count} cheque(s) pending for more than 7 days',
            'color': '#ef4444'
        })
    # Voided today
    today_voided = Cheque.objects.filter(status='voided', voided_at__gte=today_start, voided_at__lt=today_end).count()
    if today_voided > 0:
        alerts.append({
            'type': 'info',
            'icon': 'fas fa-ban',
            'message': f'{today_voided} cheque(s) voided today',
            'color': '#64748b'
        })

    # Available funds (for the commented out section if needed)
    available_funds = list(FundCluster.objects.filter(is_active=True, balance__gt=0).order_by('-balance', 'code'))
    available_funds_total = sum((fund.balance or 0) for fund in available_funds)
    available_funds_max = max((fund.balance or 0) for fund in available_funds) if available_funds else Decimal('0')
    chart_colors = [
        "#0f766e",
        "#2563eb",
        "#7c3aed",
        "#ea580c",
        "#16a34a",
        "#dc2626",
        "#0891b2",
        "#4f46e5",
    ]
    available_fund_segments = []
    for fund in available_funds:
        if available_funds_max and available_funds_max > 0:
            fund.available_percent = float((fund.balance or 0) / available_funds_max * 100)
        else:
            fund.available_percent = 0.0
        if available_funds_total and available_funds_total > 0:
            fund.distribution_percent = float((fund.balance or 0) / available_funds_total * 100)
        else:
            fund.distribution_percent = 0.0
            fund.distribution_color = chart_colors[len(available_fund_segments) % len(chart_colors)]
        available_fund_segments.append(fund)
    recent_cheques = Cheque.objects.select_related('payee', 'fund_cluster', 'created_by').order_by('-created_at')[:8]
    for rc in recent_cheques:
        rc_days = (now - rc.created_at).days if rc.created_at else 0
        rc.remaining_days = max(0, 180 - rc_days)
    recent_audit = AuditLog.objects.select_related('admin').order_by('-timestamp')[:6]

    # Cheques with duration (days since created) - pending/draft only, nearest first
    cheques_with_duration = []
    active_cheques = Cheque.objects.select_related('payee', 'fund_cluster').filter(
        status__in=['pending', 'draft']
    ).order_by('created_at')[:20]
    for c in active_cheques:
        days = (now - c.created_at).days if c.created_at else 0
        remaining_days = max(0, 180 - days)
        cheques_with_duration.append({
            'cheque': c,
            'days': days,
            'remaining_days': remaining_days,
        })
    # Sort by days ascending (nearest/most urgent first)
    cheques_with_duration.sort(key=lambda x: x['days'])

    # Monthly chart data (last 6 months)
    month_labels = []
    month_counts = []
    month_amounts = []
    for i in range(5, -1, -1):
        year = now.year
        month = now.month - i
        if month <= 0:
            month += 12
            year -= 1
        month_name = calendar.month_abbr[month]
        month_labels.append(f"{month_name} {year}")
        month_start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
        if month == 12:
            month_end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
        else:
            month_end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
        count = Cheque.objects.filter(created_at__gte=month_start, created_at__lt=month_end).count()
        amount = Cheque.objects.filter(created_at__gte=month_start, created_at__lt=month_end, status='released').aggregate(t=Sum('amount'))['t'] or 0
        month_counts.append(count)
        month_amounts.append(float(amount))

    context = _page_context(request, is_admin=True, page_title="Admin Dashboard", extra={
        "total_cheques": total_cheques,
        "pending_cheques": pending_cheques,
        "draft_cheques": draft_cheques,
        "unreleased_cheques": unreleased_cheques,
        "released_cheques": released_cheques,
        "voided_cheques": voided_cheques,
        "stale_cheques": stale_cheques,
        "total_amount": total_amount,
        "total_users": total_users,
        "total_suppliers": total_suppliers,
        "total_funds": total_funds,
        "available_funds": available_funds,
        "available_fund_segments": available_fund_segments,
        "available_funds_total": available_funds_total,
        "recent_cheques": recent_cheques,
        "recent_audit": recent_audit,
        "month_labels": month_labels,
        "month_counts": month_counts,
        "month_amounts": month_amounts,
        "today_created": today_created,
        "today_released": today_released,
        "today_amount": today_amount,
        "radai_total": radai_total,
        "radai_amount": radai_amount,
        "top_funds": top_funds,
        "avg_processing_days": avg_processing_days,
        "month_labels_12": month_labels_12,
        "month_counts_12": month_counts_12,
        "stale_cheques_list": stale_cheques_list,
        "stale_cheques_count": stale_cheques_count,
        "cashier_performance": cashier_performance,
        "cheques_with_duration": cheques_with_duration,
        "alerts": alerts,
    })
    return render(request, "admin_panel/dashboard.html", context)


@login_required
def cashier_dashboard(request):
    profile = _get_profile(request.user)
    is_guest = profile and profile.role == 'guest'

    extra = {"is_guest": is_guest}
    if not is_guest:
        my_cheques = Cheque.objects.all()
        my_total = my_cheques.count()
        my_pending = my_cheques.filter(status='pending').count()
        my_released = my_cheques.filter(status='released').count()
        my_draft = my_cheques.filter(status='draft').count()
        my_voided = my_cheques.filter(status='voided').count()
        my_stale = my_cheques.filter(status='stale').count()
        my_amount = my_cheques.filter(status='released').aggregate(t=Sum('amount'))['t'] or 0
        recent = my_cheques.order_by('-created_at')[:6]
        _now = timezone.now()
        for rc in recent:
            rc_days = (_now - rc.created_at).days if rc.created_at else 0
            rc.remaining_days = max(0, 180 - rc_days)

        # Unreleased = draft + pending
        my_unreleased = my_draft + my_pending

        # Count cheques expiring soon (stale_at within 30 days) and already stale
        from datetime import date as date_type, timedelta
        today = date_type.today()
        expiring_soon = my_cheques.filter(
            status='released',
            stale_at__isnull=False,
            stale_at__gte=today,
            stale_at__lte=today + timedelta(days=30)
        ).count()
        already_stale = my_cheques.filter(
            status='released',
            stale_at__isnull=False,
            stale_at__lt=today
        ).count()

        # Today's activity
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_created = my_cheques.filter(created_at__gte=today_start).count()
        today_released = my_cheques.filter(status='released', updated_at__gte=today_start).count()
        today_amount = my_cheques.filter(status='released', updated_at__gte=today_start).aggregate(t=Sum('amount'))['t'] or 0

        # Suppliers and RADAI counts (system-wide shared data)
        from cashier.models import Supplier, Radai
        my_suppliers = Supplier.objects.filter(status='active').count()
        my_radai = Radai.objects.filter(status='active').count()
        my_radai_amount = Radai.objects.filter(status='active').aggregate(t=Sum('amount'))['t'] or 0

        # Average processing days (for user's released cheques)
        from django.db.models import F
        released_with_days = my_cheques.filter(status='released').exclude(date__isnull=True)
        avg_processing_days = 0
        if released_with_days.exists():
            from django.db.models.functions import Cast
            from django.db.models import DateField, Avg
            avg_days_result = released_with_days.annotate(
                days_to_release=Cast(F('updated_at'), output_field=DateField()) - F('date')
            ).aggregate(avg=Avg('days_to_release'))
            if avg_days_result and avg_days_result.get('avg') is not None:
                avg_processing_days = round(avg_days_result['avg'].days, 1)

        # Stale cheques list
        stale_cheques_list = my_cheques.filter(
            status__in=['pending', 'draft'],
            created_at__lt=timezone.now() - timedelta(days=7)
        ).select_related('payee', 'fund_cluster')[:5]
        stale_cheques_count = stale_cheques_list.count()

        # Alerts
        alerts = []
        if stale_cheques_count > 0:
            alerts.append({
                'type': 'danger',
                'icon': 'fas fa-clock',
                'message': f'{stale_cheques_count} of your cheques pending for more than 7 days',
                'color': '#ef4444'
            })
        if already_stale > 0:
            alerts.append({
                'type': 'danger',
                'icon': 'fas fa-exclamation-triangle',
                'message': f'{already_stale} of your cheques are already stale',
                'color': '#dc2626'
            })

        # Monthly chart data (last 6 months)
        import calendar
        now = timezone.now()
        my_month_labels = []
        my_month_counts = []
        my_month_amounts = []
        for i in range(5, -1, -1):
            year = now.year
            month = now.month - i
            if month <= 0:
                month += 12
                year -= 1
            month_name = calendar.month_abbr[month]
            my_month_labels.append(f"{month_name} {year}")
            month_start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
            if month == 12:
                month_end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
            else:
                month_end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
            count = my_cheques.filter(created_at__gte=month_start, created_at__lt=month_end).count()
            amount = my_cheques.filter(created_at__gte=month_start, created_at__lt=month_end, status='released').aggregate(t=Sum('amount'))['t'] or 0
            my_month_counts.append(count)
            my_month_amounts.append(float(amount))

        extra.update({
            "my_total": my_total,
            "my_pending": my_pending,
            "my_released": my_released,
            "my_draft": my_draft,
            "my_voided": my_voided,
            "my_stale": my_stale,
            "my_unreleased": my_unreleased,
            "my_amount": my_amount,
            "recent_cheques": recent,
            "expiring_soon": expiring_soon,
            "already_stale": already_stale,
            "today_created": today_created,
            "today_released": today_released,
            "today_amount": today_amount,
            "my_suppliers": my_suppliers,
            "my_radai": my_radai,
            "my_radai_amount": my_radai_amount,
            "avg_processing_days": avg_processing_days,
            "stale_cheques_list": stale_cheques_list,
            "stale_cheques_count": stale_cheques_count,
            "alerts": alerts,
            "my_month_labels": my_month_labels,
            "my_month_counts": my_month_counts,
            "my_month_amounts": my_month_amounts,
        })

        # 12-month sparkline data
        month_labels_12 = []
        month_counts_12 = []
        for i in range(11, -1, -1):
            year = now.year
            month = now.month - i
            if month <= 0:
                month += 12
                year -= 1
            month_name = calendar.month_abbr[month]
            month_labels_12.append(f"{month_name}")
            month_start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
            if month == 12:
                month_end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
            else:
                month_end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
            count = my_cheques.filter(created_at__gte=month_start, created_at__lt=month_end).count()
            month_counts_12.append(count)

        extra.update({
            "month_labels_12": month_labels_12,
            "month_counts_12": month_counts_12,
        })

    context = _page_context(request, is_admin=False, page_title="Dashboard", extra=extra)
    return render(request, "cashier/dashboard.html", context)


# ─────────────────────────────────────────────
# DASHBOARD CHART APIs (live data)
# ─────────────────────────────────────────────

@login_required
@require_GET
def admin_dashboard_chart_data(request):
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    import calendar
    from django.utils import timezone
    now = timezone.now()

    # Status counts
    status_data = {
        "draft": Cheque.objects.filter(status='draft').count(),
        "pending": Cheque.objects.filter(status='pending').count(),
        "released": Cheque.objects.filter(status='released').count(),
        "voided": Cheque.objects.filter(status='voided').count(),
        "stale": Cheque.objects.filter(status='stale').count(),
    }

    # Monthly data (last 6 months)
    month_labels = []
    month_counts = []
    month_amounts = []
    for i in range(5, -1, -1):
        year = now.year
        month = now.month - i
        if month <= 0:
            month += 12
            year -= 1
        month_name = calendar.month_abbr[month]
        month_labels.append(f"{month_name} {year}")
        month_start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
        if month == 12:
            month_end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
        else:
            month_end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
        count = Cheque.objects.filter(created_at__gte=month_start, created_at__lt=month_end).count()
        amount = Cheque.objects.filter(created_at__gte=month_start, created_at__lt=month_end, status='released').aggregate(t=Sum('amount'))['t'] or 0
        month_counts.append(count)
        month_amounts.append(float(amount))

    # Fund cluster data
    funds = list(FundCluster.objects.filter(is_active=True, balance__gt=0).order_by('-balance')[:8])
    fund_labels = [f.code for f in funds]
    fund_balances = [float(f.balance or 0) for f in funds]

    return JsonResponse({
        "status": status_data,
        "months": {"labels": month_labels, "counts": month_counts, "amounts": month_amounts},
        "funds": {"labels": fund_labels, "balances": fund_balances},
    })


@login_required
@require_GET
def admin_chart_released_data(request):
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    from django.utils import timezone
    from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear

    period = request.GET.get('period', 'monthly')
    now = timezone.now()

    if period == 'daily':
        start = now - timedelta(days=29)
        qs = Cheque.objects.filter(created_at__gte=start)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%b %d')
    elif period == 'weekly':
        start = now - timedelta(weeks=11)
        qs = Cheque.objects.filter(created_at__gte=start)
        annotate_fn = TruncWeek('created_at')
        label_fmt = lambda dt: dt.strftime('%b %d')
    elif period == 'yearly':
        start = now - timedelta(days=365*4)
        qs = Cheque.objects.filter(created_at__gte=start)
        annotate_fn = TruncYear('created_at')
        label_fmt = lambda dt: dt.strftime('%Y')
    else:
        start = now - timedelta(days=365)
        qs = Cheque.objects.filter(created_at__gte=start)
        annotate_fn = TruncMonth('created_at')
        label_fmt = lambda dt: dt.strftime('%b %Y')

    released = qs.annotate(period=annotate_fn).values('period').order_by('period').annotate(
        count=Count('id'),
        total=Sum('amount')
    )

    labels = []
    counts = []
    amounts = []
    for row in released:
        labels.append(label_fmt(row['period']))
        counts.append(row['count'])
        amounts.append(float(row['total'] or 0))

    return JsonResponse({
        "labels": labels,
        "counts": counts,
        "amounts": amounts,
        "period": period,
    })


@login_required
@require_GET
def admin_suppliers_chart_data(request):
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    from django.utils import timezone
    from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
    from datetime import timedelta

    period = request.GET.get('period', 'monthly')
    now = timezone.now()

    if period == 'weekly':
        start = now - timedelta(weeks=1)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%a %d') if dt else 'Unknown'
    elif period == 'yearly':
        start = now - timedelta(days=365)
        annotate_fn = TruncMonth('created_at')
        label_fmt = lambda dt: dt.strftime('%b %Y') if dt else 'Unknown'
    else:
        start = now - timedelta(days=30)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%b %d') if dt else 'Unknown'

    suppliers_qs = Supplier.objects.filter(created_at__gte=start)
    suppliers_data = suppliers_qs.annotate(period=annotate_fn).values('period').order_by('period').annotate(
        count=Count('id')
    )

    radai_qs = Radai.objects.filter(created_at__gte=start)
    radai_data = radai_qs.annotate(period=annotate_fn).values('period').order_by('period').annotate(
        count=Count('id')
    )

    all_periods = set()
    suppliers_map = {}
    radai_map = {}

    for row in suppliers_data:
        p = row['period']
        all_periods.add(p)
        suppliers_map[p] = row['count']

    for row in radai_data:
        p = row['period']
        all_periods.add(p)
        radai_map[p] = row['count']

    sorted_periods = sorted(all_periods)

    labels = []
    supplier_counts = []
    radai_counts = []
    for p in sorted_periods:
        labels.append(label_fmt(p))
        supplier_counts.append(suppliers_map.get(p, 0))
        radai_counts.append(radai_map.get(p, 0))

    return JsonResponse({
        "labels": labels,
        "supplier_counts": supplier_counts,
        "radai_counts": radai_counts,
        "period": period,
    })


@login_required
@require_GET
def next_supplier_sequences(request):
    """Return next auto-generated Check Serial, DV/PAYROLL and ORS/BURS numbers for suppliers."""
    from cashier.models import Supplier, Cheque
    import re

    # Find highest mr_or (check serial) - only pure numeric, exclude date formats
    highest_serial = 0
    for val in Supplier.objects.exclude(mr_or='').values_list('mr_or', flat=True):
        txt = str(val or '').strip()
        if txt.isdigit() and len(txt) >= 6:
            if len(txt) == 8 and (txt.startswith('19') or txt.startswith('20')):
                continue
            try:
                n = int(txt)
                if n > highest_serial:
                    highest_serial = n
            except ValueError:
                pass
    for val in Cheque.objects.exclude(cheque_number='').values_list('cheque_number', flat=True):
        txt = str(val or '').strip()
        if txt.isdigit() and len(txt) >= 6:
            if len(txt) == 8 and (txt.startswith('19') or txt.startswith('20')):
                continue
            try:
                n = int(txt)
                if n > highest_serial:
                    highest_serial = n
            except ValueError:
                pass
    next_serial = str(highest_serial + 1)

    from django.utils import timezone as _tz
    _today = _tz.localdate()
    dv_prefix = f"{_today.year}-{_today.month:02d}"
    ors_prefix = f"02-206441-{_today.year}-{_today.month:02d}"

    # Find highest DV/PAYROLL from suppliers raw_import (all months)
    highest_dv = 0
    dv_pattern = re.compile(r"^\d{4}-\d{2}-(\d+)$")
    for extras_json in Supplier.objects.values_list('raw_import', flat=True):
        extras = (extras_json or {}).get('supplier_extras') or {}
        val = str(extras.get('dv_payroll') or '').strip()
        m = dv_pattern.match(val)
        if m:
            try:
                n = int(m.group(1))
                if n > highest_dv:
                    highest_dv = n
            except ValueError:
                pass
    for val in Cheque.objects.exclude(dv_payroll_no='').values_list('dv_payroll_no', flat=True):
        m = dv_pattern.match(str(val or '').strip())
        if m:
            try:
                n = int(m.group(1))
                if n > highest_dv:
                    highest_dv = n
            except ValueError:
                pass
    next_dv = f"{dv_prefix}-{highest_dv + 1:04d}"

    # Find highest ORS/BURS from suppliers raw_import (all months)
    highest_ors = 0
    ors_pattern = re.compile(r"^02-206441-\d{4}-\d{2}-(\d+)$")
    for extras_json in Supplier.objects.values_list('raw_import', flat=True):
        extras = (extras_json or {}).get('supplier_extras') or {}
        val = str(extras.get('ors_burs') or '').strip()
        m = ors_pattern.match(val)
        if m:
            try:
                n = int(m.group(1))
                if n > highest_ors:
                    highest_ors = n
            except ValueError:
                pass
    for val in Cheque.objects.exclude(ors_burs_no='').values_list('ors_burs_no', flat=True):
        m = ors_pattern.match(str(val or '').strip())
        if m:
            try:
                n = int(m.group(1))
                if n > highest_ors:
                    highest_ors = n
            except ValueError:
                pass
    next_ors = f"{ors_prefix}-{highest_ors + 1:05d}"

    # Get last responsibility center
    last_resp = ''
    for extras_json in Supplier.objects.exclude(raw_import={}).order_by('-created_at', '-pk').values_list('raw_import', flat=True)[:10]:
        extras = (extras_json or {}).get('supplier_extras') or {}
        rc = str(extras.get('responsibility_center') or '').strip()
        if rc:
            last_resp = rc
            break

    return JsonResponse({
        "check_serial": next_serial,
        "dv_payroll": next_dv,
        "ors_burs": next_ors,
        "responsibility_center": last_resp,
    })


@login_required
@require_GET
def cashier_dashboard_chart_data(request):
    profile = _get_profile(request.user)
    is_guest = profile and profile.role == 'guest'
    if is_guest:
        return JsonResponse({"error": "Guest users cannot access chart data"}, status=403)

    import calendar
    from django.utils import timezone
    from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
    from datetime import timedelta
    now = timezone.now()

    my_cheques = Cheque.objects.all()

    # Status counts
    status_data = {
        "draft": my_cheques.filter(status='draft').count(),
        "pending": my_cheques.filter(status='pending').count(),
        "released": my_cheques.filter(status='released').count(),
        "voided": my_cheques.filter(status='voided').count(),
        "stale": my_cheques.filter(status='stale').count(),
    }

    # Activity data with period support
    period = request.GET.get('period', 'monthly')

    if period == 'daily':
        start = now - timedelta(days=6)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%a %d')
    elif period == 'weekly':
        start = now - timedelta(weeks=5)
        annotate_fn = TruncWeek('created_at')
        label_fmt = lambda dt: dt.strftime('%b %d')
    elif period == 'yearly':
        start = now - timedelta(days=365)
        annotate_fn = TruncMonth('created_at')
        label_fmt = lambda dt: dt.strftime('%b %Y')
    else:
        start = now - timedelta(days=179)
        annotate_fn = TruncMonth('created_at')
        label_fmt = lambda dt: dt.strftime('%b %Y')

    # Build period buckets
    all_periods = set()

    cheque_data = my_cheques.filter(created_at__gte=start).annotate(
        period=annotate_fn
    ).values('period').order_by('period').annotate(
        count=Count('id')
    )

    amount_data = my_cheques.filter(created_at__gte=start, status='released').annotate(
        period=annotate_fn
    ).values('period').order_by('period').annotate(
        total=Sum('amount')
    )

    cheque_map = {}
    amount_map = {}

    for row in cheque_data:
        p = row['period']
        all_periods.add(p)
        cheque_map[p] = row['count']

    for row in amount_data:
        p = row['period']
        all_periods.add(p)
        amount_map[p] = float(row['total'] or 0)

    sorted_periods = sorted(all_periods)

    month_labels = []
    month_counts = []
    month_amounts = []
    for p in sorted_periods:
        month_labels.append(label_fmt(p))
        month_counts.append(cheque_map.get(p, 0))
        month_amounts.append(amount_map.get(p, 0))

    return JsonResponse({
        "status": status_data,
        "months": {"labels": month_labels, "counts": month_counts, "amounts": month_amounts},
    })


@login_required
@require_GET
def cashier_suppliers_chart_data(request):
    profile = _get_profile(request.user)
    is_guest = profile and profile.role == 'guest'
    if is_guest:
        return JsonResponse({"error": "Guest users cannot access chart data"}, status=403)

    from django.utils import timezone
    from django.db.models.functions import TruncDate, TruncMonth
    from cashier.models import Supplier, Radai
    from datetime import timedelta

    period = request.GET.get('period', 'monthly')
    now = timezone.now()

    if period == 'weekly':
        start = now - timedelta(weeks=1)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%a %d') if dt else 'Unknown'
    elif period == 'yearly':
        start = now - timedelta(days=365)
        annotate_fn = TruncMonth('created_at')
        label_fmt = lambda dt: dt.strftime('%b %Y') if dt else 'Unknown'
    else:
        start = now - timedelta(days=30)
        annotate_fn = TruncDate('created_at')
        label_fmt = lambda dt: dt.strftime('%b %d') if dt else 'Unknown'

    suppliers_qs = Supplier.objects.filter(created_at__gte=start)
    suppliers_data = suppliers_qs.annotate(period=annotate_fn).values('period').order_by('period').annotate(
        count=Count('id')
    )

    radai_qs = Radai.objects.filter(created_at__gte=start)
    radai_data = radai_qs.annotate(period=annotate_fn).values('period').order_by('period').annotate(
        count=Count('id')
    )

    all_periods = set()
    suppliers_map = {}
    radai_map = {}

    for row in suppliers_data:
        p = row['period']
        all_periods.add(p)
        suppliers_map[p] = row['count']

    for row in radai_data:
        p = row['period']
        all_periods.add(p)
        radai_map[p] = row['count']

    sorted_periods = sorted(all_periods)

    labels = []
    supplier_counts = []
    radai_counts = []
    for p in sorted_periods:
        labels.append(label_fmt(p))
        supplier_counts.append(suppliers_map.get(p, 0))
        radai_counts.append(radai_map.get(p, 0))

    return JsonResponse({
        "labels": labels,
        "supplier_counts": supplier_counts,
        "radai_counts": radai_counts,
        "period": period,
    })

@login_required
def admin_transactions(request):
    if not _is_admin(request.user):
        return redirect("cashier_transactions")
    transactions = Transaction.objects.order_by("-date")
    context = _page_context(request, is_admin=True, page_title="Transactions", extra={"transactions": transactions})
    return render(request, "admin_panel/transactions.html", context)


@login_required
def cashier_transactions(request):
    transactions = Transaction.objects.order_by("-date")[:25]
    context = _page_context(request, is_admin=False, page_title="My Transactions", extra={"transactions": transactions})
    return render(request, "cashier/transactions.html", context)


# ─────────────────────────────────────────────
# FUND CLUSTERS
# ─────────────────────────────────────────────

@login_required
def fund_clusters(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None

    if request.method == "POST" and is_admin:
        action = request.POST.get('action', 'create')
        if action == 'delete':
            password = request.POST.get('password', '').strip()
            is_valid = False
            for admin in User.objects.filter(is_superuser=True, is_active=True):
                if admin.check_password(password):
                    is_valid = True
                    break
            if not is_valid:
                error = "Incorrect admin password. Fund cluster deletion denied."
            else:
                fc_id = request.POST.get('fc_id')
                fc = get_object_or_404(FundCluster, pk=fc_id)
                fc.delete()
                _audit(request.user, "Deleted fund cluster", {"id": fc_id, "name": fc.name})
                return redirect('fund_clusters')
        elif action == 'edit':
            fc_id = request.POST.get('fc_id')
            fc = get_object_or_404(FundCluster, pk=fc_id)
            fc.code = request.POST.get('code', '').strip()
            fc.name = request.POST.get('name', '').strip()
            fc.description = request.POST.get('description', '').strip()
            fc.bank_name = request.POST.get('bank_name', '').strip()
            fc.account_number = request.POST.get('account_number', '').strip()
            fc.is_active = request.POST.get('is_active') == 'on'
            fc.save()
            _audit(request.user, "Edited fund cluster", {"id": fc_id})
            success = f"Fund cluster '{fc.name}' updated."
        else:
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            desc = request.POST.get('description', '').strip()
            if not code or not name:
                error = "Code and name are required."
            elif FundCluster.objects.filter(code=code).exists():
                error = "Fund cluster code already exists."
            else:
                fc = FundCluster.objects.create(
                    code=code,
                    name=name,
                    description=desc,
                    balance=0,
                    bank_name=request.POST.get('bank_name', '').strip(),
                    account_number=request.POST.get('account_number', '').strip()
                )
                _audit(request.user, "Created fund cluster", {"id": fc.pk, "code": code})
                success = f"Fund cluster '{name}' created."

    clusters = FundCluster.objects.annotate(
        cheque_count=Count('cheque', distinct=True),
        released_count=Count('cheque', filter=Q(cheque__status='released'), distinct=True),
        total_released=Sum('cheque__amount', filter=Q(cheque__status='released')),
    ).order_by('code')
    context = _page_context(request, is_admin=is_admin, page_title="Fund Clusters", extra={
        "clusters": clusters,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/fund_clusters.html", context)


def _parse_money(value, fallback=Decimal('0')):
    try:
        clean_val = str(value or '0').replace(',', '').replace('₱', '').replace('\u20B1', '').strip()
        return Decimal(clean_val).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(fallback).quantize(Decimal('0.01'))


def _fund_balance_error(fund_cluster, amount):
    if not fund_cluster:
        return "Please select a fund cluster before releasing the cheque."
    if amount <= 0:
        return "Cheque amount must be greater than zero."
    return None


def _adjust_fund_balance(fund_cluster, delta):
    pass


def _cheque_is_released(cheque):
    return cheque and cheque.status == 'released'


def _cheque_action_block_reason(cheque):
    if not cheque:
        return None
    if not cheque.fund_cluster_id:
        return None
    return _fund_balance_error(cheque.fund_cluster, cheque.amount)


# ─────────────────────────────────────────────
# SUPPLIERS
# ─────────────────────────────────────────────

SUPPLIER_IMPORT_COLUMNS = [
    ('mr_or', 'Check Serial'),
    ('date', 'Check Date'),
    ('or_number', 'OR Number'),
    ('series_day', 'Series/Day'),
    ('dv_payroll', 'DV/Payroll No.'),
    ('ors_burs', 'ORS/BURS No.'),
    ('responsibility_center', 'Responsibility Center'),
    ('uacs', 'UACS Object Code'),
    ('account_name', 'Payee'),
    ('nature_of_collections', 'Nature of Payments'),
    ('gross_amount', 'Gross Amount'),
    ('professional_tax', 'Professional Tax'),
    ('tax_5', 'Tax (5%/3%)'),
    ('tax_2', 'Tax (2%/1%)'),
    ('amount', 'Net Amount'),
    ('fund_cluster', 'Fund Cluster'),
    ('remarks', 'Remarks'),
    ('other_deductions', 'Other Deductions'),
]


RADAI_IMPORT_COLUMNS = [
    ('date', 'Date'),
    ('mr_or', 'Serial No.'),
    ('dv_payroll', 'DV/Payroll No.'),
    ('reference_code', 'ORS/BURS No.'),
    ('responsibility_center', 'Responsibility Center Code'),
    ('account_name', 'Payee'),
    ('uacs', 'UACS Object Code'),
    ('nature_of_collections', 'Nature of Payment'),
    ('amount', 'Amount'),
]


def _supplier_import_headers():
    return [label for _, label in SUPPLIER_IMPORT_COLUMNS]


def _radai_import_headers():
    return [label for _, label in RADAI_IMPORT_COLUMNS]


def _supplier_import_row_values(row):
    def _norm(value):
        return str(value or '').strip()

    def _normalize_key(value):
        return ''.join(ch for ch in str(value or '').strip().lower() if ch.isalnum())

    def _get_value(data, *keys, default=''):
        normalized = {_normalize_key(key): value for key, value in data.items() if not str(key).startswith('_')}
        for key in keys:
            lookup = _normalize_key(key)
            if lookup in normalized and _norm(normalized[lookup]):
                return _norm(normalized[lookup])
        return _norm(default)

    raw_values = row.get('_values', []) if isinstance(row, dict) else []
    ordered_values = [_norm(value) for value in raw_values]
    headers = row.get('_headers', []) or []
    values = {}

    for index, (field_name, header_name) in enumerate(SUPPLIER_IMPORT_COLUMNS):
        aliases = [field_name, header_name, header_name.lower(), header_name.replace('/', ' ')]
        if field_name == 'or_number':
            aliases.extend(['or number', 'or_number', 'account_number', 'account number'])
        elif field_name == 'account_name':
            aliases.extend(['payee', 'supplier_name', 'supplier name', 'vendor', 'name'])
        elif field_name == 'date':
            aliases.extend(['check date', 'check_date', 'date'])
        elif field_name == 'amount':
            aliases.extend(['net amount', 'net_amount', 'amount'])
        elif field_name == 'fund_cluster':
            aliases.extend([
                'fund cluster', 'fund_cluster', 'cluster', 'fund',
                'fc', 'fc code', 'fc_code', 'fc id', 'fc_id',
                'fund code', 'fund_code', 'fund cluster code', 'fund_cluster_code',
                'fund cluster id', 'fund_cluster_id', 'cluster code', 'cluster_code',
                'cluster id', 'cluster_id'
            ])
        elif field_name == 'other_deductions':
            aliases.extend(['other deductions', 'other_deductions', 'deductions', 'deduction'])


        val = _get_value(row, *aliases, default='')
        if not val:
            if not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS):
                val = ordered_values[index] if index < len(ordered_values) else ''
        values[field_name] = val

    # Backward compatibility for old columns
    extra_fields = {
        'mr': ['mr', 'MR'],
        'code_or': ['code_or', 'CODE OR'],
        'code_or2': ['code_or2', 'CODE OR 2', 'code or 2'],
        'series_month': ['series_month', 'SERIES/MONTH', 'series/month'],
        'code_line': ['code_line', 'CODE LINE', 'code line'],
        'line': ['line', 'LINE'],
        'code_noc': ['code_noc', 'CODE NOC', 'code noc'],
        'address': ['address', 'ADDRESS'],
        'tin': ['tin', 'TIN'],
        'status': ['status', 'STATUS'],
    }
    for field_name, aliases in extra_fields.items():
        if field_name not in values:
            values[field_name] = _get_value(row, *aliases, default='')

    values['account_name'] = values['account_name'] or _get_value(row, 'payee', 'supplier_name', 'supplier name', 'vendor', 'name')
    if not values['account_name'] and (not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS)):
        values['account_name'] = ordered_values[8] if len(ordered_values) > 8 else ''

    values['or_number'] = values['or_number'] or _get_value(row, 'or number', 'or_number', 'account_number', 'account number')
    if not values['or_number'] and (not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS)):
        values['or_number'] = ordered_values[2] if len(ordered_values) > 2 else ''

    values['date'] = values['date'] or _get_value(row, 'check date', 'check_date', 'date')
    if not values['date'] and (not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS)):
        values['date'] = ordered_values[1] if len(ordered_values) > 1 else ''

    values['amount'] = values['amount'] or _get_value(row, 'net amount', 'net_amount', 'amount')
    if not values['amount'] and (not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS)):
        values['amount'] = ordered_values[14] if len(ordered_values) > 14 else ''

    values['other_deductions'] = values.get('other_deductions') or _get_value(row, 'other deductions', 'other_deductions', 'deductions', 'deduction')
    if not values.get('other_deductions') and (not headers or len(headers) == len(SUPPLIER_IMPORT_COLUMNS)):
        values['other_deductions'] = ordered_values[17] if len(ordered_values) > 17 else ''

    return values


def _supplier_import_raw_import(row, sheet_name=''):
    return {
        'sheet': sheet_name or (row.get('_sheet', '') if isinstance(row, dict) else ''),
        'row_number': row.get('_row_number') if isinstance(row, dict) else None,
        'headers': row.get('_headers', []) if isinstance(row, dict) else [],
        'columns': row.get('_headers', []) if isinstance(row, dict) else [],
        'values': row.get('_values', []) if isinstance(row, dict) else [],
        'cells': row.get('_ordered_cells', []) if isinstance(row, dict) else [],
        'data': {k: v for k, v in row.items() if not str(k).startswith('_')} if isinstance(row, dict) else {},
    }


def _parse_supplier_date_value(value):
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    try:
        serial_value = float(text)
    except ValueError:
        serial_value = None
    if serial_value is not None:
        try:
            from openpyxl.utils.datetime import from_excel
            converted = from_excel(serial_value)
            return converted.date() if isinstance(converted, datetime) else converted
        except Exception:
            try:
                return (date(1899, 12, 30) + timedelta(days=serial_value))
            except Exception:
                pass

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError:
        pass

    for pattern in ('%Y-%b-%d', '%Y-%B-%d', '%b %d, %Y', '%B %d, %Y', '%m/%d/%Y', '%Y/%m/%d', '%m-%d-%Y', '%d-%b-%Y'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue

    return None

def _build_registry_context(request, is_admin, selected_cashier):
    cashier_options = Profile.objects.filter(role='cashier').select_related('user').order_by('user__username')
    supplier_list = Supplier.objects.select_related('created_by').order_by('account_name')
    if is_admin and selected_cashier:
        supplier_list = supplier_list.filter(created_by__username=selected_cashier)
    elif not is_admin:
        supplier_list = supplier_list.filter(created_by=request.user)

    sheet_columns = []
    supplier_rows = []

    def _normalize_column_name(value):
        return str(value or '').strip().lower().replace(' ', '_')

    def _display_value(value):
        if value is None:
            return ''
        if isinstance(value, list):
            return ', '.join(_display_value(item) for item in value if _display_value(item))
        if isinstance(value, dict):
            return ', '.join(f'{key}: {_display_value(item)}' for key, item in value.items() if _display_value(item))
        return str(value)

    raw_supplier_rows = []
    schema_source = None
    for supplier in supplier_list:
        raw_import = supplier.raw_import or {}
        raw_data = raw_import.get('data') or {}
        raw_cells = raw_import.get('cells') or []
        headers = raw_import.get('headers') or raw_import.get('columns') or []

        if schema_source is None and (raw_cells or headers):
            schema_source = raw_import

        raw_supplier_rows.append({
            'supplier': supplier,
            'row_number': raw_import.get('row_number'),
            'sheet': raw_import.get('sheet', ''),
            'data': raw_data,
            'cells': raw_cells,
        })

    if schema_source:
        source_cells = schema_source.get('cells') or []
        source_headers = schema_source.get('headers') or schema_source.get('columns') or []

        if source_cells:
            sheet_columns = [str(cell.get('header') or '').strip() or f'Column {idx}' for idx, cell in enumerate(source_cells, start=1)]
        elif source_headers:
            sheet_columns = [str(header).strip() or f'Column {idx}' for idx, header in enumerate(source_headers, start=1)]

    if not sheet_columns:
        sheet_columns = _supplier_import_headers()

    def _column_lookup_key(value):
        return ''.join(ch for ch in str(value or '').strip().lower() if ch.isalnum())

    def _excel_column_letter(index):
        letters = ''
        while index:
            index, remainder = divmod(index - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    column_letters = [_excel_column_letter(i) for i in range(1, len(sheet_columns) + 1)]

    def _pick_cell_value(supplier, data, column_name):
        value = data.get(column_name)
        if value not in (None, ''):
            return value

        normalized = column_name.strip().lower().replace(' ', '_').replace('/', '_')
        extras = {}
        if supplier.raw_import and isinstance(supplier.raw_import, dict):
            extras = supplier.raw_import.get('supplier_extras') or {}
            if not isinstance(extras, dict):
                extras = {}

        fallback_map = {
            'check_serial': supplier.mr_or,
            'mr_or': supplier.mr_or,
            'check_date': supplier.date.isoformat() if supplier.date else '',
            'date': supplier.date.isoformat() if supplier.date else '',
            'or_number': supplier.or_number,
            'series_day': supplier.series_day,
            'dv_payroll_no': extras.get('dv_payroll', ''),
            'dv_payroll': extras.get('dv_payroll', ''),
            'ors_burs_no': extras.get('ors_burs', ''),
            'ors_burs': extras.get('ors_burs', ''),
            'responsibility_center': extras.get('responsibility_center', ''),
            'uacs_object_code': extras.get('uacs', ''),
            'uacs': extras.get('uacs', ''),
            'payee': supplier.account_name,
            'account_name': supplier.account_name,
            'nature_of_payments': supplier.nature_of_collections,
            'nature_of_collections': supplier.nature_of_collections,
            'net_amount': f'{supplier.amount:.2f}',
            'amount': f'{supplier.amount:.2f}',
            'professional_tax': extras.get('professional_tax', ''),
            'tax_5_3': extras.get('tax_5', ''),
            'tax_5': extras.get('tax_5', ''),
            'tax_3_1': extras.get('tax_2', ''),
            'tax_2': extras.get('tax_2', ''),
            'gross_amount': extras.get('gross_amount', ''),
            'remarks': supplier.remarks,
            'address': supplier.address,
            'tin': supplier.tin,
            'status': supplier.get_status_display(),
        }
        return fallback_map.get(normalized, '')

    for row in raw_supplier_rows:
        raw_cells = row.get('cells') or []
        if raw_cells:
            row['cells'] = [_display_value(raw_cells[idx].get('value')) if idx < len(raw_cells) else '' for idx in range(len(sheet_columns))]
        else:
            row['cells'] = [_pick_cell_value(row['supplier'], row['data'], column) for column in sheet_columns]
        supplier_rows.append(row)

    series_month_index = next((idx for idx, column in enumerate(sheet_columns) if _column_lookup_key(column) in {'seriesmonth', 'seriesmonth', 'seriesmon'}), None)
    series_day_index = next((idx for idx, column in enumerate(sheet_columns) if _column_lookup_key(column) in {'seriesday', 'seriesday', 'seriesdy'}), None)

    def _sort_number(value):
        text = _display_value(value).strip()
        if not text:
            return (1, 0, '')
        cleaned = ''.join(ch for ch in text if ch.isdigit() or ch == '.')
        if cleaned:
            try:
                return (0, float(cleaned), text)
            except Exception:
                pass
        return (0, text.lower(), text)

    if series_month_index is not None or series_day_index is not None:
        def _registry_sort_key(row):
            month_value = row['cells'][series_month_index] if series_month_index is not None and series_month_index < len(row['cells']) else ''
            day_value = row['cells'][series_day_index] if series_day_index is not None and series_day_index < len(row['cells']) else ''
            return (_sort_number(month_value), _sort_number(day_value), _sort_number(row.get('row_number')))

        supplier_rows.sort(key=_registry_sort_key)

    for index, row in enumerate(supplier_rows, start=1):
        row['row_number'] = index

    return _page_context(request, is_admin=is_admin, page_title="Registry", extra={
        "supplier_list": supplier_list,
        "supplier_rows": supplier_rows,
        "sheet_columns": sheet_columns,
        "cashier_options": cashier_options,
        "selected_cashier": selected_cashier,
        "column_letters": column_letters,
    })


def _save_registry_sheet(payload, user):
    def _normalize_column_name(value):
        return ''.join(ch for ch in str(value or '').strip().lower() if ch.isalnum())

    def _display_value(value):
        if value is None:
            return ''
        if isinstance(value, (list, tuple)):
            return ', '.join(_display_value(item) for item in value if _display_value(item))
        if isinstance(value, dict):
            return ', '.join(f'{key}: {_display_value(item)}' for key, item in value.items() if _display_value(item))
        return str(value).strip()

    headers = [str(header).strip() for header in (payload.get('headers') or [])]
    if not headers:
        raise ValueError('Registry sheet is missing column headers.')

    rows = payload.get('rows') or []
    if not isinstance(rows, list):
        raise ValueError('Registry sheet rows must be a list.')

    saved_count = 0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue

        cell_values = row.get('cells') or []
        if not isinstance(cell_values, list):
            raise ValueError('Registry row cells must be a list.')

        ordered_cells = []
        row_data = {}
        for cell_index, header in enumerate(headers):
            value = _display_value(cell_values[cell_index]) if cell_index < len(cell_values) else ''
            row_data[header] = value
            ordered_cells.append({'header': header, 'value': value})

        supplier_id = row.get('supplier_id') or row.get('id')
        supplier = None
        if supplier_id not in (None, '', 'null'):
            supplier = Supplier.objects.filter(pk=supplier_id).first()

        non_empty_values = [value for value in (_display_value(value) for value in cell_values) if value]
        parsed = _supplier_import_row_values({
            '_values': [_display_value(value) for value in cell_values],
            '_headers': headers,
            '_ordered_cells': ordered_cells,
            **row_data,
        })
        account_name = parsed.get('account_name', '') or (supplier.account_name if supplier else '')
        if not account_name and non_empty_values:
            account_name = non_empty_values[0]
        if not account_name:
            account_name = f'Registry Row {index}'

        account_number = parsed.get('or_number', '')
        try:
            amount = Decimal(parsed.get('amount', '') or '0').quantize(Decimal('0.01'))
        except InvalidOperation:
            amount = Decimal('0.00')
        parsed_date = _parse_supplier_date_value(parsed.get('date')) or _parse_supplier_date_value(parsed.get('mr')) or _parse_supplier_date_value(parsed.get('mr_or'))

        raw_import = {
            'sheet': payload.get('sheet_name') or 'Registry',
            'row_number': index,
            'headers': headers,
            'columns': headers,
            'values': [_display_value(value) for value in cell_values],
            'cells': ordered_cells,
            'data': row_data,
        }

        if supplier is None:
            if not any(value for value in non_empty_values):
                continue
            supplier = Supplier.objects.create(
                account_name=account_name,
                account_number=account_number,
                mr_or=parsed.get('mr_or', ''),
                mr=parsed.get('mr', ''),
                code_or=parsed.get('code_or', ''),
                code_or2=parsed.get('code_or2', ''),
                series_month=parsed.get('series_month', ''),
                date=parsed_date,
                or_number=account_number,
                series_day=parsed.get('series_day', ''),
                code_line=parsed.get('code_line', ''),
                line=parsed.get('line', ''),
                code_noc=parsed.get('code_noc', ''),
                nature_of_collections=parsed.get('nature_of_collections', ''),
                amount=amount,
                remarks=parsed.get('remarks', ''),
                address=parsed.get('address', ''),
                tin=parsed.get('tin', ''),
                status=parsed.get('status', 'active'),
                created_by=user,
                raw_import=raw_import,
            )
        else:
            supplier.account_name = account_name
            supplier.account_number = account_number
            supplier.mr_or = parsed.get('mr_or', '')
            supplier.mr = parsed.get('mr', '')
            supplier.code_or = parsed.get('code_or', '')
            supplier.code_or2 = parsed.get('code_or2', '')
            supplier.series_month = parsed.get('series_month', '')
            supplier.date = parsed_date
            supplier.or_number = account_number
            supplier.series_day = parsed.get('series_day', '')
            supplier.code_line = parsed.get('code_line', '')
            supplier.line = parsed.get('line', '')
            supplier.code_noc = parsed.get('code_noc', '')
            supplier.nature_of_collections = parsed.get('nature_of_collections', '')
            supplier.amount = amount
            supplier.remarks = parsed.get('remarks', '')
            supplier.address = parsed.get('address', '')
            supplier.tin = parsed.get('tin', '')
            supplier.status = parsed.get('status', 'active')
            if not supplier.created_by:
                supplier.created_by = user
            supplier.raw_import = raw_import
            supplier.save()

        saved_count += 1

    return saved_count


@login_required
def suppliers(request):
    is_admin = _is_admin(request.user)
    profile = _get_profile(request.user)
    error = None
    success = None
    period_key = request.GET.get('period', 'all').strip()
    period_type = 'all'
    period_value = ''
    if '|' in period_key:
        period_type, period_value = period_key.split('|', 1)
        period_type = period_type.strip().lower()
        period_value = period_value.strip()

    def _split_noc_pair(code_value, nature_value=''):
        code_text = str(code_value or '').strip()
        nature_text = str(nature_value or '').strip()
        if code_text and '-' in code_text:
            prefix, suffix = code_text.split('-', 1)
            prefix = prefix.strip()
            suffix = suffix.strip()
            if prefix.isdigit():
                code_text = prefix
                if suffix and not nature_text:
                    nature_text = suffix
        return code_text, nature_text

    def _auto_supplier_series(today=None):
        series_date = today or timezone.localdate()
        series_qs = Supplier.objects.all()
        # compute series within the current user's flow (match cashier behavior)
        series_qs = series_qs.filter(created_by=request.user)

        def _series_value(value):
            text = str(value or '').strip()
            if not text:
                return None
            if not text.isdigit():
                return None
            try:
                return int(text)
            except ValueError:
                return None

        existing_series = [
            _series_value(value)
            for value in series_qs.exclude(series_month='').values_list('series_month', flat=True)
        ]
        existing_numbers = [value for value in existing_series if value is not None]
        next_series_month = (max(existing_numbers) if existing_numbers else series_qs.count()) + 1
        existing_count = series_qs.filter(created_at__date=series_date).count()
        return str(next_series_month), str(existing_count + 1)

    def _parse_supplier_form(post):
        check_serial = post.get('check_serial', '').strip() or post.get('mr_or', '').strip()
        payee = post.get('payee', '').strip() or post.get('account_name', '').strip()
        or_number = post.get('or_number', '').strip() or post.get('account_number', '').strip()
        amount_raw = post.get('amount', '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
        try:
            amount = Decimal(amount_raw or '0').quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            amount = Decimal('0.00')

        supplier_date = post.get('date', '').strip()
        parsed_date = None
        if supplier_date:
            try:
                parsed_date = date.fromisoformat(supplier_date)
            except ValueError:
                parsed_date = None

        code_noc, nature_of_collections = _split_noc_pair(
            post.get('code_noc', '').strip(),
            post.get('nature_of_payments', '').strip() or post.get('nature_of_collections', '').strip(),
        )

        return {
            'mr_or': check_serial,
            'mr': '',
            'code_or': '',
            'code_or2': '',
            'date': parsed_date,
            'or_number': or_number,
            'account_number': or_number,
            'account_name': payee,
            'code_line': '',
            'line': '',
            'code_noc': '',
            'nature_of_collections': nature_of_collections,
            'amount': amount,
            'remarks': post.get('remarks', '').strip() or post.get('remarks_text', '').strip(),
            'address': post.get('address', '').strip(),
            'tin': post.get('tin', '').strip(),
            'status': post.get('status', 'active').strip(),
        }

    def _parse_supplier_extras(post):
        deductions_raw = post.get('other_deductions', '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
        try:
            deductions = str(Decimal(deductions_raw or '0').quantize(Decimal('0.01')))
        except (InvalidOperation, ValueError):
            deductions = '0.00'

        def _parse_optional_decimal(name):
            raw_value = post.get(name, '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
            if not raw_value:
                return ''
            try:
                return str(Decimal(raw_value).quantize(Decimal('0.01')))
            except (InvalidOperation, ValueError):
                return ''

        amount_raw = post.get('amount', '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
        try:
            base_amount = Decimal(amount_raw or '0').quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            base_amount = Decimal('0.00')

        professional_tax_rate_raw = post.get('professional_tax_rate', '').strip().replace('%', '')
        try:
            professional_tax_rate = str(Decimal(professional_tax_rate_raw).quantize(Decimal('0.01'))) if professional_tax_rate_raw else ''
        except (InvalidOperation, ValueError):
            professional_tax_rate = ''

        try:
            pro_rate_decimal = (Decimal(professional_tax_rate) / Decimal('100')) if professional_tax_rate else Decimal('0')
        except (InvalidOperation, ValueError):
            pro_rate_decimal = Decimal('0')

        professional_tax_fixed_raw = post.get('professional_tax', '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
        try:
            professional_tax_fixed = Decimal(professional_tax_fixed_raw).quantize(Decimal('0.01')) if professional_tax_fixed_raw else Decimal('0.00')
        except (InvalidOperation, ValueError):
            professional_tax_fixed = Decimal('0.00')

        if not professional_tax_rate:
            net_plus_fixed = base_amount + Decimal(deductions) + professional_tax_fixed
        else:
            net_plus_fixed = base_amount + Decimal(deductions)



        def _resolve_tax_rates(option_name, default_rates):
            if not option_name:
                return default_rates
            opt = ManagementOption.objects.filter(
                category=ManagementOption.CATEGORY_TAX,
                value=option_name,
                is_active=True
            ).first()
            if opt:
                return _get_tax_rates(opt.uacs)
            # Try to lookup by rate string itself
            opt_by_rate = ManagementOption.objects.filter(
                category=ManagementOption.CATEGORY_TAX,
                uacs=option_name,
                is_active=True
            ).first()
            if opt_by_rate:
                return _get_tax_rates(opt_by_rate.uacs)
            return _get_tax_rates(option_name)

        active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
        t1 = active_taxes[0] if len(active_taxes) > 0 else None
        t2 = active_taxes[1] if len(active_taxes) > 1 else None

        t1_rate = t1.uacs if t1 else '5%/3%'
        t2_rate = t2.uacs if t2 else '2%/1%'

        t1_vat, t1_nv = _get_tax_rates(t1_rate)
        t2_vat, t2_nv = _get_tax_rates(t2_rate)

        # Check for custom tax rates from POST
        is_vat = bool(post.get('is_vat'))
        if is_vat:
            custom_tax5_rate = post.get('tax_5_rate_vat', '').strip().replace('%', '')
            custom_tax2_rate = post.get('tax_2_rate_vat', '').strip().replace('%', '')
        else:
            custom_tax5_rate = post.get('tax_5_rate_non_vat', '').strip().replace('%', '')
            custom_tax2_rate = post.get('tax_2_rate_non_vat', '').strip().replace('%', '')

        if custom_tax5_rate:
            try:
                rate_val = Decimal(custom_tax5_rate) / Decimal('100')
                if is_vat:
                    t1_vat = rate_val
                else:
                    t1_nv = rate_val
            except (InvalidOperation, ValueError):
                pass
        if custom_tax2_rate:
            try:
                rate_val = Decimal(custom_tax2_rate) / Decimal('100')
                if is_vat:
                    t2_vat = rate_val
                else:
                    t2_nv = rate_val
            except (InvalidOperation, ValueError):
                pass

        if not post.get('is_vat'):
            # Non-VAT mode: dynamic taxes are NOT calculated!
            denom = Decimal('1.0') - pro_rate_decimal
            if denom <= 0:
                denom = Decimal('1.0')
            base = (net_plus_fixed / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            tax_5 = '0.00'
            tax_2 = '0.00'
            tax_base_5_3 = '0.00'
            tax_base_3_1 = '0.00'
            if pro_rate_decimal > 0:
                professional_tax_amount = str((base * pro_rate_decimal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            else:
                professional_tax_amount = str(professional_tax_fixed)
        else:
            # VAT mode: Factors in 1.12 Gross addition, subtracts all tax rates
            denom = Decimal('1.12') - (t1_vat + t2_vat + pro_rate_decimal)
            if denom <= 0:
                denom = Decimal('1.12')
            base = (net_plus_fixed / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            gross = (base * Decimal('1.12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            # Use submitted tax_5 and tax_2 if present, otherwise recalculate
            tax_5_submitted = post.get('tax_5', '').strip()
            if tax_5_submitted:
                tax_5 = str(_parse_money(tax_5_submitted))
            else:
                tax_5 = str((base * t1_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

            tax_2_submitted = post.get('tax_2', '').strip()
            if tax_2_submitted:
                tax_2 = str(_parse_money(tax_2_submitted))
            else:
                tax_2 = str((base * t2_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

            tax_base_5_3 = str(base)
            tax_base_3_1 = str(base)
            if pro_rate_decimal > 0:
                professional_tax_amount = str((base * pro_rate_decimal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            else:
                professional_tax_amount = str(professional_tax_fixed)

        gross_amount = str((base_amount + Decimal(tax_5) + Decimal(tax_2) + Decimal(professional_tax_amount) + Decimal(deductions)).quantize(Decimal('0.01')))

        return {
            'account_title': post.get('account_title', '').strip(),
            'fund_cluster': post.get('fund_cluster', '').strip(),
            'fund_cluster_id': post.get('fund_cluster_id', '').strip(),
            'cashier_name': post.get('cashier_name', '').strip() or _full_name_or_username(request.user),
            'approval': post.get('approval', '').strip(),
            'other_deductions': deductions,
            'is_vat': 'true' if post.get('is_vat') else 'false',
            'remarks_text': post.get('remarks_text', '').strip() or post.get('remarks', '').strip(),
            'dv_payroll': post.get('dv_payroll', '').strip(),
            'ors_burs': post.get('ors_burs', '').strip(),
            'responsibility_center': post.get('responsibility_center', '').strip(),
            'uacs': post.get('uacs', '').strip(),
            'professional_tax_rate': professional_tax_rate,
            'professional_tax': professional_tax_amount,
            'tax_5': tax_5,
            'tax_2': tax_2,
            'tax_base_5_3': tax_base_5_3,
            'tax_base_3_1': tax_base_3_1,
            'tax_5_rate_vat': post.get('tax_5_rate_vat', '').strip(),
            'tax_5_rate_non_vat': post.get('tax_5_rate_non_vat', '').strip(),
            'tax_2_rate_vat': post.get('tax_2_rate_vat', '').strip(),
            'tax_2_rate_non_vat': post.get('tax_2_rate_non_vat', '').strip(),
            'gross_amount': gross_amount,
        }

    def _parse_custom_columns(post):
        raw_value = post.get('custom_columns_json', '').strip()
        if not raw_value:
            return {}
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}

        cleaned = {}
        for key, value in payload.items():
            cleaned[str(key)] = '' if value is None else str(value).strip()
        return cleaned

    def _code_or2_for_date(target_date, exclude_supplier_pk=None):
        if not target_date:
            return ''
        date_qs = Supplier.objects.select_related('created_by')
        date_qs = date_qs.filter(date=target_date)
        if exclude_supplier_pk:
            date_qs = date_qs.exclude(pk=exclude_supplier_pk)
        day_count = date_qs.count()
        return f"{target_date.month}-{target_date.day}-{day_count + 1}"

    today = timezone.localdate()

    if request.method == "POST":
        action = request.POST.get('action', 'create')
        if action == 'delete':
            from cashier.models import DEFAULT_ROLE_PERMISSIONS
            role_config = DEFAULT_ROLE_PERMISSIONS.get(profile.role if profile else 'guest', {"permissions": {}})
            can_delete = is_admin or 'delete' in role_config.get('permissions', {}).get('suppliers', [])
            if not can_delete:
                error = "You do not have permission to delete suppliers."
            else:
                password = request.POST.get('password', '').strip()
                is_valid = False
                if request.user.check_password(password):
                    is_valid = True
                else:
                    for admin in User.objects.filter(is_superuser=True, is_active=True):
                        if admin.check_password(password):
                            is_valid = True
                            break
                if not is_valid:
                    error = "Incorrect password. Supplier deletion denied."
                else:
                    sup_id = request.POST.get('sup_id')
                    sup = get_object_or_404(Supplier, pk=sup_id)
                    sup.delete()
                    _audit(request.user, "Deleted supplier", {"id": sup_id})
                    return redirect('suppliers')
        elif action == 'edit':
            sup_id = request.POST.get('sup_id')
            sup = get_object_or_404(Supplier, pk=sup_id)
            if not is_admin and sup.created_by != request.user:
                error = "You can only edit your own suppliers."
            else:
                data = _parse_supplier_form(request.POST)
                custom_columns = _parse_custom_columns(request.POST)
                supplier_extras = _parse_supplier_extras(request.POST)
                data['code_or2'] = _code_or2_for_date(data['date'] or sup.date, exclude_supplier_pk=sup.pk)
                for field, value in data.items():
                    setattr(sup, field, value)
                raw_import = sup.raw_import or {}
                raw_import['custom_columns'] = custom_columns
                raw_import['supplier_extras'] = supplier_extras
                sup.raw_import = raw_import
                if not sup.account_name:
                    error = "Payee is required."
                else:
                    sup.save()
                    _audit(request.user, "Edited supplier", {"id": sup_id})
                    success = f"Supplier '{sup.account_name}' updated."
                    return redirect('suppliers')
        else:
            data = _parse_supplier_form(request.POST)
            custom_columns = _parse_custom_columns(request.POST)
            supplier_extras = _parse_supplier_extras(request.POST)
            if not data['account_name']:
                error = "Payee is required."
            else:
                data['code_or2'] = _code_or2_for_date(data['date'] or today)
                series_month, series_day = _auto_supplier_series()
                data['series_month'] = series_month
                data['series_day'] = series_day
                # Auto-generate DV/PAYROLL and ORS/BURS if empty
                if not supplier_extras.get('dv_payroll'):
                    supplier_extras['dv_payroll'] = SystemSetting.next_supplier_dv_payroll_no()
                if not supplier_extras.get('ors_burs'):
                    supplier_extras['ors_burs'] = SystemSetting.next_ors_burs_no()
                sup = Supplier.objects.create(
                    created_by=request.user,
                    raw_import={
                        'custom_columns': custom_columns,
                        'supplier_extras': supplier_extras,
                    },
                    **data,
                )
                _audit(request.user, "Created supplier", {"id": sup.pk, "name": data['account_name']})
                success = f"Supplier '{data['account_name']}' added."
                return redirect('suppliers')

    def _supplier_sort_value(value):
        text = str(value or '').strip()
        if not text:
            return (1, 0, '')
        cleaned = ''.join(ch for ch in text if ch.isdigit() or ch == '.')
        if cleaned:
            try:
                return (0, float(cleaned), text)
            except Exception:
                pass
        return (0, text.lower(), text)

    supplier_qs = Supplier.objects.select_related('created_by')

    if period_type == 'date' and period_value:
        try:
            selected_date = date.fromisoformat(period_value)
            supplier_qs = supplier_qs.filter(date=selected_date)
        except ValueError:
            period_type = 'all'
            period_value = ''
    elif period_type == 'month' and period_value:
        try:
            selected_month = date.fromisoformat(period_value + '-01')
            supplier_qs = supplier_qs.filter(date__year=selected_month.year, date__month=selected_month.month)
        except ValueError:
            period_type = 'all'
            period_value = ''

    supplier_list = list(supplier_qs)
    supplier_list.sort(key=lambda supplier: (supplier.created_at or timezone.now(), supplier.pk or 0), reverse=True)

    for supplier in supplier_list:
        custom_columns = ((supplier.raw_import or {}).get('custom_columns') or {})
        _enrich_supplier_for_display(supplier)
        try:
            supplier.custom_columns_json = json.dumps(custom_columns, ensure_ascii=False)
        except TypeError:
            supplier.custom_columns_json = '{}'

        display_date = supplier.date
        if not display_date:
            raw_import = supplier.raw_import or {}
            raw_data = raw_import.get('data') or {}
            candidate_values = [
                raw_data.get('date'), raw_data.get('DATE'), raw_data.get('Date'),
                raw_data.get('mr'), raw_data.get('MR'),
                raw_data.get('mr_or'), raw_data.get('MR OR'),
                supplier.mr,
                supplier.mr_or,
            ]
            for candidate in candidate_values:
                display_date = _parse_supplier_date_value(candidate)
                if display_date:
                    break
        supplier.display_date_text = display_date.strftime('%b %d, %Y') if display_date else ''

    daily_counts = {}
    for supplier in supplier_list:
        created_date = timezone.localdate(supplier.created_at) if supplier.created_at else None
        if created_date not in daily_counts:
            daily_counts[created_date] = 0
        daily_counts[created_date] += 1
        supplier.daily_sequence = daily_counts[created_date]

    def _supplier_reference_year(supplier):
        candidates = [
            supplier.date.isoformat() if supplier.date else '',
            supplier.mr,
            supplier.mr_or,
        ]
        raw_import = supplier.raw_import or {}
        raw_data = raw_import.get('data') or {}
        candidates.append(raw_data.get('date', ''))
        candidates.append(raw_data.get('mr', ''))
        candidates.append(raw_import.get('date', ''))
        for value in candidates:
            text = str(value or '').strip()
            if not text:
                continue
            match = re.search(r'(19|20)\d{2}', text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    continue
        return None

    for supplier in supplier_list:
        reference_year = _supplier_reference_year(supplier)
        supplier.is_old_or = bool(reference_year and reference_year < today.year)
        supplier.or_number_label = 'Old OR' if supplier.is_old_or else 'OR Number'

    supplier_dates = []
    supplier_months = []
    seen_dates = set()
    seen_months = set()
    # Use the current user's supplier records when building the dates/months lists
    for supplier in Supplier.objects.filter(created_by=request.user):
        record_date = supplier.date
        if not record_date:
            continue
        month_value = record_date.strftime('%Y-%m')
        if record_date.isoformat() not in seen_dates:
            seen_dates.add(record_date.isoformat())
            supplier_dates.append(record_date)
        if month_value not in seen_months:
            seen_months.add(month_value)
            supplier_months.append(record_date.replace(day=1))

    supplier_dates.sort(reverse=True)
    supplier_months = sorted(supplier_months, reverse=True)

    auto_supplier_series_month, auto_supplier_series_day = _auto_supplier_series(today)
    today_supplier_date = today.isoformat()
    # Use the current user's supplier records as the source for computing next defaults
    default_source_qs = Supplier.objects.select_related('created_by').filter(created_by=request.user)

    def _supplier_reference_year(supplier):
        candidates = [
            supplier.date.isoformat() if supplier.date else '',
            supplier.mr,
            supplier.mr_or,
        ]
        raw_import = supplier.raw_import or {}
        raw_data = raw_import.get('data') or {}
        candidates.append(raw_data.get('date', ''))
        candidates.append(raw_data.get('mr', ''))
        candidates.append(raw_import.get('date', ''))
        for value in candidates:
            text = str(value or '').strip()
            if not text:
                continue
            match = re.search(r'(19|20)\d{2}', text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    continue
        return None

    current_source_ids = []
    for supplier in default_source_qs.order_by('-created_at', '-pk'):
        reference_year = _supplier_reference_year(supplier)
        if reference_year is None or reference_year >= today.year:
            current_source_ids.append(supplier.pk)
    current_source_qs = default_source_qs.filter(pk__in=current_source_ids) if current_source_ids else default_source_qs.none()

    def _latest_non_empty(field_name):
        return current_source_qs.exclude(**{field_name: ''}).order_by('-created_at', '-pk').values_list(field_name, flat=True).first() or ''

    def _next_trailing_number(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        parts = txt.split('-')
        if len(parts) == 3:
            a, b, c = parts
            if b.strip().isdigit():
                try:
                    return f"{a}-{int(b.strip()) + 1}-{c}"
                except Exception:
                    pass
        if '-' in txt:
            prefix, suffix = txt.rsplit('-', 1)
            if suffix.isdigit():
                try:
                    return f"{prefix}-{int(suffix) + 1}"
                except Exception:
                    pass
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            return f"{prefix}{num + 1}"
        return txt + '-1'

    def _next_last_segment(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        parts = txt.split('-')
        if parts and parts[-1].strip().isdigit():
            try:
                parts[-1] = str(int(parts[-1].strip()) + 1)
                return '-'.join(parts)
            except Exception:
                pass
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            return f"{prefix}{num + 1}"
        return txt + '-1'

    def _next_or_number_value():
        existing_or_numbers = [
            str(value).strip()
            for value in current_source_qs.exclude(or_number='').values_list('or_number', flat=True)
            if str(value).strip()
        ]
        if not existing_or_numbers:
            return ''

        def _or_sort_key(value):
            text = str(value or '').strip()
            digits = re.findall(r'\d+', text)
            if digits:
                try:
                    return (0, int(digits[-1]), text)
                except Exception:
                    pass
            return (1, text.lower(), text)

        highest_or_number = max(existing_or_numbers, key=_or_sort_key)
        return _next_trailing_number(highest_or_number)

    latest_noc_supplier = current_source_qs.filter(code_noc__gt='').order_by('-created_at', '-pk').first()
    default_code_noc = ''
    default_nature_of_collections = ''
    if latest_noc_supplier:
        default_code_noc, default_nature_of_collections = _split_noc_pair(
            latest_noc_supplier.code_noc,
            latest_noc_supplier.nature_of_collections,
        )
        if not default_nature_of_collections:
            default_nature_of_collections = latest_noc_supplier.nature_of_collections or ''
        default_code_noc = _next_trailing_number(default_code_noc)

    code_or2_default = _code_or2_for_date(today)

    auto_supplier_defaults = {
        'mr_or': _next_trailing_number(_latest_non_empty('mr_or')),
        'mr': today.strftime('%Y-%b-%d'),
        'code_or': _next_trailing_number(_latest_non_empty('code_or')),
        'code_or2': code_or2_default,
        'or_number': _next_or_number_value(),
        'code_line': _latest_non_empty('code_line'),
        'line': _latest_non_empty('line'),
        'code_noc': default_code_noc,
        'nature_of_collections': default_nature_of_collections,
    }

    payee_choices_qs = Supplier.objects.select_related('created_by')
    if not is_admin:
        payee_choices_qs = payee_choices_qs.filter(created_by=request.user)
    payee_choices = [
        name for name in payee_choices_qs.exclude(account_name='').values_list('account_name', flat=True)
        if str(name).strip()
    ]
    payee_choices = sorted(set(payee_choices), key=lambda x: str(x).lower())

    # Build grouped account title options: [{group_name, titles:[{value, uacs}]}]
    # Ungrouped titles (no group FK) fall under a special "Other" group at the end.
    _at_groups = AccountTitleGroup.objects.prefetch_related('options').order_by('name')
    account_title_groups = []
    for g in _at_groups:
        titles = list(
            g.options.filter(category=ManagementOption.CATEGORY_ACCOUNT_TITLE, is_active=True)
            .order_by('value')
            .values('value', 'uacs')
        )
        account_title_groups.append({'name': g.name, 'uacs': g.uacs or '', 'titles': titles})
    # Ungrouped titles
    _ungrouped = list(
        ManagementOption.objects.filter(
            category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
            is_active=True,
            group__isnull=True,
        ).order_by('value').values('value', 'uacs')
    )
    if _ungrouped:
        account_title_groups.append({'name': 'Other', 'uacs': '', 'titles': _ungrouped})
    # Flat list with UACS for datalist auto-fill — query ALL active account titles
    _all_titles = list(
        ManagementOption.objects.filter(
            category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
            is_active=True,
        ).order_by('value').values('value', 'uacs')
    )
    account_title_options = [
        {'value': t['value'], 'uacs': t.get('uacs', '')}
        for t in _all_titles
    ]
    remark_options = list(
        ManagementOption.objects.filter(
            category=ManagementOption.CATEGORY_REMARK,
            is_active=True,
        ).order_by('value').values_list('value', flat=True)
    )
    _fc_qs = FundCluster.objects.filter(is_active=True).order_by('code')
    fund_cluster_options = [str(fc) for fc in _fc_qs]
    fund_clusters = _fc_qs   # passed to modal so options can carry data-pk
    approval_options = ['Approved', 'Pending', 'For Review']
    cashier_full_name = _full_name_or_username(request.user)

    # If code_line appears to be an incrementing value (like "4-45-1"),
    # provide the next value by incrementing the trailing number so the
    # Add Supplier modal shows a progressing code line instead of a
    # repeating/stuck value.
    def _next_trailing_number(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        # If pattern like A-B-C (two dashes), increment the middle (B)
        parts = txt.split('-')
        if len(parts) == 3:
            a, b, c = parts
            if b.strip().isdigit():
                try:
                    return f"{a}-{int(b.strip()) + 1}-{c}"
                except Exception:
                    pass
        # Try simple dash-separated suffix first (fallback)
        if '-' in txt:
            prefix, suffix = txt.rsplit('-', 1)
            if suffix.isdigit():
                try:
                    return f"{prefix}-{int(suffix) + 1}"
                except Exception:
                    pass
        # Fallback: find trailing digits and increment
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            return f"{prefix}{num + 1}"
        # If nothing numeric, append -1 as a starter
        return txt + '-1'

    # compute next defaults for code_line and line when possible
    last_code_line = auto_supplier_defaults.get('code_line', '')
    next_code_line = _next_trailing_number(last_code_line) if last_code_line else ''
    auto_supplier_defaults['code_line'] = next_code_line or last_code_line

    last_line = auto_supplier_defaults.get('line', '')
    try:
        next_line = str(int(last_line) + 1) if str(last_line).strip().isdigit() else last_line
    except Exception:
        next_line = last_line
    auto_supplier_defaults['line'] = next_line

    tax_options = ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).order_by('value')
    if not tax_options.exists():
        defaults = [
            {'name': 'Tax (5%/3%)', 'value': '5%/3%'},
            {'name': 'Tax (2%/1%)', 'value': '2%/1%'},
            {'name': 'Professional Tax', 'value': '0%'},
        ]
        for item in defaults:
            ManagementOption.objects.get_or_create(
                category=ManagementOption.CATEGORY_TAX,
                value=item['name'],
                defaults={'uacs': item['value'], 'is_active': True},
            )
        tax_options = ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).order_by('value')

    # Auto-migrate any old Tax (3%/1%) to Tax (2%/1%)
    ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, value='Tax (3%/1%)').update(value='Tax (2%/1%)', uacs='2%/1%')
    active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
    t1 = active_taxes[0] if len(active_taxes) > 0 else None
    t2 = active_taxes[1] if len(active_taxes) > 1 else None

    tax_1_rate = t1.uacs if t1 else '5%/3%'
    tax_2_rate = t2.uacs if t2 else '2%/1%'
    tax_1_label = t1.value if t1 else 'Tax (5%/3%)'
    tax_2_label = t2.value if t2 else 'Tax (2%/1%)'

    context = _page_context(request, is_admin=is_admin, page_title="Supplier Management", extra={
        "supplier_list": supplier_list,
        "page_obj": None,
        "error": error,
        "success": success,
        "auto_supplier_series_month": auto_supplier_series_month,
        "auto_supplier_series_day": auto_supplier_series_day,
        "today_supplier_date": today_supplier_date,
        "auto_supplier_defaults": auto_supplier_defaults,
        "period_type": period_type,
        "period_value": period_value,
        "period_key": period_key,
        "supplier_dates": supplier_dates,
        "supplier_months": supplier_months,
        "payee_choices": payee_choices,
        "account_title_options": account_title_options,
        "account_title_groups": account_title_groups,
        "remark_options": remark_options,
        "fund_cluster_options": fund_cluster_options,
        "fund_clusters": fund_clusters,
        "approval_options": approval_options,
        "cashier_full_name": cashier_full_name,
        "tax_options": tax_options,
        "tax_1_rate": tax_1_rate,
        "tax_2_rate": tax_2_rate,
        "tax_1_label": tax_1_label,
        "tax_2_label": tax_2_label,
    })
    # Assign descending display numbers
    total_count = len(supplier_list)
    for idx, supplier in enumerate(supplier_list):
        supplier.display_number = total_count - idx

    context['page_obj'] = None
    context['visible_pages'] = []

    return render(request, "admin_panel/suppliers.html", context)


@login_required
def radai(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None
    period_key = request.GET.get('period', 'all').strip()
    period_type = 'all'
    period_value = ''
    if '|' in period_key:
        period_type, period_value = period_key.split('|', 1)
        period_type = period_type.strip().lower()
        period_value = period_value.strip()

    from django.db.models import Count
    dup_serials = (
        Radai.objects.exclude(mr_or='').exclude(mr_or__isnull=True)
        .values('mr_or')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
        .values_list('mr_or', flat=True)
    )
    merged_count = 0
    for serial in dup_serials:
        ids = list(Radai.objects.filter(mr_or=serial).order_by('id').values_list('id', flat=True))
        if len(ids) > 1:
            keep_id = ids[0]
            delete_ids = ids[1:]
            Radai.objects.filter(id__in=delete_ids).delete()
            merged_count += len(delete_ids)
    if merged_count:
        _audit(request.user, "Auto-merged duplicate RADAI records", {"deleted": merged_count})
        success = f"Merged {merged_count} duplicate RADAI record(s)." if not success else f"{success} Merged {merged_count} duplicate RADAI record(s)."

    def _auto_radai_series(today=None):
        series_date = today or timezone.localdate()
        series_qs = Radai.objects.filter(created_by=request.user)

        def _series_value(value):
            text = str(value or '').strip()
            return int(text) if text and text.isdigit() else None

        existing_series = [
            _series_value(value)
            for value in series_qs.exclude(series_month='').values_list('series_month', flat=True)
        ]
        existing_numbers = [v for v in existing_series if v is not None]
        next_series_month = (max(existing_numbers) if existing_numbers else series_qs.count()) + 1
        existing_count = series_qs.filter(created_at__date=series_date).count()
        return str(next_series_month), str(existing_count + 1)

    def _parse_radai_form(post):
        check_serial = post.get('check_serial', '').strip() or post.get('mr_or', '').strip()
        payee = post.get('payee', '').strip() or post.get('account_name', '').strip()
        amount_raw = post.get('amount', '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
        try:
            amount = Decimal(amount_raw or '0').quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            amount = Decimal('0.00')

        radai_date = post.get('date', '').strip()
        parsed_date = None
        if radai_date:
            try:
                parsed_date = date.fromisoformat(radai_date)
            except ValueError:
                parsed_date = None

        return {
            'mr_or': check_serial,
            'mr': '',
            'code_or': '',
            'code_or2': '',
            'date': parsed_date,
            'or_number': '',
            'account_number': '',
            'account_name': payee,
            'code_line': '',
            'line': '',
            'code_noc': '',
            'nature_of_collections': post.get('nature_of_payments', '').strip() or post.get('nature_of_collections', '').strip(),
            'amount': amount,
            'remarks': post.get('remarks', '').strip(),
            'address': '',
            'tin': '',
            'status': post.get('status', 'active').strip(),
            'reference_code': post.get('reference_code', '').strip(),
        }

    today = timezone.localdate()

    if request.method == "POST":
        action = request.POST.get('action', 'create')
        if action == 'delete':
            obj = get_object_or_404(Radai, pk=request.POST.get('radai_id'))
            obj.delete()
            _audit(request.user, "Deleted RADAI record", {"id": request.POST.get('radai_id')})
            return redirect('radai')
        elif action == 'edit':
            obj = get_object_or_404(Radai, pk=request.POST.get('radai_id'))
            if not is_admin and obj.created_by != request.user:
                error = "You can only edit your own records."
            else:
                data = _parse_radai_form(request.POST)
                for field, value in data.items():
                    setattr(obj, field, value)
                raw_import = obj.raw_import or {}
                raw_import['radai_extras'] = {
                    'dv_payroll': request.POST.get('dv_payroll', '').strip(),
                    'uacs': request.POST.get('uacs', '').strip(),
                    'remarks_text': request.POST.get('remarks', '').strip(),
                }
                obj.raw_import = raw_import
                fc_id = request.POST.get('fund_cluster', '').strip()
                if fc_id and fc_id.isdigit():
                    obj.fund_cluster_id = int(fc_id)
                elif fc_id == '':
                    obj.fund_cluster = None
                if not obj.account_name:
                    error = "Payee is required."
                else:
                    obj.save()
                    _audit(request.user, "Edited RADAI record", {"id": request.POST.get('radai_id')})
                    success = f"RADAI record '{obj.account_name}' updated."
        else:
            data = _parse_radai_form(request.POST)
            if not data['account_name']:
                error = "Payee is required."
            else:
                # Server-side duplicate check
                check_serial = data.get('mr_or', '').strip()
                dv_payroll = request.POST.get('dv_payroll', '').strip()
                reference_code = request.POST.get('reference_code', '').strip()
                existing_qs = Radai.objects.all()
                if check_serial and existing_qs.filter(mr_or=check_serial).exists():
                    error = f"Duplicate Serial No. '{check_serial}' already exists."
                elif dv_payroll and Radai.objects.filter(raw_import__radai_extras__dv_payroll=dv_payroll).exists():
                    error = f"Duplicate DV/Payroll No. '{dv_payroll}' already exists."
                elif reference_code and existing_qs.filter(reference_code=reference_code).exists():
                    error = f"Duplicate ORS/BURS & Responsibility Center '{reference_code}' already exists."
                else:
                    series_month, series_day = _auto_radai_series()
                    data['series_month'] = series_month
                    data['series_day'] = series_day
                    fc_id = request.POST.get('fund_cluster', '').strip()
                    if fc_id and fc_id.isdigit():
                        data['fund_cluster_id'] = int(fc_id)
                    obj = Radai.objects.create(
                        created_by=request.user,
                        raw_import={
                            'radai_extras': {
                                'dv_payroll': request.POST.get('dv_payroll', '').strip(),
                                'uacs': request.POST.get('uacs', '').strip(),
                                'remarks_text': request.POST.get('remarks', '').strip(),
                            }
                        },
                        **data,
                    )
                    _audit(request.user, "Created RADAI record", {"id": obj.pk, "name": data['account_name']})
                    success = f"RADAI record '{data['account_name']}' added."

    radai_qs = Radai.objects.select_related('created_by')
    if not is_admin:
        radai_qs = radai_qs.filter(created_by=request.user)

    if period_type == 'date' and period_value:
        try:
            selected_date = date.fromisoformat(period_value)
            radai_qs = radai_qs.filter(date=selected_date)
        except ValueError:
            period_type = 'all'
    elif period_type == 'month' and period_value:
        try:
            selected_month = date.fromisoformat(period_value + '-01')
            radai_qs = radai_qs.filter(date__year=selected_month.year, date__month=selected_month.month)
        except ValueError:
            period_type = 'all'

    radai_list = list(radai_qs)
    radai_list.sort(key=lambda r: (r.created_at or timezone.now(), r.pk or 0), reverse=True)

    for radai_obj in radai_list:
        extras = ((radai_obj.raw_import or {}).get('radai_extras') or {})
        radai_obj.dv_payroll = str(extras.get('dv_payroll') or '')
        radai_obj.uacs_code = str(extras.get('uacs') or '')
        radai_obj.display_date = radai_obj.date.strftime('%b %d, %Y') if radai_obj.date else ''

    daily_counts = {}
    for radai_obj in radai_list:
        created_date = timezone.localdate(radai_obj.created_at) if radai_obj.created_at else None
        if created_date not in daily_counts:
            daily_counts[created_date] = 0
        daily_counts[created_date] += 1
        radai_obj.daily_sequence = daily_counts[created_date]

    radai_dates = []
    radai_months = []
    seen_dates = set()
    seen_months = set()
    for radai_obj in Radai.objects.filter(created_by=request.user):
        record_date = radai_obj.date
        if not record_date:
            continue
        month_str = record_date.strftime('%Y-%m')
        if record_date.isoformat() not in seen_dates:
            seen_dates.add(record_date.isoformat())
            radai_dates.append(record_date)
        if month_str not in seen_months:
            seen_months.add(month_str)
            radai_months.append(record_date.replace(day=1))
    radai_dates.sort(reverse=True)
    radai_months = sorted(radai_months, reverse=True)

    auto_radai_series_month, auto_radai_series_day = _auto_radai_series(today)
    today_radai_date = today.isoformat()
    default_source_qs = Radai.objects.select_related('created_by').filter(created_by=request.user)

    def _latest_non_empty(field_name):
        return default_source_qs.exclude(**{field_name: ''}).order_by('-created_at', '-pk').values_list(field_name, flat=True).first() or ''

    def _next_trailing_number(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        parts = txt.split('-')
        if len(parts) == 3 and parts[1].strip().isdigit():
            try:
                return f"{parts[0]}-{int(parts[1].strip()) + 1}-{parts[2]}"
            except Exception:
                pass
        if '-' in txt:
            prefix, suffix = txt.rsplit('-', 1)
            if suffix.isdigit():
                try:
                    return f"{prefix}-{int(suffix) + 1}"
                except Exception:
                    pass
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            return f"{m.group(1)}{int(m.group(2)) + 1}"
        return txt + '-1'

    latest_mr_or = _latest_non_empty('mr_or')
    auto_radai_defaults = {
        'mr_or': _next_trailing_number(latest_mr_or) if latest_mr_or else '1',
    }

    payee_choices_qs = Radai.objects.select_related('created_by')
    if not is_admin:
        payee_choices_qs = payee_choices_qs.filter(created_by=request.user)
    payee_choices = sorted(set(
        name for name in payee_choices_qs.exclude(account_name='').values_list('account_name', flat=True)
        if str(name).strip()
    ), key=lambda x: str(x).lower())

    radai_settings = RadaiSetting.get_settings()
    fund_clusters = FundCluster.objects.filter(is_active=True).order_by('code')

    context = _page_context(request, is_admin=is_admin, page_title="RADAI Management", extra={
        "radai_list": radai_list,
        "page_obj": None,
        "error": error,
        "success": success,
        "auto_radai_series_month": auto_radai_series_month,
        "auto_radai_series_day": auto_radai_series_day,
        "today_radai_date": today_radai_date,
        "auto_radai_defaults": auto_radai_defaults,
        "period_type": period_type,
        "period_value": period_value,
        "period_key": period_key,
        "radai_dates": radai_dates,
        "radai_months": radai_months,
        "payee_choices": payee_choices,
        "radai_settings": radai_settings,
        "fund_clusters": fund_clusters,
        "is_radai": True,
    })
    try:
        page_number = int(request.GET.get('page', 1))
    except Exception:
        page_number = 1
    paginator = Paginator(radai_list, 20)
    try:
        page_obj = paginator.page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    page_start_number = paginator.count - ((page_obj.number - 1) * paginator.per_page)
    for idx, radai_obj in enumerate(page_obj.object_list):
        radai_obj.display_number = page_start_number - idx

    total = paginator.num_pages
    current = page_obj.number
    visible_set = set()
    for p in range(1, total + 1):
        if p <= 2 or p > total - 2 or abs(p - current) <= 2:
            visible_set.add(p)
    visible_pages = []
    last = None
    for p in range(1, total + 1):
        if p in visible_set:
            visible_pages.append(p)
            last = p
        else:
            if last != '...':
                visible_pages.append('...')
                last = '...'

    context['page_obj'] = page_obj
    context['visible_pages'] = visible_pages

    return render(request, "admin_panel/radai.html", context)


@login_required
@require_POST
def update_radai_remark(request, pk):
    is_admin = _is_admin(request.user)
    obj = get_object_or_404(Radai, pk=pk)
    if not is_admin and obj.created_by != request.user:
        return JsonResponse({"ok": False, "error": "You do not have permission to edit this record."}, status=403)

    remark = request.POST.get('remarks', '').strip()
    obj.remarks = remark
    obj.save(update_fields=['remarks'])
    _audit(request.user, "Updated RADAI remark via inline edit", {"id": pk, "remarks": remark})
    return JsonResponse({"ok": True, "remarks": remark})


@login_required
@require_POST
def update_radai_settings(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "Access denied."}, status=403)
    radai_settings = RadaiSetting.get_settings()
    ref = request.POST.get('reference_code', '').strip()
    if '|' in ref:
        parts = ref.split('|', 1)
        radai_settings.ors_burs_no = parts[0].strip()
        radai_settings.responsibility_center = parts[1].strip()
    else:
        radai_settings.ors_burs_no = ref
        radai_settings.responsibility_center = ref
    radai_settings.save(update_fields=['ors_burs_no', 'responsibility_center'])
    _audit(request.user, "Updated RADAI shared settings", {
        "ors_burs_no": radai_settings.ors_burs_no,
        "responsibility_center": radai_settings.responsibility_center,
    })
    return JsonResponse({"ok": True})


@login_required
@require_POST
def export_radai(request):
    is_admin = _is_admin(request.user)
    qs = Radai.objects.all().order_by('-created_at')
    if not is_admin:
        qs = qs.filter(created_by=request.user)

    import csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="radai.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'MR/OR', 'Account Number', 'Account Name', 'Date', 'Amount', 'ORS/BURS No.', 'Responsibility Center', 'Remarks', 'Address', 'TIN', 'Status', 'Created By', 'Created At'])
    for r in qs:
        writer.writerow([
            r.pk, r.mr_or, r.account_number, r.account_name,
            r.date.isoformat() if r.date else '',
            str(r.amount), r.ors_burs_no, r.responsibility_center,
            r.remarks, r.address, r.tin, r.status,
            r.created_by.username if r.created_by else '',
            r.created_at.isoformat() if r.created_at else '',
        ])
    return response


@login_required
@require_GET
def radai_detail(request, pk):
    obj = get_object_or_404(Radai, pk=pk)
    return JsonResponse({
        'id': obj.pk,
        'mr_or': obj.mr_or,
        'account_number': obj.account_number,
        'account_name': obj.account_name,
        'date': obj.date.isoformat() if obj.date else '',
        'amount': str(obj.amount) if obj.amount else '0.00',
        'ors_burs_no': obj.ors_burs_no,
        'responsibility_center': obj.responsibility_center,
        'remarks': obj.remarks,
        'address': obj.address,
        'tin': obj.tin,
        'status': obj.status,
    })


@login_required
@require_POST
def update_supplier_remark(request, pk):
    is_admin = _is_admin(request.user)
    supplier = get_object_or_404(Supplier, pk=pk)
    if not is_admin and supplier.created_by != request.user:
        return JsonResponse({"ok": False, "error": "You do not have permission to edit this supplier."}, status=403)

    remark = request.POST.get('remarks', '').strip()
    if not remark:
        remark = request.POST.get('remarks_text', '').strip()

    supplier.remarks = remark
    supplier.save(update_fields=['remarks'])
    _audit(request.user, "Updated supplier remark via inline edit", {"id": pk, "remarks": remark})
    return JsonResponse({"ok": True, "remarks": remark})


@login_required
def registry(request):
    is_admin = _is_admin(request.user)
    selected_cashier = request.GET.get('cashier', '').strip() if is_admin else ''
    error = None
    success = None

    if request.method == 'POST':
        if not is_admin:
            return redirect('admin_dashboard')

        action = request.POST.get('action', '').strip().lower()
        if action == 'save':
            try:
                payload = json.loads(request.POST.get('sheet_payload', '{}'))
                saved_count = _save_registry_sheet(payload, request.user)
                success = f'Saved {saved_count} rows to the registry sheet.'
            except Exception as exc:
                error = f'Unable to save registry sheet: {exc}'

    context = _build_registry_context(request, is_admin, selected_cashier)
    context['error'] = error
    context['success'] = success
    return render(request, "admin_panel/registry.html", context)


# ─────────────────────────────────────────────
# CHEQUES
# ─────────────────────────────────────────────

@login_required
def cheque_list(request):
    is_admin = _is_admin(request.user)
    qs = Cheque.objects.select_related('payee', 'fund_cluster', 'created_by').order_by('-created_at')

    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    raw_cheques = list(qs)
    seen_numbers = set()
    cheques = []
    for c in raw_cheques:
        num = str(c.real_cheque_number or c.cheque_number or '').strip()
        if num and num in seen_numbers:
            continue
        if num:
            seen_numbers.add(num)
        cheques.append(c)

    total_cheques = len(cheques)
    for i, cheque in enumerate(cheques):
        action_reason = _cheque_action_block_reason(cheque)
        cheque.action_block_reason = action_reason
        cheque.action_blocked = bool(action_reason)
        cheque.desc_index = total_cheques - i

    error = request.GET.get('error', '').strip()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        html = render_to_string("admin_panel/cheques_rows.html", {"cheques": cheques, "is_admin": is_admin}, request=request)
        return JsonResponse({"ok": True, "html": html, "count": len(cheques)})

    context = _page_context(request, is_admin=is_admin, page_title="Cheques", extra={
        "cheques": cheques,
        "status_filter": status_filter,
        "status_choices": Cheque.STATUS_CHOICES,
        "error": error or None,
    })
    return render(request, "admin_panel/cheques.html", context)


@login_required
@require_GET
def cheque_lookup(request):
    recent = request.GET.get('recent')
    payee_name = request.GET.get('payee_name', '').strip()
    number_raw = request.GET.get('number', '').strip()
    pk_raw = request.GET.get('pk', '').strip()

    def _dec(value):
        try:
            return decrypt_value(value) if value else ''
        except Exception:
            return value or ''

    # ── Mode 1: kiosk bundle (recent cheques + suppliers + fund clusters) ──
    if recent:
        recent_cheques = []
        for c in Cheque.objects.select_related('payee', 'fund_cluster').order_by('-created_at')[:8]:
            num = str(c.real_cheque_number or c.cheque_number or '')
            recent_cheques.append({
                "pk": c.pk,
                "checkNo": num,
                "label": f"#{num}",
                "payee": c.payee_name or '',
                "amount": str(c.amount or 0),
            })

        existing_cheque_payee_ids = set(
            Cheque.objects.exclude(status='voided')
            .filter(payee_id__isnull=False)
            .values_list('payee_id', flat=True)
        )
        existing_cheque_payee_names = set(
            name.strip().lower() for name in 
            Cheque.objects.exclude(status='voided')
            .values_list('payee_name', flat=True)
            if name
        )

        suppliers_payload = []
        for s in Supplier.objects.filter(status='active').order_by('id'):
            if s.pk in existing_cheque_payee_ids or (s.account_name and s.account_name.strip().lower() in existing_cheque_payee_names):
                continue
            extras = (s.raw_import or {}).get('supplier_extras') or {}
            seq_label = s.mr_or if s.mr_or else f"#{s.pk}"
            suppliers_payload.append({
                "id": s.pk,
                "name": s.account_name,
                "seq_number": seq_label,
                "display_label": f"{seq_label} — {s.account_name}",
                "check_serial": s.mr_or or '',
                "fund_cluster_id": str(extras.get('fund_cluster_id') or ''),
                "fund_cluster_code": str(extras.get('fund_cluster') or ''),
                "account_number": _dec(s.account_number),
                "nature_of_collections": s.nature_of_collections or '',
                "remarks": s.remarks or '',
                "amount": str(s.amount or 0),
            })

        funds_payload = [
            {
                "id": fc.pk,
                "code": fc.code,
                "name": fc.name,
                "bank_name": fc.bank_name or '',
                "account_number": _dec(fc.account_number),
            }
            for fc in FundCluster.objects.filter(is_active=True).order_by('code')
        ]

        return JsonResponse({
            "ok": True,
            "next_cheque_number": Cheque.next_cheque_number(),
            "recent": recent_cheques,
            "suppliers": suppliers_payload,
            "funds": funds_payload,
        })

    # ── Mode 2: supplier details by payee name (kiosk autofill) ──
    if payee_name:
        supplier = (
            Supplier.objects.filter(account_name__iexact=payee_name).first()
            or Supplier.objects.filter(account_name__icontains=payee_name).first()
        )
        if not supplier:
            return JsonResponse({"ok": False})

        # Exclude suppliers that ALREADY have a cheque (printed, released, pending, draft, staled)
        has_existing_cheque = Cheque.objects.exclude(status='voided').filter(
            models.Q(payee=supplier) | models.Q(payee_name__iexact=supplier.account_name)
        ).exists()

        if has_existing_cheque:
            return JsonResponse({
                "ok": False,
                "already_has_cheque": True,
                "error": f"A cheque has already been created, printed, released, or staled for '{supplier.account_name}'."
            })

        extras = (supplier.raw_import or {}).get('supplier_extras') or {}
        return JsonResponse({
            "ok": True,
            "supplier": {
                "id": supplier.pk,
                "name": supplier.account_name,
                "check_serial": supplier.mr_or or '',
                "fund_cluster_id": str(extras.get('fund_cluster_id') or ''),
                "fund_cluster_code": str(extras.get('fund_cluster') or ''),
                "account_number": _dec(supplier.account_number),
                "nature_of_collections": supplier.nature_of_collections or '',
                "remarks": supplier.remarks or '',
                "amount": str(supplier.amount or 0),
            },
        })

    # ── Mode 3: cheque lookup by number or pk (editor + validator) ──
    if not number_raw and not pk_raw:
        return JsonResponse({"found": False})

    cheque = None
    if pk_raw.isdigit():
        cheque = Cheque.objects.select_related('payee', 'fund_cluster').filter(pk=pk_raw).first()
    if cheque is None and number_raw:
        cheque = _find_cheque_by_number(number_raw)

    if not cheque:
        last = Cheque.objects.order_by('-cheque_number').first()
        return JsonResponse({
            "found": False,
            "number": number_raw,
            "fallback": last.cheque_number if last else 1
        })

    released_display = ''
    if cheque.status == 'released' and cheque.updated_at:
        try:
            released_display = to_ph_time(cheque.updated_at).strftime('%b %d, %Y %I:%M %p')
        except Exception:
            released_display = ''

    return JsonResponse({
        "found": True,
        "number": cheque.cheque_number,
        "checkNo": cheque.real_cheque_number or cheque.cheque_number,
        "pk": cheque.pk,
        "date": cheque.date.isoformat() if cheque.date else '',
        "issueDate": cheque.date.isoformat() if cheque.date else '',
        "staleLimitDays": cheque.stale_after_days or 180,
        "payee_name": cheque.payee_name,
        "payee": cheque.payee_name,
        "payee_id": cheque.payee_id,
        "fund_cluster_id": cheque.fund_cluster_id,
        "fund_cluster_label": str(cheque.fund_cluster) if cheque.fund_cluster else '',
        "amount": str(cheque.amount),
        "bank": cheque.bank_name,
        "bank_name": cheque.bank_name,
        "accountNo": _dec(cheque.account_number),
        "account_number": _dec(cheque.account_number),
        "purpose": cheque.purpose,
        "status": cheque.status,
        "released_display": released_display,
        "edit_url": f"/cheques/{cheque.pk}/edit/",
        "payee_label": cheque.payee.account_name if cheque.payee else '',
        "fund_label": str(cheque.fund_cluster) if cheque.fund_cluster else '',
    })


def _sync_suppliers_by_uacs(uacs_code, account_title):
    """Sync all suppliers' raw_import account_title when a matching UACS code account title is added/updated."""
    if not uacs_code:
        return 0
    from cashier.models import Supplier
    updated = 0
    for s in Supplier.objects.all():
        extras = (s.raw_import or {}).get('supplier_extras') or {}
        if str(extras.get('uacs') or '').strip() == uacs_code.strip():
            extras['account_title'] = account_title
            if not isinstance(s.raw_import, dict):
                s.raw_import = {}
            s.raw_import['supplier_extras'] = extras
            Supplier.objects.filter(pk=s.pk).update(raw_import=s.raw_import)
            updated += 1
    return updated


def _enrich_supplier_for_display(supplier):
    """
    Annotate a Supplier object with computed display attributes.
    Used by both the Suppliers Management table and the Cheque Editor dropdown
    so that the exact same values appear in both places.
    Stored extras are used as-is; missing taxes are computed from amount.
    """
    extras = ((supplier.raw_import or {}).get('supplier_extras') or {})
    supplier.account_title         = str(extras.get('account_title') or '')
    supplier.fund_cluster_label    = str(extras.get('fund_cluster') or '')
    supplier.x_fund_cluster_id     = str(extras.get('fund_cluster_id') or '')
    supplier.cashier_name          = str(extras.get('cashier_name') or '')
    supplier.approval              = str(extras.get('approval') or '')
    supplier.other_deductions      = str(extras.get('other_deductions') or '')
    supplier.is_vat                = str(extras.get('is_vat') or 'false')
    supplier.remarks_text          = str(extras.get('remarks_text') or supplier.remarks or '')
    supplier.dv_payroll            = str(extras.get('dv_payroll') or '')
    supplier.ors_burs              = str(extras.get('ors_burs') or '')
    supplier.responsibility_center = str(extras.get('responsibility_center') or '')
    supplier.uacs                  = str(extras.get('uacs') or '')
    supplier.professional_tax_rate = str(extras.get('professional_tax_rate') or '')
    supplier.professional_tax      = str(extras.get('professional_tax') or '')
    supplier.tax_base_5_3          = str(extras.get('tax_base_5_3') or '')
    supplier.tax_base_3_1          = str(extras.get('tax_base_3_1') or '')
    supplier.tax_1_option          = str(extras.get('tax_1_option') or '')
    supplier.tax_2_option          = str(extras.get('tax_2_option') or '')
    supplier.tax_5_rate_vat        = str(extras.get('tax_5_rate_vat') or '')
    supplier.tax_5_rate_non_vat    = str(extras.get('tax_5_rate_non_vat') or '')
    supplier.tax_2_rate_vat        = str(extras.get('tax_2_rate_vat') or '')
    supplier.tax_2_rate_non_vat    = str(extras.get('tax_2_rate_non_vat') or '')
    # Nature shown in table = stored nature_of_collections field
    supplier.x_nature     = str(supplier.nature_of_collections or extras.get('remarks_text') or '')
    supplier.x_dv_payroll = supplier.dv_payroll
    supplier.x_ors_burs   = supplier.ors_burs
    supplier.x_resp_center= supplier.responsibility_center
    supplier.x_uacs       = supplier.uacs
    supplier.x_prof_tax   = supplier.professional_tax

    # Taxes: use stored values if present; otherwise compute from amount
    raw_tax_5 = extras.get('tax_5')
    raw_tax_2 = extras.get('tax_2')
    raw_gross = extras.get('gross_amount')
    is_vat_flag = supplier.is_vat == 'true'

    if not is_vat_flag:
        supplier.tax_5 = '0.00'
        supplier.tax_2 = '0.00'
        try:
            base_amount = Decimal(str(supplier.amount or 0))
            other = Decimal(str(extras.get('other_deductions') or 0))
            prof = Decimal(str(extras.get('professional_tax') or 0))
            supplier.gross_amount = str((base_amount + prof + other).quantize(Decimal('0.01')))
        except Exception:
            supplier.gross_amount = str(raw_gross or '')
    elif raw_tax_5 is None and raw_tax_2 is None:
        try:
            base_amount  = Decimal(str(supplier.amount or 0))
            other        = Decimal(str(extras.get('other_deductions') or 0))
            net          = base_amount + other

            active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
            t1 = active_taxes[0] if len(active_taxes) > 0 else None
            t2 = active_taxes[1] if len(active_taxes) > 1 else None
            t1_rate = t1.uacs if t1 else '5%/3%'
            t2_rate = t2.uacs if t2 else '2%/1%'
            t1_vat, t1_nv = _get_tax_rates(t1_rate)
            t2_vat, t2_nv = _get_tax_rates(t2_rate)

            # Check if there are custom rates in the extras
            custom_t1_vat = extras.get('tax_5_rate_vat')
            custom_t2_vat = extras.get('tax_2_rate_vat')
            if custom_t1_vat not in (None, ''):
                try:
                    t1_vat = Decimal(str(custom_t1_vat).replace('%', '').strip()) / Decimal('100')
                except Exception:
                    pass
            if custom_t2_vat not in (None, ''):
                try:
                    t2_vat = Decimal(str(custom_t2_vat).replace('%', '').strip()) / Decimal('100')
                except Exception:
                    pass

            denom = Decimal('1.12') - (t1_vat + t2_vat)
            if denom <= 0:
                denom = Decimal('1.12')

            gross = (net * Decimal('1.12') / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            b     = (gross / Decimal('1.12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            t5    = (b * t1_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            t2    = (b * t2_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            prof          = Decimal(str(extras.get('professional_tax') or 0))
            computed_gross = (base_amount + t5 + t2 + prof + other).quantize(Decimal('0.01'))
            supplier.tax_5        = str(t5)
            supplier.tax_2        = str(t2)
            supplier.gross_amount = str(raw_gross if raw_gross is not None else computed_gross)
        except Exception:
            supplier.tax_5        = ''
            supplier.tax_2        = ''
            supplier.gross_amount = str(raw_gross or '')
    else:
        supplier.tax_5        = str(raw_tax_5 or '')
        supplier.tax_2        = str(raw_tax_2 or '')
        supplier.gross_amount = str(raw_gross or '')

    # Aliases used by the cheque editor template (x_ prefix avoids Django underscore restriction)
    supplier.x_tax5  = supplier.tax_5
    supplier.x_tax2  = supplier.tax_2
    supplier.x_gross = supplier.gross_amount
    return supplier


def _enrich_radai_for_display(radai_obj):
    extras = ((radai_obj.raw_import or {}).get('radai_extras') or {})
    radai_obj.account_title         = str(extras.get('account_title') or '')
    radai_obj.fund_cluster_label    = str(extras.get('fund_cluster') or '')
    radai_obj.x_fund_cluster_id     = str(extras.get('fund_cluster_id') or '')
    radai_obj.cashier_name          = str(extras.get('cashier_name') or '')
    radai_obj.approval              = str(extras.get('approval') or '')
    radai_obj.other_deductions      = str(extras.get('other_deductions') or '')
    radai_obj.is_vat                = str(extras.get('is_vat') or 'false')
    radai_obj.remarks_text          = str(extras.get('remarks_text') or radai_obj.remarks or '')
    radai_obj.dv_payroll            = str(extras.get('dv_payroll') or '')
    radai_obj.ors_burs              = str(extras.get('ors_burs') or '')
    radai_obj.responsibility_center = str(extras.get('responsibility_center') or '')
    radai_obj.uacs                  = str(extras.get('uacs') or '')
    radai_obj.professional_tax_rate = str(extras.get('professional_tax_rate') or '')
    radai_obj.professional_tax      = str(extras.get('professional_tax') or '')
    radai_obj.tax_base_5_3          = str(extras.get('tax_base_5_3') or '')
    radai_obj.tax_base_3_1          = str(extras.get('tax_base_3_1') or '')
    radai_obj.tax_1_option          = str(extras.get('tax_1_option') or '')
    radai_obj.tax_2_option          = str(extras.get('tax_2_option') or '')
    radai_obj.tax_5_rate_vat        = str(extras.get('tax_5_rate_vat') or '')
    radai_obj.tax_5_rate_non_vat    = str(extras.get('tax_5_rate_non_vat') or '')
    radai_obj.tax_2_rate_vat        = str(extras.get('tax_2_rate_vat') or '')
    radai_obj.tax_2_rate_non_vat    = str(extras.get('tax_2_rate_non_vat') or '')
    radai_obj.x_nature     = str(radai_obj.nature_of_collections or extras.get('remarks_text') or '')
    radai_obj.x_dv_payroll = radai_obj.dv_payroll
    radai_obj.x_ors_burs   = radai_obj.ors_burs
    radai_obj.x_resp_center= radai_obj.responsibility_center
    radai_obj.x_uacs       = radai_obj.uacs
    radai_obj.x_prof_tax   = radai_obj.professional_tax

    raw_tax_5 = extras.get('tax_5')
    raw_tax_2 = extras.get('tax_2')
    raw_gross = extras.get('gross_amount')
    is_vat_flag = radai_obj.is_vat == 'true'

    if not is_vat_flag:
        radai_obj.tax_5 = '0.00'
        radai_obj.tax_2 = '0.00'
        try:
            base_amount = Decimal(str(radai_obj.amount or 0))
            other = Decimal(str(extras.get('other_deductions') or 0))
            prof = Decimal(str(extras.get('professional_tax') or 0))
            radai_obj.gross_amount = str((base_amount + prof + other).quantize(Decimal('0.01')))
        except Exception:
            radai_obj.gross_amount = str(raw_gross or '')
    elif raw_tax_5 is None and raw_tax_2 is None:
        try:
            base_amount  = Decimal(str(radai_obj.amount or 0))
            other        = Decimal(str(extras.get('other_deductions') or 0))
            net          = base_amount + other

            active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
            t1 = active_taxes[0] if len(active_taxes) > 0 else None
            t2 = active_taxes[1] if len(active_taxes) > 1 else None
            t1_rate = t1.uacs if t1 else '5%/3%'
            t2_rate = t2.uacs if t2 else '2%/1%'
            t1_vat, t1_nv = _get_tax_rates(t1_rate)
            t2_vat, t2_nv = _get_tax_rates(t2_rate)

            custom_t1_vat = extras.get('tax_5_rate_vat')
            custom_t2_vat = extras.get('tax_2_rate_vat')
            if custom_t1_vat not in (None, ''):
                try:
                    t1_vat = Decimal(str(custom_t1_vat).replace('%', '').strip()) / Decimal('100')
                except Exception:
                    pass
            if custom_t2_vat not in (None, ''):
                try:
                    t2_vat = Decimal(str(custom_t2_vat).replace('%', '').strip()) / Decimal('100')
                except Exception:
                    pass

            denom = Decimal('1.12') - (t1_vat + t2_vat)
            if denom <= 0:
                denom = Decimal('1.12')

            gross = (net * Decimal('1.12') / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            b     = (gross / Decimal('1.12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            t5    = (b * t1_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            t2    = (b * t2_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            prof          = Decimal(str(extras.get('professional_tax') or 0))
            computed_gross = (base_amount + t5 + t2 + prof + other).quantize(Decimal('0.01'))
            radai_obj.tax_5        = str(t5)
            radai_obj.tax_2        = str(t2)
            radai_obj.gross_amount = str(raw_gross if raw_gross is not None else computed_gross)
        except Exception:
            radai_obj.tax_5        = ''
            radai_obj.tax_2        = ''
            radai_obj.gross_amount = str(raw_gross or '')
    else:
        radai_obj.tax_5        = str(raw_tax_5 or '')
        radai_obj.tax_2        = str(raw_tax_2 or '')
        radai_obj.gross_amount = str(raw_gross or '')

    radai_obj.x_tax5  = radai_obj.tax_5
    radai_obj.x_tax2  = radai_obj.tax_2
    radai_obj.x_gross = radai_obj.gross_amount
    return radai_obj


@login_required
def cheque_create(request):
    is_admin = _is_admin(request.user)

    existing_cheque_payee_ids = set(
        Cheque.objects.exclude(status='voided')
        .filter(payee_id__isnull=False)
        .values_list('payee_id', flat=True)
    )
    existing_cheque_payee_names = set(
        name.strip().lower() for name in 
        Cheque.objects.exclude(status='voided')
        .values_list('payee_name', flat=True)
        if name
    )

    suppliers_qs = [
        _enrich_supplier_for_display(s)
        for s in Supplier.objects.filter(status='active').order_by('account_name')
        if s.pk not in existing_cheque_payee_ids and (not s.account_name or s.account_name.strip().lower() not in existing_cheque_payee_names)
    ]
    funds_qs = FundCluster.objects.filter(is_active=True).order_by('code')
    error = request.GET.get('error') or None
    success = request.GET.get('success') or None
    cheque = None

    if request.method == "POST":
        amount = _parse_money(request.POST.get('amount', '0'))

        payee_id = request.POST.get('payee_id') or None
        fund_id = request.POST.get('fund_cluster_id') or None
        cheque_date = request.POST.get('date') or date.today().isoformat()
        status = request.POST.get('status', 'draft')
        fund_cluster = FundCluster.objects.filter(pk=fund_id, is_active=True).first() if fund_id else None

        if status == 'released':
            error = _fund_balance_error(fund_cluster, amount)

        manual_cheque_input = request.POST.get('cheque_number', '').strip()
        try:
            cheque_number_value = Cheque.normalize_cheque_number(manual_cheque_input) if manual_cheque_input else Cheque.next_cheque_number()
        except ValueError as exc:
            cheque_number_value = Cheque.next_cheque_number()
            error = str(exc)

        payee_name_val = request.POST.get('payee_name', '').strip()
        existing_cheque = None
        if manual_cheque_input:
            existing_cheque = Cheque.objects.filter(cheque_number=cheque_number_value).first()
        if not existing_cheque and payee_name_val:
            existing_cheque = Cheque.objects.filter(payee_name__iexact=payee_name_val).exclude(status='voided').first()

        if existing_cheque:
            cheque = existing_cheque
            cheque.cheque_number = cheque_number_value
            cheque.payee_name = payee_name_val or cheque.payee_name
            cheque.amount = amount if amount > 0 else cheque.amount
            cheque.date = cheque_date
            cheque.purpose = request.POST.get('purpose', '').strip() or cheque.purpose
            cheque.bank_name = request.POST.get('bank_name', '').strip() or cheque.bank_name
            cheque.account_number = request.POST.get('account_number', '').strip() or cheque.account_number
            cheque.status = status
            cheque.updated_by = request.user
        else:
            cheque = Cheque(
                cheque_number=cheque_number_value,
                payee_name=payee_name_val,
                amount=amount,
                date=cheque_date,
                purpose=request.POST.get('purpose', '').strip(),
                bank_name=request.POST.get('bank_name', '').strip(),
                account_number=request.POST.get('account_number', '').strip(),
                status=status,
                created_by=request.user,
                dv_payroll_no=request.POST.get('dv_payroll_no', '').strip() or SystemSetting.next_dv_payroll_no(),
                ors_burs_no=request.POST.get('ors_burs_no', '').strip() or SystemSetting.next_ors_burs_no(),
                responsibility_center=request.POST.get('responsibility_center', '').strip(),
                uacs_object_code=request.POST.get('uacs_object_code', '').strip(),
                nature_of_payment=request.POST.get('nature_of_payment', '').strip(),
                professional_tax=_parse_money(request.POST.get('professional_tax', '')) or None,
                tax_5_3=_parse_money(request.POST.get('tax_5_3', '')) or None,
                tax_3_1=_parse_money(request.POST.get('tax_3_1', '')) or None,
            )

        if payee_id:
            cheque.payee_id = payee_id
        if fund_id:
            cheque.fund_cluster_id = fund_id

        # optional stale timer
        stale_days = request.POST.get('stale_after_days')
        try:
            cheque.stale_after_days = int(stale_days) if stale_days not in (None, '', 'null') else None
        except Exception:
            cheque.stale_after_days = None

        if cheque.status == 'released' and cheque.stale_after_days:
            try:
                base_date = date.fromisoformat(cheque.date) if isinstance(cheque.date, str) else (cheque.date or date.today())
            except Exception:
                base_date = date.today()
            cheque.stale_at = base_date + timedelta(days=cheque.stale_after_days)

        if error:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"ok": False, "error": error})
        else:
            cheque.save()

            # Sync the new/edited cheque number directly to the supplier's check serial (mr_or)
            try:
                supplier_obj = cheque.payee
                if not supplier_obj and cheque.payee_name:
                    supplier_obj = Supplier.objects.filter(account_name__iexact=cheque.payee_name.strip()).first()
                if supplier_obj and cheque.cheque_number:
                    supplier_obj.mr_or = cheque.cheque_number
                    supplier_obj.save(update_fields=['mr_or'])
            except Exception as sync_err:
                logger.warning(f"Failed to sync cheque serial to supplier: {sync_err}")

            if cheque.status == 'released' and cheque.fund_cluster_id:
                _adjust_fund_balance(cheque.fund_cluster, -cheque.amount)

            # Update the running sequence. If the user manually entered a number,
            # treat it as the new base so the next auto-generated cheque follows it.
            settings_obj = SystemSetting.get_settings()
            settings_obj.last_cheque_number = cheque.cheque_number
            settings_obj.save(update_fields=['last_cheque_number'])

            _audit(request.user, "Created cheque", {
                "id": cheque.pk,
                "number": cheque.cheque_number,
                "amount": str(amount)
            })

            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    "ok": True,
                    "cheque_id": cheque.pk,
                    "print_url": reverse('cheque_print', args=[cheque.pk]),
                    "success_url": reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} saved and print initiated."
                })
            return redirect(reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} saved successfully.")

    context = _page_context(request, is_admin=is_admin, page_title="New Cheque", extra={
        "suppliers": suppliers_qs,
        "funds": funds_qs,
        "error": error,
        "success": success,
        "cheque_number_value": cheque.cheque_number if cheque else Cheque.next_cheque_number(),
        "cheque": cheque,
        "next_cheque": Cheque.next_cheque_number(),
        "today": date.today().isoformat(),
    })

    # include supplier defaults for the Add Supplier modal
    try:
        # use global scope so cashier Add Supplier modal matches admin sequencing
        defaults = _compute_auto_supplier_defaults(request.user, global_scope=True)
        context.update(defaults)
    except Exception:
        pass

    return render(request, "admin_panel/cheque_editor.html", context)

@login_required
def cheque_edit(request, pk):
    is_admin = _is_admin(request.user)
    cheque = get_object_or_404(Cheque, pk=pk)
    if not is_admin and cheque.created_by != request.user:
        return redirect('cheque_list')
    if cheque.status == 'released' and not is_admin:
        return redirect(f"{reverse('cheque_list')}?error={quote('Released cheques cannot be edited.')}")

    suppliers_qs = [
        _enrich_supplier_for_display(s)
        for s in Supplier.objects.filter(status='active').order_by('account_name')
    ]
    funds_qs = FundCluster.objects.filter(is_active=True).order_by('code')
    error = None
    success = None

    if request.method == "POST":
        if cheque.status in ('voided', 'stale', 'released'):
            error = "Cannot edit a voided, stale, or released cheque."
        else:
            amount = cheque.amount  # Amount is not editable on edit

            payee_id = request.POST.get('payee_id') or None
            fund_id = request.POST.get('fund_cluster_id') or None
            cheque_date = request.POST.get('date') or cheque.date.isoformat()
            status = request.POST.get('status', cheque.status)
            new_fund_cluster = FundCluster.objects.filter(pk=fund_id, is_active=True).first() if fund_id else None

            if status == 'released':
                error = _fund_balance_error(new_fund_cluster, amount)

            cheque_number_raw = request.POST.get('cheque_number', '').strip()

            try:
                attempted = Cheque.normalize_cheque_number(
                    cheque_number_raw,
                    fallback=cheque.cheque_number
                )
            except:
                attempted = cheque.cheque_number

            cheque.cheque_number = attempted
            cheque.payee_name = request.POST.get('payee_name', '').strip()
            cheque.amount = amount
            cheque.date = cheque_date
            cheque.purpose = request.POST.get('purpose', '').strip()
            cheque.bank_name = request.POST.get('bank_name', '').strip()
            cheque.account_number = request.POST.get('account_number', '').strip()
            cheque.status = status
            # Report / disbursement fields
            cheque.dv_payroll_no = request.POST.get('dv_payroll_no', '').strip()
            cheque.ors_burs_no = request.POST.get('ors_burs_no', '').strip()
            cheque.responsibility_center = request.POST.get('responsibility_center', '').strip()
            cheque.uacs_object_code = request.POST.get('uacs_object_code', '').strip()
            cheque.nature_of_payment = request.POST.get('nature_of_payment', '').strip()
            cheque.professional_tax = _parse_money(request.POST.get('professional_tax', '')) or None
            cheque.tax_5_3 = _parse_money(request.POST.get('tax_5_3', '')) or None
            cheque.tax_3_1 = _parse_money(request.POST.get('tax_3_1', '')) or None
            # persist stale timer if provided
            stale_days = request.POST.get('stale_after_days')
            try:
                cheque.stale_after_days = int(stale_days) if stale_days not in (None, '', 'null') else cheque.stale_after_days
            except Exception:
                pass

            if cheque.status == 'released':
                days = cheque.stale_after_days or 180
                try:
                    base_date = date.fromisoformat(cheque.date) if isinstance(cheque.date, str) else (cheque.date or date.today())
                except Exception:
                    base_date = date.today()
                cheque.stale_at = base_date + timedelta(days=days)
            cheque.payee_id = payee_id
            cheque.fund_cluster_id = fund_id
            cheque.updated_by = request.user

            if error:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({"ok": False, "error": error})
            else:
                cheque.save()

                # Sync the new/edited cheque number directly to the supplier's check serial (mr_or)
                try:
                    supplier_obj = cheque.payee
                    if not supplier_obj and cheque.payee_name:
                        supplier_obj = Supplier.objects.filter(account_name__iexact=cheque.payee_name.strip()).first()
                    if supplier_obj and cheque.cheque_number:
                        supplier_obj.mr_or = cheque.cheque_number
                        supplier_obj.save(update_fields=['mr_or'])
                except Exception as sync_err:
                    logger.warning(f"Failed to sync cheque serial to supplier: {sync_err}")

                if cheque.status == 'released' and cheque.fund_cluster_id:
                    _adjust_fund_balance(cheque.fund_cluster, -cheque.amount)

                _audit(request.user, "Edited cheque", {"id": cheque.pk, "status": cheque.status})

                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        "ok": True,
                        "cheque_id": cheque.pk,
                        "print_url": reverse('cheque_print', args=[cheque.pk]),
                        "success_url": reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} updated and print initiated."
                    })
                return redirect(reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} updated successfully.")

    context = _page_context(request, is_admin=is_admin, page_title="Edit Cheque", extra={
        "cheque": cheque,
        "suppliers": suppliers_qs,
        "funds": funds_qs,
        "error": error,
        "success": success,
        "cheque_number_value": cheque.real_cheque_number,
        "today": date.today().isoformat(),
    })
    try:
        # use global scope so cashier Add Supplier modal matches admin sequencing
        defaults = _compute_auto_supplier_defaults(request.user, global_scope=True)
        context.update(defaults)
    except Exception:
        pass
    return render(request, "admin_panel/cheque_editor.html", context)


@login_required
def cheque_print(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)
    is_admin = _is_admin(request.user)
    if not is_admin and cheque.created_by != request.user:
        return redirect('cheque_list')

    if cheque.status == 'released' and not is_admin:
        return redirect(f"{reverse('cheque_list')}?error={quote('Released cheques cannot be printed again.')}")

    if not cheque.printed_at:
        cheque.printed_at = timezone.now()
        if cheque.status == 'draft':
            cheque.status = 'pending'
        cheque.save(update_fields=['printed_at', 'status'])
        _audit(request.user, "Printed cheque", {"id": cheque.pk})

    is_embed = request.GET.get('embed') == '1' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    template_name = "admin_panel/cheque_print_embed.html" if is_embed else "admin_panel/cheque_print.html"

    context = _page_context(request, is_admin=is_admin, page_title="Print Cheque", extra={
        "cheque": cheque,
        "is_embed": is_embed
    })
    return render(request, template_name, context)


@login_required
@require_POST
def cheque_void(request, pk):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    cheque = get_object_or_404(Cheque, pk=pk)
    reason = request.POST.get('reason', '').strip()

    if cheque.status == 'released' and cheque.fund_cluster_id:
        _adjust_fund_balance(cheque.fund_cluster, cheque.amount)

    cheque.status = 'voided'
    cheque.voided_at = timezone.now()
    cheque.void_reason = reason
    cheque.save(update_fields=['status', 'voided_at', 'void_reason'])

    _audit(request.user, "Voided cheque", {"id": cheque.pk, "reason": reason})
    return redirect('cheque_list')


@login_required
@require_POST
def cheque_delete(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)
    is_admin = _is_admin(request.user)

    if not is_admin and cheque.created_by != request.user:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
        return redirect('cheque_list')

    cheque_number = cheque.cheque_number
    cheque_payee = cheque.payee_name

    if cheque.status == 'released' and not is_admin:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"ok": False, "error": "Released cheques cannot be deleted."}, status=400)
        return redirect(f"{reverse('cheque_list')}?error={quote('Released cheques cannot be deleted.')}")

    if cheque.fund_cluster_id:
        _adjust_fund_balance(cheque.fund_cluster, cheque.amount)

    cheque.delete()

    _audit(request.user, "Deleted cheque", {
        "id": pk,
        "number": cheque_number,
        "payee_name": cheque_payee
    })

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({"ok": True, "id": pk, "number": cheque_number})

    return redirect('cheque_list')


@login_required
@require_POST
def cheque_advance_status(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)
    is_admin = _is_admin(request.user)

    if not is_admin and cheque.created_by != request.user:
        return redirect('cheque_list')

    flow = ['draft', 'pending', 'released']
    current_idx = flow.index(cheque.status) if cheque.status in flow else -1

    if current_idx >= 0 and current_idx < len(flow) - 1:
        next_status = flow[current_idx + 1]
        action_error = _cheque_action_block_reason(cheque)
        if action_error:
            return redirect(f"{reverse('cheque_list')}?error={quote(action_error)}")
        cheque.status = next_status
        if cheque.status == 'released':
            balance_error = _fund_balance_error(cheque.fund_cluster, cheque.amount)
            if balance_error:
                return redirect(f"{reverse('cheque_list')}?error={quote(balance_error)}")
            days = cheque.stale_after_days or 180
            try:
                base_date = cheque.date or date.today()
                cheque.stale_at = base_date + timedelta(days=days)
            except Exception:
                cheque.stale_at = None
            cheque.save(update_fields=['status', 'stale_at'])
            if cheque.fund_cluster_id:
                _adjust_fund_balance(cheque.fund_cluster, -cheque.amount)
        else:
            cheque.save(update_fields=['status'])
        _audit(request.user, f"Advanced cheque status to {cheque.status}", {"id": cheque.pk})

    return redirect('cheque_list')

# ─────────────────────────────────────────────
# CHECK VALIDATOR (KIOSK)
# ─────────────────────────────────────────────

CHECK_VALIDATOR_DEFAULT_STALE_DAYS = 180


def _find_cheque_by_number(check_no):
    if not check_no:
        return None
    q = str(check_no).strip()
    qs = Cheque.objects.select_related('payee', 'fund_cluster').order_by('-created_at')

    # 1. Try normalized cheque number if digits
    try:
        norm_q = Cheque.normalize_cheque_number(q)
        c = qs.filter(cheque_number=norm_q).first()
        if c:
            return c
    except ValueError:
        pass

    # 2. Exact or case-insensitive cheque number
    c = qs.filter(cheque_number__iexact=q).first()
    if c:
        return c

    # 3. Exact payee name
    c = qs.filter(payee_name__iexact=q).first()
    if c:
        return c

    # 4. Contains payee name (e.g. "bernadette" matches "BERNADETTE G. BAYACA")
    c = qs.filter(payee_name__icontains=q).first()
    if c:
        return c

    # 5. Linked supplier account name or serial number
    c = qs.filter(payee__account_name__icontains=q).first() or qs.filter(payee__mr_or__iexact=q).first()
    if c:
        return c

    # 6. Contains cheque number
    return qs.filter(cheque_number__icontains=q).first()


def _serialize_cheque_for_validator(cheque):
    today = timezone.localdate()
    issue_date = cheque.date or today
    raw_days_elapsed = (today - issue_date).days
    stale_limit_days = cheque.stale_after_days or CHECK_VALIDATOR_DEFAULT_STALE_DAYS

    is_released = cheque.status == 'released'
    is_voided = cheque.status == 'voided'

    if is_released and cheque.stale_at and cheque.date:
        release_date = timezone.localdate(cheque.updated_at) if cheque.updated_at else today
        days_elapsed_out = max(0, (release_date - cheque.date).days)
        days_remaining = max(0, (cheque.stale_at - release_date).days)
        pct_used = 0
        if stale_limit_days > 0:
            pct_used = min(100.0, round((days_elapsed_out / stale_limit_days) * 100.0, 1))
    elif cheque.stale_at:
        try:
            stale_limit_days = cheque.stale_after_days or max(1, (cheque.stale_at - issue_date).days)
        except Exception:
            pass
        days_remaining = (cheque.stale_at - today).days
        days_elapsed_out = max(0, raw_days_elapsed)
        pct_used = 0
        if stale_limit_days > 0:
            pct_used = min(100.0, round((days_elapsed_out / stale_limit_days) * 100.0, 1))
    else:
        days_remaining = stale_limit_days - max(0, raw_days_elapsed)
        days_elapsed_out = max(0, raw_days_elapsed)
        pct_used = 0
        if stale_limit_days > 0:
            pct_used = min(100.0, round((days_elapsed_out / stale_limit_days) * 100.0, 1))

    is_postdated = (not is_released and not is_voided and raw_days_elapsed < 0)
    is_stale = (not is_released and not is_voided and not is_postdated and days_remaining <= 0)

    if is_voided:
        validity = 'voided'
    elif cheque.status == 'stale' or is_stale:
        validity = 'stale'
    elif is_postdated:
        validity = 'postdated'
    else:
        validity = 'valid'

    try:
        account_no = decrypt_value(cheque.account_number) if cheque.account_number else ''
    except Exception:
        account_no = cheque.account_number or ''

    released_display = ''
    if is_released and cheque.updated_at:
        try:
            released_display = to_ph_time(cheque.updated_at).strftime('%b %d, %Y %I:%M %p')
        except Exception:
            released_display = ''

    if validity == 'postdated':
        days_elapsed_out = abs(raw_days_elapsed)
        pct_used = 0

    return {
        "id": cheque.pk,
        "check_no": str(cheque.real_cheque_number or cheque.cheque_number or ''),
        "payee": cheque.payee_name or '',
        "amount_float": float(cheque.amount or 0),
        "bank": cheque.bank_name or '',
        "account_no": account_no,
        "issue_date": issue_date.strftime('%b %d, %Y'),
        "fund_cluster": cheque.fund_cluster.code if cheque.fund_cluster else '',
        "purpose": cheque.purpose or '',
        "status": cheque.status,
        "is_released": is_released,
        "released_display": released_display,
        "validity": validity,
        "is_stale": validity == 'stale',
        "is_postdated": is_postdated,
        "days_elapsed": days_elapsed_out,
        "days_remaining": max(0, days_remaining),
        "stale_limit_days": stale_limit_days,
        "pct_used": pct_used,
    }


@login_required
@require_GET
def check_validator_lookup(request):
    check_no = (request.GET.get('check_no') or '').strip()
    if not check_no:
        return JsonResponse({"found": False})
    cheque = _find_cheque_by_number(check_no)
    if not cheque:
        return JsonResponse({"found": False})
    return JsonResponse({"found": True, "cheque": _serialize_cheque_for_validator(cheque)})


@login_required
@require_POST
def check_validator_release(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        payload = {}

    check_no = str(payload.get('check_no') or '').strip()
    if not check_no:
        return JsonResponse({"ok": False, "error": "Check number is required."}, status=400)

    cheque = _find_cheque_by_number(check_no)
    if not cheque:
        return JsonResponse({"ok": False, "error": "Check record not found."}, status=404)

    data = _serialize_cheque_for_validator(cheque)
    if data["is_released"]:
        return JsonResponse({"ok": False, "error": "This check has already been released/claimed."}, status=400)
    if data["validity"] == 'voided':
        return JsonResponse({"ok": False, "error": "This check is voided and cannot be released."}, status=400)
    if data["validity"] == 'stale':
        return JsonResponse({"ok": False, "error": "This check is stale/expired and cannot be released."}, status=400)
    if data["validity"] == 'postdated':
        return JsonResponse({"ok": False, "error": f"This check is post-dated. It becomes valid on {data['issue_date']}."}, status=400)

    cheque.status = 'released'
    days = cheque.stale_after_days or CHECK_VALIDATOR_DEFAULT_STALE_DAYS
    base_date = cheque.date or timezone.localdate()
    try:
        cheque.stale_at = base_date + timedelta(days=days)
    except Exception:
        cheque.stale_at = None
    cheque.updated_by = request.user
    cheque.save(update_fields=['status', 'stale_at', 'updated_by'])

    if cheque.fund_cluster_id:
        _adjust_fund_balance(cheque.fund_cluster, -cheque.amount)

    _audit(request.user, "Released cheque via Check Validator", {"id": cheque.pk, "number": cheque.cheque_number})

    return JsonResponse({"ok": True, "cheque": _serialize_cheque_for_validator(cheque)})

# ─────────────────────────────────────────────
# REPORTS (IMMUTABLE)
# ─────────────────────────────────────────────

@login_required
def reports(request):
    is_admin = _is_admin(request.user)
    profile = _get_profile(request.user)
    # Fixed permissions by role:
    # - admin: can generate + print
    # - cashier: can generate + print
    # - guest: can generate, but CANNOT print
    can_generate = is_admin or (profile and profile.role in ('cashier', 'guest'))
    can_print = is_admin or (profile and profile.role == 'cashier')
    error = None
    edit_report = None
    missing_report_message = ''

    def _resolve_fund_cluster(fund_cluster_id):
        if not fund_cluster_id:
            return None
        return FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()

    if request.method == "POST" and can_generate:
        report_id = request.POST.get('report_id') or None
        report_type = request.POST.get('report_type', 'cheque_summary')
        date_from_str = request.POST.get('date_from') or None
        date_to_str = request.POST.get('date_to') or None
        notes = request.POST.get('notes', '').strip()
        fund_cluster_id = request.POST.get('fund_cluster') or None
        cheque_status = request.POST.get('cheque_status') or None
        payee = request.POST.get('payee', '').strip()
        selected_rows_raw = request.POST.get('selected_rows') or ''

        existing_report = Report.objects.filter(pk=report_id).first() if report_id else None
        if report_id and not existing_report:
            missing_report_message = 'This report is already deleted or not exist.'
            return redirect(f"{reverse('reports')}?missing_report=1")

        if existing_report:
            report_type = existing_report.report_type
            date_from_str = existing_report.date_from.isoformat() if existing_report.date_from else None
            date_to_str = existing_report.date_to.isoformat() if existing_report.date_to else None
            existing_snapshot = existing_report.data_snapshot or {}
            if not fund_cluster_id:
                fund_cluster_id = str(existing_snapshot.get('fund_cluster_id') or '') or None

        date_from = date_from_str if date_from_str else None
        date_to = date_to_str if date_to_str else None
        fund_cluster = _resolve_fund_cluster(fund_cluster_id) if report_type in ('cheque_summary', 'radai_summary') else None
        selected_row_ids = [value.strip() for value in selected_rows_raw.split(',') if value.strip()]

        if report_type == 'cheque_summary' and not cheque_status:
            return redirect(f"{reverse('reports')}?error=cheque_status_required")

        snapshot = _build_report_snapshot(report_type, date_from, date_to, fund_cluster_id=fund_cluster.pk if fund_cluster else None, cheque_status=cheque_status, payee=payee if report_type == 'radai_summary' else None)
        snapshot = _filter_report_snapshot_rows(snapshot, selected_row_ids)
        if cheque_status and not existing_report:
            snapshot["cheque_status"] = cheque_status
        type_label = dict(Report.REPORT_TYPES).get(report_type, report_type)
        title = f"{type_label}"
        if date_from:
            title += f" from {date_from}"
        if date_to:
            title += f" to {date_to}"

        if fund_cluster:
            snapshot["fund_cluster_id"]   = fund_cluster.pk
            snapshot["fund_cluster_code"] = fund_cluster.code
            snapshot["fund_cluster_name"] = fund_cluster.name
            snapshot["fund_cluster"]      = str(fund_cluster)
            snapshot["bank_name"]         = fund_cluster.bank_name or ''
            snapshot["account_number"]    = fund_cluster.account_number or ''
            snapshot["bank_account"] = (
                f"{fund_cluster.bank_name} / {fund_cluster.account_number}"
                if fund_cluster.bank_name and fund_cluster.account_number
                else fund_cluster.bank_name or fund_cluster.account_number or ''
            )

        if payee and report_type == 'radai_summary':
            snapshot["payee"] = payee

        # Enrich snapshot with creator details
        snapshot["generated_by_username"] = request.user.username
        snapshot["generated_by_name"] = request.user.get_full_name() or request.user.username
        snapshot["certification_name"] = _get_certification_name(request.user)

        # Generate and save report number
        report_prefix = None
        if date_to_str and len(date_to_str) >= 7:
            report_prefix = date_to_str[:7]
        elif date_from_str and len(date_from_str) >= 7:
            report_prefix = date_from_str[:7]
        
        if not report_prefix:
            report_prefix = timezone.localdate().strftime("%Y-%m")

        settings_obj = SystemSetting.get_settings()
        if existing_report:
            report_no = existing_report.data_snapshot.get('report_no') if existing_report.data_snapshot else None
            if not report_no:
                report_no = settings_obj.next_report_number(prefix=report_prefix)
                SystemSetting.record_report_number(report_no)
            snapshot["report_no"] = report_no
        else:
            report_no = settings_obj.next_report_number(prefix=report_prefix)
            snapshot["report_no"] = report_no
            SystemSetting.record_report_number(report_no)

        if existing_report:
            existing_report.title = title
            existing_report.report_type = report_type
            existing_report.date_from = date_from
            existing_report.date_to = date_to
            existing_report.data_snapshot = snapshot
            existing_report.notes = notes
            existing_report.generated_by = request.user
            existing_report.save(update_fields=['title', 'report_type', 'date_from', 'date_to', 'data_snapshot', 'notes', 'generated_by'])
            _audit(request.user, "Updated report", {"id": existing_report.pk, "type": report_type})
            return redirect('report_detail', pk=existing_report.pk)

        rpt = Report.objects.create(
            title=title,
            report_type=report_type,
            generated_by=request.user,
            date_from=date_from,
            date_to=date_to,
            data_snapshot=snapshot,
            notes=notes,
        )
        _audit(request.user, "Generated report", {"id": rpt.pk, "type": report_type})
        return redirect('report_detail', pk=rpt.pk)

    edit_report_id = request.GET.get('edit') or None
    if edit_report_id:
        edit_report = Report.objects.filter(pk=edit_report_id).first()
        if not edit_report:
            missing_report_message = 'This report is already deleted or not exist.'

    if request.GET.get('missing_report'):
        missing_report_message = 'This report is already deleted or not exist.'

    error_param = request.GET.get('error', '').strip()
    if error_param == 'cheque_status_required':
        error = 'Cheque Status is required for Cheque Summary reports.'

    preselected_type = request.GET.get('report_type', '').strip()
    if preselected_type not in dict(Report.REPORT_TYPES):
        preselected_type = ''

    report_list = Report.objects.select_related('generated_by').order_by('-generated_at')
    system_setting = SystemSetting.get_settings()
    fund_clusters = FundCluster.objects.filter(is_active=True).order_by('code')
    radai_payees = list(
        Radai.objects.exclude(account_name='')
        .values_list('account_name', flat=True)
        .distinct()
        .order_by('account_name')
    )
    context = _page_context(request, is_admin=is_admin, page_title="Reports", extra={
        "report_list": report_list,
        "report_types": Report.REPORT_TYPES,
        "error": error,
        "today": date.today().isoformat(),
        "fund_clusters": fund_clusters,
        "radai_payees": radai_payees,
        "system_setting": system_setting,
        "edit_report": edit_report,
        "missing_report_message": missing_report_message,
        "can_generate": can_generate,
        "can_print": can_print,
        "preselected_report_type": preselected_type,
    })
    return render(request, "admin_panel/reports.html", context)


@login_required
@require_POST
def report_delete(request, pk):
    if not _is_admin(request.user):
        return redirect('reports')

    report = get_object_or_404(Report, pk=pk)
    report_title = report.title
    report_type = report.report_type
    report.delete()
    _audit(request.user, "Deleted report", {"title": report_title, "type": report_type, "id": pk})
    return redirect('reports')


def _build_report_snapshot(report_type, date_from=None, date_to=None, fund_cluster_id=None, cheque_status=None, payee=None):
    snapshot = {"generated_at": timezone.now().isoformat(), "type": report_type}

    def apply_date_filter(qs, date_field='created_at'):
        if date_from:
            if date_field == 'date':
                qs = qs.filter(**{f"{date_field}__gte": date_from})
            else:
                qs = qs.filter(**{f"{date_field}__date__gte": date_from})
        if date_to:
            if date_field == 'date':
                qs = qs.filter(**{f"{date_field}__lte": date_to})
            else:
                qs = qs.filter(**{f"{date_field}__date__lte": date_to})
        return qs

    if report_type == 'cheque_summary':
        # Use the cheque issue date, not created_at, so the selected report range matches the issued checks.
        qs = apply_date_filter(Cheque.objects.all(), 'date')
        fund_cluster = None
        if fund_cluster_id:
            fund_cluster = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
            if fund_cluster:
                qs = qs.filter(fund_cluster=fund_cluster)
        if cheque_status and cheque_status != 'all':
            qs = qs.filter(status=cheque_status)
        by_status = {s: qs.filter(status=s).count() for s, _ in Cheque.STATUS_CHOICES}
        total_amount = qs.aggregate(t=Sum('amount'))['t'] or 0
        rows = []
        for c in qs.select_related('payee', 'fund_cluster').order_by('-date')[:500]:
            rows.append({
                "id": c.pk,
                "number": c.cheque_number,
                "payee": c.payee_name,
                "fund": c.fund_cluster.code if c.fund_cluster else '',
                "amount": str(c.amount),
                "date": str(c.date),
                "status": c.status,
                # Report / disbursement fields
                "dv_payroll": c.dv_payroll_no or '',
                "ors_burs": c.ors_burs_no or '',
                "responsibility_center": c.responsibility_center or '',
                "uacs": c.uacs_object_code or '',
                "nature": c.nature_of_payment or '',
                "professional_tax": str(c.professional_tax) if c.professional_tax is not None else '',
                "tax_5": str(c.tax_5_3) if c.tax_5_3 is not None else '',
                "tax_2": str(c.tax_3_1) if c.tax_3_1 is not None else '',
            })
        # estimate sheet count (20 rows per sheet) and compute cheque number range
        sheet_count = _estimate_report_sheets(len(rows))
        # determine first and last cheque numbers numerically if possible, else by order
        cheque_nums = [r.get('number') for r in rows if r.get('number')]
        first_cheque = ''
        last_cheque = ''
        try:
            # build list of (int_value, original_string) for numeric cheque numbers
            numeric_pairs = []
            for s in cheque_nums:
                if isinstance(s, int):
                    numeric_pairs.append((int(s), str(s)))
                elif isinstance(s, str) and s.isdigit():
                    numeric_pairs.append((int(s), s))
            if numeric_pairs:
                numeric_pairs.sort(key=lambda x: x[0])
                first_cheque = numeric_pairs[0][1]
                last_cheque = numeric_pairs[-1][1]
            else:
                # fallback: use first/last from rows list order (rows ordered by -date)
                if cheque_nums:
                    # rows is ordered by -date, so first in list is latest -> treat last_cheque as first element
                    last_cheque = cheque_nums[0]
                    first_cheque = cheque_nums[-1]
        except Exception:
            if cheque_nums:
                last_cheque = cheque_nums[0]
                first_cheque = cheque_nums[-1]

        snapshot.update({"by_status": by_status, "total_amount": str(total_amount), "rows": rows, "sheet_count": sheet_count, "check_first": first_cheque, "check_last": last_cheque, "cheque_count": len(rows)})
        if fund_cluster:
            snapshot["fund_cluster_id"]   = fund_cluster.pk
            snapshot["fund_cluster"]      = str(fund_cluster)
            snapshot["fund_cluster_code"] = fund_cluster.code
            snapshot["fund_cluster_name"] = fund_cluster.name
            snapshot["bank_name"]         = fund_cluster.bank_name or ''
            snapshot["account_number"]    = fund_cluster.account_number or ''
            snapshot["bank_account"] = (
                f"{fund_cluster.bank_name} / {fund_cluster.account_number}"
                if fund_cluster.bank_name and fund_cluster.account_number
                else fund_cluster.bank_name or fund_cluster.account_number or ''
            )

    elif report_type == 'fund_summary':
        qs = FundCluster.objects.all()
        rows = []
        for f in qs:
            cheques = apply_date_filter(f.cheque_set.all())
            released = cheques.filter(status='released').aggregate(t=Sum('amount'))['t'] or 0
            rows.append({
                "id": f.pk,
                "code": f.code, "name": f.name,
                "balance": str(f.balance),
                "released_amount": str(released),
                "cheque_count": cheques.count(),
                "is_active": f.is_active,
            })
        snapshot["rows"] = rows

    elif report_type == 'supplier_summary':
        rows = []
        for s in Supplier.objects.all().order_by('-date', '-pk'):
            cheques = apply_date_filter(s.cheque_set.all())
            total = cheques.aggregate(t=Sum('amount'))['t'] or 0
            rows.append({
                "id": s.pk,
                "mr_or": s.mr_or,
                "mr": s.mr,
                "code_or": s.code_or,
                "code_or2": s.code_or2,
                "series_month": s.series_month,
                "date": s.date.isoformat() if s.date else '',
                "or_number": s.or_number,
                "series_day": s.series_day,
                "account_number": s.account_number,
                "account_name": s.account_name,
                "code_line": s.code_line,
                "line": s.line,
                "code_noc": s.code_noc,
                "nature_of_collections": s.nature_of_collections,
                "amount": str(s.amount),
                "remarks": s.remarks,
                "status": s.status,
                "cheque_count": cheques.count(),
                "total_amount": str(total),
            })
        snapshot["rows"] = rows

    elif report_type == 'radai_summary':
        qs = Radai.objects.select_related('created_by', 'fund_cluster').all()
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        if fund_cluster_id:
            qs = qs.filter(fund_cluster_id=fund_cluster_id)
        if payee:
            qs = qs.filter(account_name=payee)
        rows = []
        fc_code = ''
        fc_name = ''
        bank_name = ''
        acct_no = ''
        if fund_cluster_id:
            fc = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
            if fc:
                fc_code = fc.code
                fc_name = str(fc)
                bank_name = fc.bank_name or ''
                acct_no = fc.account_number or ''
        for r in qs.order_by('-date', '-pk'):
            extras = (r.raw_import or {}).get('radai_extras') or {}
            if not fc_code and r.fund_cluster:
                fc_code = r.fund_cluster.code
                fc_name = str(r.fund_cluster)
                bank_name = r.fund_cluster.bank_name or ''
                acct_no = r.fund_cluster.account_number or ''
            rows.append({
                "id": r.pk,
                "date": r.date.isoformat() if r.date else '',
                "mr_or": r.mr_or,
                "dv_payroll": str(extras.get('dv_payroll') or ''),
                "ors_burs_no": r.reference_code or '',

                "account_name": r.account_name,
                "uacs": str(extras.get('uacs') or ''),
                "nature_of_collections": r.nature_of_collections,
                "amount": str(r.amount),
            })
        snapshot["rows"] = rows
        snapshot["fund_cluster_code"] = fc_code
        snapshot["fund_cluster_name"] = fc_name
        snapshot["fund_cluster"] = fc_name
        snapshot["fund_cluster_id"] = fund_cluster_id
        snapshot["bank_name"] = bank_name
        snapshot["account_number"] = acct_no
        snapshot["bank_account"] = f"{bank_name} / {acct_no}" if bank_name and acct_no else (bank_name or acct_no or '')
        if payee:
            snapshot["payee"] = payee
        # Save first and last serial numbers for certification text
        serial_numbers = [r["mr_or"] for r in rows if r.get("mr_or")]
        snapshot["ada_first"] = serial_numbers[-1] if serial_numbers else ''
        snapshot["ada_last"] = serial_numbers[0] if serial_numbers else ''

    elif report_type == 'audit_summary':
        qs = apply_date_filter(AuditLog.objects.select_related('admin'), 'timestamp')
        rows = [{"id": a.pk, "user": a.admin.username if a.admin else '', "action": a.action, "timestamp": a.timestamp.isoformat()} for a in qs.order_by('-timestamp')[:500]]
        snapshot["rows"] = rows

    return snapshot


def _report_headers_and_rows(report_type, snapshot):
    rows = snapshot.get("rows") or []

    if report_type == "supplier_summary":
        headers = [
            "MR OR",
            "MR",
            "CODE OR",
            "CODE OR 2",
            "SERIES/MONTH",
            "DATE",
            "OR NUMBER",
            "SERIES/DAY",
            "PAYEE",
            "CODE LINE",
            "LINE",
            "CODE NOC",
            "NATURE OF COLLECTIONS",
            "AMOUNT",
            "REMARKS",
        ]
        table_rows = []
        for row in rows:
            table_rows.append([
                row.get("mr_or") or "",
                row.get("mr") or "",
                row.get("code_or") or "",
                row.get("code_or2") or "",
                row.get("series_month") or "",
                row.get("date") or "",
                row.get("or_number") or "",
                row.get("series_day") or "",
                row.get("account_name") or "",
                row.get("code_line") or "",
                row.get("line") or "",
                row.get("code_noc") or "",
                row.get("nature_of_collections") or "",
                (lambda v: ("₱ " + f"{Decimal(v):,.2f}") if v not in (None, "") else "")(row.get("amount")) or "",
                row.get("remarks") or "",
            ])
        return headers, table_rows

    if report_type == "cheque_summary":
        # Detailed layout matching "Reports of Check Issued" for preview and PDF
        headers = [
            "Date",
            "Serial No.",
            "DV/Payroll No.",
            "ORS/BURS No.",
            "Responsibility Center Code",
            "Payee",
            "UACS Object Code",
            "Nature of Payment",
            "Gross Amount",
            "Professional Tax",
            "Tax 5%/3%",
            "Tax 2%/1%",
            "Net Amount",
        ]
        table_rows = []
        for row in rows:
            date_val = row.get("date") or ""
            serial = row.get("number") or ""
            dv_payroll = row.get("dv_payroll") or ""
            ors_burs = row.get("ors_burs") or ""
            resp_center = row.get("responsibility_center") or ""
            payee = row.get("payee") or ""
            uacs = row.get("uacs") or ""
            nature = row.get("nature") or ""

            def fmt_money(v):
                try:
                    d = Decimal(str(v))
                    return "\u20b1 " + format(d, ",.2f")
                except Exception:
                    return ''

            def fmt_money_blank(v):
                """Like fmt_money but returns empty string if value is blank/None/zero."""
                if v in (None, '', '0', '0.00'):
                    return ''
                try:
                    d = Decimal(str(v))
                    if d == 0:
                        return ''
                    return "\u20b1 " + format(d, ",.2f")
                except Exception:
                    return ''

            gross_val = row.get("amount") or '0'
            prof_tax_val = row.get("professional_tax") or ''
            tax_5_val = row.get("tax_5") or ''
            tax_2_val = row.get("tax_2") or ''

            gross = fmt_money(gross_val)
            prof_tax = fmt_money_blank(prof_tax_val)
            tax_5 = fmt_money_blank(tax_5_val)
            tax_2 = fmt_money_blank(tax_2_val)

            # Compute net amount = gross - deductions
            try:
                net_val = Decimal(str(gross_val or 0)) \
                    - Decimal(str(prof_tax_val or 0)) \
                    - Decimal(str(tax_5_val or 0)) \
                    - Decimal(str(tax_2_val or 0))
                net = fmt_money(net_val)
            except Exception:
                net = gross

            table_rows.append([date_val, serial, dv_payroll, ors_burs, resp_center, payee, uacs, nature, gross, prof_tax, tax_5, tax_2, net])
        return headers, table_rows

    if report_type == "radai_summary":
        headers = [
            "Date", "Serial No.", "DV/Payroll No.", "ORS/BURS No.",
            "Payee", "UACS Object Code",
            "Nature of Payment", "Amount",
        ]
        table_rows = []
        for row in rows:
            table_rows.append([
                row.get("date") or "",
                row.get("mr_or") or "",
                row.get("dv_payroll") or "",
                row.get("ors_burs_no") or "",
                row.get("account_name") or "",
                row.get("uacs") or "",
                row.get("nature_of_collections") or "",
                (lambda v: ("₱ " + f"{Decimal(v):,.2f}") if v not in (None, "") else "")(row.get("amount")) or "",
            ])
        # Compute total amount for RADAI summary report
        total_amount = sum(Decimal(r.get("amount") or 0) for r in rows)
        return headers, table_rows

    if report_type == "fund_summary":
        headers = ["#", "Code", "Fund Name", "Balance", "Released", "Cheques", "Active"]
        table_rows = []
        for idx, row in enumerate(rows, start=1):
            table_rows.append([
                str(idx),
                row.get("code") or "",
                row.get("name") or "",
                (lambda v: ("₱ " + f"{Decimal(v):,.2f}") if v not in (None, "") else "")(row.get('balance')),
                (lambda v: ("₱ " + f"{Decimal(v):,.2f}") if v not in (None, "") else "")(row.get('released_amount')),
                str(row.get("cheque_count") or 0),
                "Yes" if row.get("is_active") else "No",
            ])
        return headers, table_rows

    headers = ["#", "Timestamp", "User", "Action"]
    table_rows = []
    for idx, row in enumerate(rows, start=1):
        table_rows.append([
            str(idx),
            row.get("timestamp") or "",
            row.get("user") or "",
            row.get("action") or "",
        ])
    return headers, table_rows


def _filter_report_snapshot_rows(snapshot, selected_row_ids):
    selected = {str(row_id).strip() for row_id in (selected_row_ids or []) if str(row_id).strip()}
    if not selected:
        return snapshot

    filtered = dict(snapshot)
    rows = snapshot.get("rows") or []
    filtered_rows = [row for row in rows if str(row.get("id")) in selected]
    filtered["rows"] = filtered_rows
    filtered["selected_row_ids"] = list(selected)
    filtered["selected_row_count"] = len(filtered_rows)
    return filtered


def _get_certification_name(user, report=None):
    if report and not report.generated_by:
        if report.data_snapshot and isinstance(report.data_snapshot, dict):
            snap_cert = report.data_snapshot.get('certification_name')
            if snap_cert:
                return snap_cert
            snap_name = report.data_snapshot.get('generated_by_name')
            if snap_name:
                return snap_name
        return "DEE JAY CRISTOBAL"

    from cashier.models import Profile
    officer_profile = Profile.objects.filter(is_disbursing_officer=True).first()
    u = officer_profile.user if officer_profile else user

    if not u:
        return ""
    first = u.first_name.strip()
    last = u.last_name.strip()
    
    # Handle swapped first/last name if the DB was not updated yet or in other sessions
    if u.username == 'admindeejay':
        if first.upper() == 'CRISTOBAL':
            first, last = last, first

    middle = ""
    if hasattr(u, 'profile') and u.profile and u.profile.middle_initial:
        mi = u.profile.middle_initial.strip()
        if mi:
            if not mi.endswith('.'):
                mi += '.'
            middle = mi

    parts = []
    if first:
        parts.append(first)
    if middle:
        parts.append(middle)
    if last:
        parts.append(last)
    
    return " ".join(parts) or u.get_full_name() or u.username


def _report_snapshot_for_display(snapshot):
    display_snapshot = dict(snapshot or {})

    # Build fund_cluster_display — try stored code, then full string, then DB lookup
    fc_display = (
        display_snapshot.get("fund_cluster_code")
        or display_snapshot.get("fund_cluster")
        or ""
    )
    if not fc_display:
        fc_id = display_snapshot.get("fund_cluster_id")
        if fc_id:
            try:
                fc = FundCluster.objects.filter(pk=fc_id).first()
                if fc:
                    fc_display = fc.code
                    display_snapshot.setdefault("fund_cluster",      str(fc))
                    display_snapshot.setdefault("fund_cluster_code", fc.code)
                    display_snapshot.setdefault("fund_cluster_name", fc.name)
                    display_snapshot.setdefault("bank_name",         fc.bank_name or '')
                    display_snapshot.setdefault("account_number",    fc.account_number or '')
                    display_snapshot.setdefault("bank_account",
                        f"{fc.bank_name} / {fc.account_number}" if fc.bank_name and fc.account_number
                        else fc.bank_name or fc.account_number or ''
                    )
            except Exception:
                pass
    if " — " in fc_display:
        fc_display = fc_display.split(" — ", 1)[0].strip()
    elif " - " in fc_display:
        fc_display = fc_display.split(" - ", 1)[0].strip()
    display_snapshot["fund_cluster_display"] = fc_display

    # Ensure account_number is exposed separately (older snapshots only have bank_account combined)
    if not display_snapshot.get("account_number") and display_snapshot.get("bank_account"):
        combined = display_snapshot["bank_account"]
        # "BANK / ACCOUNTNO" → split on " / "
        if " / " in combined:
            display_snapshot["account_number"] = combined.split(" / ", 1)[1].strip()
        else:
            display_snapshot["account_number"] = combined.strip()

    return display_snapshot


def _estimate_report_sheets(row_count):
    return max(1, (int(row_count or 0) + 24) // 25)


def _format_period_covered(date_from=None, date_to=None):
    start_text = str(date_from or '').strip()
    end_text = str(date_to or '').strip()
    if not start_text and not end_text:
        return 'All time'

    try:
        start_date = date.fromisoformat(start_text) if start_text else None
        end_date = date.fromisoformat(end_text) if end_text else None
    except Exception:
        if start_text and end_text:
            return f"{start_text} to {end_text}"
        return start_text or end_text or 'All time'

    if start_date and end_date:
        if start_date.year == end_date.year and start_date.month == end_date.month:
            return f"{start_date.strftime('%B')} {start_date.day} - {end_date.day}, {end_date.year}"
        
        return f"{start_date.strftime('%B')} {start_date.day}, {start_date.year} to {end_date.strftime('%B')} {end_date.day}, {end_date.year}"

    if start_date:
        return f"{start_date.strftime('%B')} {start_date.day}, {start_date.year}"
    if end_date:
        return f"{end_date.strftime('%B')} {end_date.day}, {end_date.year}"
    return start_text or end_text or 'All time'


def _append_checks_certification(story, request_user, certification_width, sheet_count_text=''):
    from reportlab.lib import colors

    cert_name = _get_certification_name(request_user)

    cert_style = ParagraphStyle(name="cert", fontName="Helvetica", fontSize=8.2, alignment=1, leading=10)
    cert_title_style = ParagraphStyle(name="cert_title", fontName="Helvetica-Bold", fontSize=9, alignment=1, leading=11)
    cert_name_style = ParagraphStyle(name="cert_name", fontName="Helvetica-Bold", fontSize=8.5, alignment=1, leading=10)

    certification = Table([
        [Paragraph("CERTIFICATION", cert_title_style)],
        [Paragraph(
            f"I hereby certify on my official oath that this Report of Checks issued in {sheet_count_text or '___'} sheet(s) is a full, true and correct statement of all checks issued by me during the period stated above for which Check Nos. are actually issued by me in payment of obligations shown in the attached disbursement vouchers/payroll.",
            cert_style,
        )],
        [Spacer(1, 5)],
        [Paragraph(cert_name, cert_name_style)],
        [Paragraph("Name and Signature of Disbursing Officer/Cashier", cert_style)],
    ], colWidths=[certification_width])
    certification.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.black),
    ]))
    story.append(Spacer(1, 10))
    story.append(certification)


@login_required
@require_GET
def report_export_preview(request):
    profile = _get_profile(request.user)
    can_generate = _is_admin(request.user) or (profile and profile.role in ('cashier', 'guest'))
    if not can_generate:
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    report_type = request.GET.get("report_type", "supplier_summary")
    if report_type not in dict(Report.REPORT_TYPES):
        return JsonResponse({"ok": False, "error": "Invalid report type"}, status=400)

    date_from = request.GET.get("date_from") or None
    date_to = request.GET.get("date_to") or None
    edit_report_id = request.GET.get("edit_report_id") or None
    # additional header meta for checks report
    entity_name = request.GET.get("entity_name") or None
    fund_cluster_id = request.GET.get("fund_cluster") or None
    payee = request.GET.get("payee") or None
    bank_account = request.GET.get("bank_account") or None
    report_no = request.GET.get("report_no") or None
    sheets = request.GET.get("sheets") or None
    edit_report = Report.objects.filter(pk=edit_report_id).first() if edit_report_id else None
    if edit_report_id and not edit_report:
        return JsonResponse({"ok": False, "error": "This report is already deleted or not exist.", "missing_report": True}, status=404)

    if edit_report:
        report_type = edit_report.report_type
        if not date_from:
            date_from = edit_report.date_from.isoformat() if edit_report.date_from else None
        if not date_to:
            date_to = edit_report.date_to.isoformat() if edit_report.date_to else None
    else:
        cheque_status = request.GET.get("cheque_status") or None
        fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first() if (report_type in ('cheque_summary', 'radai_summary') and fund_cluster_id) else None
        snapshot = _build_report_snapshot(report_type, date_from, date_to, fund_cluster_id=fund_cluster_obj.pk if fund_cluster_obj else None, cheque_status=cheque_status, payee=payee if report_type == 'radai_summary' else None)

    if edit_report:
        base_snapshot = edit_report.data_snapshot or {}
        selected_row_ids = [str(row.get('id')) for row in (base_snapshot.get('rows') or []) if row.get('id') is not None]
        preview_fund_cluster_id = base_snapshot.get('fund_cluster_id') or None
        preview_date_from = edit_report.date_from.isoformat() if edit_report.date_from else None
        preview_date_to = edit_report.date_to.isoformat() if edit_report.date_to else None
        fund_cluster_obj = FundCluster.objects.filter(pk=preview_fund_cluster_id, is_active=True).first() if preview_fund_cluster_id else None
        snapshot = _build_report_snapshot(report_type, preview_date_from, preview_date_to, fund_cluster_id=fund_cluster_obj.pk if fund_cluster_obj else None, cheque_status=base_snapshot.get('cheque_status'), payee=base_snapshot.get('payee') if report_type == 'radai_summary' else None)
        if fund_cluster_obj:
            snapshot["fund_cluster_code"] = fund_cluster_obj.code
            snapshot["fund_cluster_id"] = fund_cluster_obj.pk
            snapshot["fund_cluster"] = str(fund_cluster_obj)
            snapshot["bank_account"] = f"{fund_cluster_obj.bank_name or ''}{' / ' if fund_cluster_obj.bank_name and fund_cluster_obj.account_number else ''}{fund_cluster_obj.account_number or ''}".strip()
    else:
        selected_row_ids = []
    # ensure variables used in the response are always defined
    fund_cluster = None
    headers, rows = _report_headers_and_rows(report_type, snapshot)
    settings_obj = SystemSetting.get_settings()
    preview_prefix = None
    if date_to and len(date_to) >= 7:
        preview_prefix = date_to[:7]
    elif date_from and len(date_from) >= 7:
        preview_prefix = date_from[:7]

    if report_type == 'cheque_summary':
        report_no = report_no or settings_obj.next_report_number(prefix=preview_prefix)
        sheets = sheets or str(_estimate_report_sheets(len(rows)))
        entity_name = entity_name or settings_obj.entity_name or settings_obj.system_name
        fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first() if (not edit_report and fund_cluster_id) else None
        if edit_report:
            fund_cluster_id = snapshot.get('fund_cluster_id')
            if fund_cluster_id:
                fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
        if fund_cluster_obj:
            if not bank_account:
                bank_account = f"{fund_cluster_obj.bank_name or ''}{' / ' if fund_cluster_obj.bank_name and fund_cluster_obj.account_number else ''}{fund_cluster_obj.account_number or ''}".strip()
            snapshot["fund_cluster_code"] = fund_cluster_obj.code
            fund_cluster = fund_cluster_obj.code
        else:
            fund_cluster = None
            if not bank_account:
                bank_account = ''

    if report_type == 'radai_summary':
        report_no = report_no or settings_obj.next_report_number(prefix=preview_prefix)
        sheets = sheets or str(_estimate_report_sheets(len(rows)))
        entity_name = entity_name or settings_obj.entity_name or settings_obj.system_name
        fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first() if (not edit_report and fund_cluster_id) else None
        if edit_report:
            fund_cluster_id = snapshot.get('fund_cluster_id')
            if fund_cluster_id:
                fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
        if fund_cluster_obj:
            if not bank_account:
                bank_account = f"{fund_cluster_obj.bank_name or ''}{' / ' if fund_cluster_obj.bank_name and fund_cluster_obj.account_number else ''}{fund_cluster_obj.account_number or ''}".strip()
            snapshot["fund_cluster_code"] = fund_cluster_obj.code
            fund_cluster = fund_cluster_obj.code
        else:
            fund_cluster = snapshot.get('fund_cluster_code', '')
            if not bank_account:
                bank_account = snapshot.get('bank_account', '')

    # For supplier summary and radai summary, return full preview data (no 500-row cap).
    is_full_preview = (report_type in ('supplier_summary', 'radai_summary'))
    preview_rows = rows if is_full_preview else rows[:500]
    # preview_items correspond to snapshot rows paired with table rows
    all_preview_items = [{"id": row.get("id"), "cells": cells} for row, cells in zip(snapshot.get("rows") or [], rows)]
    preview_items = all_preview_items if is_full_preview else all_preview_items[:500]

    return JsonResponse({
        "ok": True,
        "report_type": report_type,
        "report_label": dict(Report.REPORT_TYPES).get(report_type, report_type),
        "headers": headers,
        "rows": rows,
        "row_count": len(rows),
        "preview_rows": preview_rows,
        "preview_items": preview_items,
        "selected_row_ids": selected_row_ids,
        "date_from": date_from,
        "date_to": date_to,
        "entity_name": entity_name,
        "fund_cluster": fund_cluster,
        "bank_account": bank_account,
        "report_no": report_no,
        "sheets": sheets,
    })


@login_required
@require_GET
def fund_cluster_info(request):
    # Return bank name and account number for a fund cluster if available.
    fc_id = request.GET.get('fc_id')
    if not fc_id:
        return JsonResponse({"ok": False, "error": "missing fc_id"}, status=400)
    try:
        fc = FundCluster.objects.get(pk=int(fc_id))
    except Exception:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    bank = fc.bank_name or ''
    account = fc.account_number or ''
    if not bank and not account:
        cheque = Cheque.objects.filter(fund_cluster=fc).order_by('-created_at').first()
        bank = cheque.bank_name if cheque and cheque.bank_name else ''
        account = cheque.account_number if cheque and cheque.account_number else ''
    return JsonResponse({"ok": True, "bank_name": bank, "account_number": account})


@login_required
@require_GET
def export_report_pdf(request):
    profile = _get_profile(request.user)
    can_generate = _is_admin(request.user) or (profile and profile.role in ('cashier', 'guest'))
    if not can_generate:
        return redirect("reports")

    report_type = request.GET.get("report_type", "supplier_summary")
    if report_type not in dict(Report.REPORT_TYPES):
        report_type = "supplier_summary"

    date_from = request.GET.get("date_from") or None
    date_to = request.GET.get("date_to") or None
    cheque_status = request.GET.get("cheque_status") or None
    snapshot = _build_report_snapshot(report_type, date_from, date_to, cheque_status=cheque_status)
    headers, table_rows = _report_headers_and_rows(report_type, snapshot)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

    response = HttpResponse(content_type="application/pdf")
    filename = f"{report_type}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        response,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
    )

    meta_style = ParagraphStyle(name="meta", fontName="Helvetica", fontSize=9, alignment=0)
    school_name_style = ParagraphStyle(name="school_name", fontName="Helvetica-Bold", fontSize=12, alignment=1)
    school_sub_style = ParagraphStyle(name="school_sub", fontName="Helvetica", fontSize=9, alignment=1)

    settings_obj = SystemSetting.get_settings()
    logo_flowable = Spacer(12 * mm, 12 * mm)
    if settings_obj.system_logo:
        try:
            logo_flowable = Image(settings_obj.system_logo.path, width=12 * mm, height=12 * mm)
        except Exception:
            logo_flowable = Spacer(12 * mm, 12 * mm)

    campus_name = getattr(settings_obj, 'system_name', 'SYSTEM NAME') or 'SYSTEM NAME'
    campus_subtitle = getattr(settings_obj, 'system_subname', '') or ''
    campus_address = getattr(settings_obj, 'system_address', '') or ''
    report_label = dict(Report.REPORT_TYPES).get(report_type, report_type)
    available_width = landscape(A4)[0] - doc.leftMargin - doc.rightMargin

    if report_type == 'cheque_summary':
        from reportlab.pdfgen import canvas

        entity_name = request.GET.get('entity_name') or settings_obj.entity_name or settings_obj.system_name
        fund_cluster_id = request.GET.get('fund_cluster') or None
        fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first() if fund_cluster_id else None
        fund_cluster = fund_cluster_obj.code if fund_cluster_obj else ''
        bank_account = request.GET.get('bank_account') or ''
        if fund_cluster_obj:
            bank_account = fund_cluster_obj.account_number or ''
        elif bank_account:
            if ' / ' in bank_account:
                bank_account = bank_account.split(' / ', 1)[1].strip()
            elif '/' in bank_account:
                bank_account = bank_account.split('/', 1)[1].strip()
        pdf_prefix = None
        if date_to and len(date_to) >= 7:
            pdf_prefix = date_to[:7]
        elif date_from and len(date_from) >= 7:
            pdf_prefix = date_from[:7]
        report_no = request.GET.get('report_no') or settings_obj.next_report_number(prefix=pdf_prefix)
        snapshot = _build_report_snapshot(report_type, date_from, date_to, fund_cluster_id=fund_cluster_obj.pk if fund_cluster_obj else None)
        headers, table_rows = _report_headers_and_rows(report_type, snapshot)
        sheet_text = request.GET.get('sheets') or str(_estimate_report_sheets(len(table_rows)))

        class PageCountCanvas(canvas.Canvas):
            last_page_count = 1

            def save(self):
                PageCountCanvas.last_page_count = self._pageNumber
                super().save()

        def _build_checks_story(sheets_value):
            story = []

            appendix_style = ParagraphStyle(name="appendix", fontName="Helvetica-Oblique", fontSize=9, alignment=2)
            appendix_flow = Paragraph("Appendix 35", appendix_style)
            story.append(appendix_flow)
            story.append(Spacer(1, 2))

            left_block = Table([
                [Paragraph('Entity Name', meta_style), Paragraph(entity_name, meta_style)],
                [Paragraph('Fund Cluster', meta_style), Paragraph(fund_cluster, meta_style)],
                [Paragraph('Bank Name/Account No.', meta_style), Paragraph(bank_account, meta_style)],
            ], colWidths=[30 * mm, 60 * mm])
            left_block.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))

            center_block = Table([
                [Paragraph('REPORTS OF CHECK ISSUED', school_name_style)],
                [Paragraph(f'Period Covered: {_format_period_covered(date_from, date_to)}', school_sub_style)],
            ], colWidths=[available_width - (120 * mm)])
            center_block.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ]))

            right_block = Table([
                [Paragraph('Report No.', meta_style), Paragraph(report_no, meta_style)],
                [Paragraph('No. Of Sheets', meta_style), Paragraph(sheets_value, meta_style)],
            ], colWidths=[30 * mm, 30 * mm])
            right_block.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ]))

            header_row = Table([[left_block, center_block, right_block]], colWidths=[90 * mm, available_width - (90 * mm + 60 * mm), 60 * mm])
            header_row.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            story.append(header_row)
            story.append(Spacer(1, 4))

            table_data = [headers] + table_rows[:500]
            widths_mm = [12, 12, 16, 16, 18, 48, 18, 50, 22, 16, 12, 12, 22]
            col_widths = [w * mm for w in widths_mm[:len(headers)]]
            data_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            data_table.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 0.55, colors.black),
                ('INNERGRID', (0, 0), (-1, -1), 0.35, colors.black),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7.8),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2.5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2.5),
            ]))
            story.append(data_table)
            _append_checks_certification(story, request.user, available_width, sheets_value)
            return story

        temp_buffer = io.BytesIO()
        temp_doc = SimpleDocTemplate(
            temp_buffer,
            pagesize=landscape(A4),
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=10 * mm,
            bottomMargin=12 * mm,
        )
        temp_doc.build(_build_checks_story(''), canvasmaker=PageCountCanvas)
        sheets_value = str(PageCountCanvas.last_page_count or 1)

        response = HttpResponse(content_type="application/pdf")
        filename = f"{report_type}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        final_doc = SimpleDocTemplate(
            response,
            pagesize=landscape(A4),
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=10 * mm,
            bottomMargin=12 * mm,
        )
        final_doc.build(_build_checks_story(sheets_value), canvasmaker=PageCountCanvas)
        SystemSetting.record_report_number(report_no)
        _audit(request.user, "Exported report PDF", {"type": report_type, "rows": len(table_rows)})
        return response

    story = []

    # If this is the checks-issued report, render a specialized header block
    if report_type == 'cheque_summary':
        entity_name = request.GET.get('entity_name') or campus_name
        fund_cluster_id = request.GET.get('fund_cluster') or None
        fund_cluster_obj = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first() if fund_cluster_id else None
        fund_cluster = fund_cluster_obj.code if fund_cluster_obj else ''
        bank_account = request.GET.get('bank_account') or ''
        if fund_cluster_obj:
            bank_account = fund_cluster_obj.account_number or ''
        elif bank_account:
            if ' / ' in bank_account:
                bank_account = bank_account.split(' / ', 1)[1].strip()
            elif '/' in bank_account:
                bank_account = bank_account.split('/', 1)[1].strip()
        report_no = request.GET.get('report_no') or ''
        sheets = request.GET.get('sheets') or ''

        left_block = Table([
            [Paragraph('Entity Name', meta_style), Paragraph(entity_name, meta_style)],
            [Paragraph('Fund Cluster', meta_style), Paragraph(fund_cluster, meta_style)],
            [Paragraph('Bank Name/Account No.', meta_style), Paragraph(bank_account, meta_style)],
        ], colWidths=[30 * mm, 60 * mm])
        left_block.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        center_block = Table([
            [Paragraph('REPORTS OF CHECK ISSUED', school_name_style)],
            [Paragraph(f'Period Covered: {_format_period_covered(date_from, date_to)}', school_sub_style)],
        ], colWidths=[available_width - (120 * mm)])
        center_block.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        right_block = Table([
            [Paragraph('Report No.', meta_style), Paragraph(report_no or '', meta_style)],
            [Paragraph('No. Of Sheets', meta_style), Paragraph(sheets or '', meta_style)],
        ], colWidths=[30 * mm, 30 * mm])
        right_block.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        header_row = Table([[left_block, center_block, right_block]], colWidths=[90 * mm, available_width - (90 * mm + 60 * mm), 60 * mm])
        header_row.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(header_row)
    else:
        header_text = Table(
            [
                [Paragraph(campus_name, school_name_style)],
                [Paragraph(campus_subtitle, school_sub_style)],
                [Paragraph(campus_address, school_sub_style)],
            ],
            colWidths=[96 * mm],
        )
        header_text.setStyle(
            TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )

        header_block = Table([[logo_flowable, header_text]], colWidths=[14 * mm, 96 * mm])
        header_block.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )

        side_width = max((available_width - (110 * mm)) / 2, 0)
        header_row = Table([["", header_block, ""]], colWidths=[side_width, 110 * mm, side_width])
        header_row.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        story.append(header_row)
    story.append(Spacer(1, 6))

    range_text = "All time"
    if date_from or date_to:
        range_text = f"{date_from or 'start'} to {date_to or 'present'}"

    story.append(Paragraph(f"Date report: {range_text}", meta_style))
    story.append(Paragraph(f"Report type: {report_label}", meta_style))
    story.append(Spacer(1, 6))

    # For supplier summary, include all rows when printing/exporting; otherwise limit preview for performance
    if report_type == 'supplier_summary':
        display_rows = table_rows
    else:
        display_rows = table_rows[:500]
    table_data = [headers] + display_rows
    if report_type == 'cheque_summary':
        # Only include data from preview — do not append empty rows.
        widths_mm = [12, 12, 16, 16, 18, 48, 18, 50, 22, 16, 12, 12, 22]
        col_widths = [w * mm for w in widths_mm[:len(headers)]]
        data_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        data_table.setStyle(
            TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.3, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ])
        )
    else:
        if len(display_rows) < 25:
            for _ in range(25 - len(display_rows)):
                table_data.append([""] * len(headers))

        col_count = max(len(headers), 1)
        col_width = available_width / col_count
        data_table = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
        data_table.setStyle(
            TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ])
        )
    story.append(data_table)
    story.append(Spacer(1, 6))

    generated_stamp = to_ph_time().strftime("%Y-%m-%d %H:%M:%S")
    story.append(Paragraph(f"GENERATED: {generated_stamp}", ParagraphStyle(name="gen", fontSize=8, alignment=2)))

    doc.build(story)
    _audit(request.user, "Exported report PDF", {"type": report_type, "rows": len(table_rows)})
    return response


@login_required
def report_detail(request, pk):
    is_admin = _is_admin(request.user)
    report = Report.objects.filter(pk=pk).first()
    if not report:
        return redirect(f"{reverse('reports')}?missing_report=1")
    snapshot = _report_snapshot_for_display(report.data_snapshot or {})
    table_headers, table_rows = _report_headers_and_rows(report.report_type, snapshot)

    # Always recompute sheet_count from the actual row count (25 rows/page)
    # so it's accurate for both new and old reports, regardless of stored value.
    computed_sheet_count = _estimate_report_sheets(len(table_rows))

    # include snapshot data and business entity_name for templates
    settings_obj = SystemSetting.get_settings()
    # Check print permission — guest can NEVER print
    can_print = is_admin or (hasattr(request.user, 'profile') and request.user.profile.role == 'cashier')
    _rp2 = _get_profile(request.user)
    if _rp2:
        can_print = is_admin or _rp2.role == 'cashier'
    can_generate = is_admin or (_rp2 and _rp2.role in ('cashier', 'guest'))
    context = _page_context(request, is_admin=is_admin, page_title=f"Report: {report.title}", extra={
        "report": report,
        "table_headers": table_headers,
        "table_rows": table_rows,
        "data": snapshot,
        "entity_name": getattr(settings_obj, 'entity_name', '') or '',
        "certification_name": _get_certification_name(report.generated_by, report=report),
        "period_covered": _format_period_covered(report.date_from, report.date_to),
        "can_print": can_print,
        "can_generate": can_generate,
        "sheet_count": computed_sheet_count,
    })
    return render(request, "admin_panel/report_detail.html", context)


@login_required
def report_print(request, pk):
    # Render a full-page printable view for the report (opens in new tab)
    profile = _get_profile(request.user)
    if profile and profile.role == 'guest':
        raise PermissionDenied("Guests are not allowed to print reports.")

    is_admin = _is_admin(request.user)
    report = Report.objects.filter(pk=pk).first()
    if not report:
        return redirect(f"{reverse('reports')}?missing_report=1")
    if report.report_type == "supplier_summary":
        table_headers = [
            "MR OR",
            "MR",
            "CODE OR",
            "CODE OR 2",
            "SERIES/MONTH",
            "DATE",
            "OR NUMBER",
            "SERIES/DAY",
            "PAYEE",
            "CODE LINE",
            "LINE",
            "CODE NOC",
            "NATURE OF COLLECTIONS",
            "AMOUNT",
            "REMARKS",
        ]
        table_rows = []
        for supplier in Supplier.objects.all().order_by('-date', '-pk'):
            table_rows.append([
                supplier.mr_or or "",
                supplier.mr or "",
                supplier.code_or or "",
                supplier.code_or2 or "",
                supplier.series_month or "",
                supplier.date.isoformat() if supplier.date else "",
                supplier.or_number or "",
                supplier.series_day or "",
                supplier.account_name or "",
                supplier.code_line or "",
                supplier.line or "",
                supplier.code_noc or "",
                supplier.nature_of_collections or "",
                str(supplier.amount or ""),
                supplier.remarks or "",
            ])
    else:
        snapshot = _report_snapshot_for_display(report.data_snapshot or {})
        table_headers, table_rows = _report_headers_and_rows(report.report_type, snapshot)

    # Use enriched snapshot data if present
    snapshot_data = _report_snapshot_for_display(report.data_snapshot or {})
    settings_obj = SystemSetting.get_settings()

    context = _page_context(request, is_admin=is_admin, page_title=f"Print: {report.title}", extra={
        "report": report,
        "table_headers": table_headers,
        "table_rows": table_rows,
        "data": snapshot_data,
        "entity_name": getattr(settings_obj, 'entity_name', '') or '',
        "certification_name": _get_certification_name(report.generated_by, report=report),
        "period_covered": _format_period_covered(report.date_from, report.date_to),
    })
    return render(request, "admin_panel/report_print.html", context)


@login_required
def report_print_modal(request, pk):
    """Return the report HTML fragment used inside the modal (AJAX)."""
    profile = _get_profile(request.user)
    if profile and profile.role == 'guest':
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    report = Report.objects.filter(pk=pk).first()
    if not report:
        return JsonResponse({"ok": False, "error": "This report is already deleted or not exist.", "missing_report": True}, status=404)
    snapshot = _report_snapshot_for_display(report.data_snapshot or {})
    table_headers, table_rows = _report_headers_and_rows(report.report_type, snapshot)
    settings_obj = SystemSetting.get_settings()
    context = {
        "report": report,
        "table_headers": table_headers,
        "table_rows": table_rows,
        "data": snapshot,
        "entity_name": getattr(settings_obj, 'entity_name', '') or '',
        "certification_name": _get_certification_name(report.generated_by, report=report),
        "period_covered": _format_period_covered(report.date_from, report.date_to),
    }
    return render(request, "admin_panel/report_print_modal_fragment.html", context)


# ─────────────────────────────────────────────
# IMPORT / EXPORT
# ─────────────────────────────────────────────

@login_required
def import_data(request):
    if not _is_admin(request.user):
        return redirect('admin_dashboard')

    def _import_upload_dir():
        upload_dir = Path(tempfile.gettempdir()) / "finalproject_import_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    def _save_uploaded_file(uploaded_file):
        token = secrets.token_hex(16)
        upload_dir = _import_upload_dir()
        safe_name = Path(uploaded_file.name).name
        stored_path = upload_dir / f"{token}_{safe_name}"
        with open(stored_path, "wb") as dest:
            for chunk in uploaded_file.chunks():
                dest.write(chunk)
        request.session["import_upload_token"] = token
        request.session["import_upload_name"] = safe_name
        request.session.modified = True
        return token, stored_path

    def _load_uploaded_file_path(token):
        if not token:
            return None
        upload_dir = _import_upload_dir()
        matches = list(upload_dir.glob(f"{token}_*"))
        return matches[0] if matches else None

    def _build_excel_preview(ws):
        merged_map = {}
        covered = set()
        for merged_range in ws.merged_cells.ranges:
            min_col, min_row, max_col, max_row = merged_range.bounds
            merged_map[(min_row, min_col)] = (max_row - min_row + 1, max_col - min_col + 1)
            for row in range(min_row, max_row + 1):
                for col in range(min_col, max_col + 1):
                    if (row, col) != (min_row, min_col):
                        covered.add((row, col))

        def _cell_style(cell):
            fill = getattr(cell.fill, 'fgColor', None)
            fill_rgb = ''
            if fill and getattr(fill, 'type', None) == 'rgb' and fill.rgb:
                fill_rgb = fill.rgb

            font = cell.font
            alignment = cell.alignment
            css = []
            if fill_rgb and fill_rgb not in ('00000000', '000000', 'FFFFFFFF'):
                css.append(f'background-color:#{fill_rgb[-6:]};')
            if font and font.bold:
                css.append('font-weight:700;')
            if font and font.italic:
                css.append('font-style:italic;')
            if font and font.sz:
                css.append(f'font-size:{float(font.sz):.0f}px;')
            if font and font.color and getattr(font.color, 'type', None) == 'rgb' and font.color.rgb:
                css.append(f'color:#{font.color.rgb[-6:]};')
            if alignment and alignment.horizontal:
                css.append(f'text-align:{alignment.horizontal};')
            if alignment and alignment.vertical:
                css.append(f'vertical-align:{alignment.vertical};')
            return ''.join(css)

        def _row_has_data(row_cells):
            for cell in row_cells:
                if cell.value not in (None, ''):
                    return True
            return False

        row_items = []
        for r_idx, row in enumerate(ws.iter_rows(), start=1):
            if not _row_has_data(row):
                continue
            cells = []
            for c_idx, cell in enumerate(row, start=1):
                if (r_idx, c_idx) in covered:
                    continue
                rowspan, colspan = merged_map.get((r_idx, c_idx), (1, 1))
                value = '' if cell.value is None else str(cell.value)
                cells.append({
                    'value': value,
                    'rowspan': rowspan,
                    'colspan': colspan,
                    'style': _cell_style(cell),
                    'is_blank': value == '',
                })
            row_items.append({'cells': cells})
        return row_items

    def _parse_upload(file_path):
        preview_rows = []
        preview_sheets = {}
        import_sheets = {}
        sheet_names = []

        if file_path.suffix.lower() == '.csv':
            decoded = file_path.read_text(encoding='utf-8-sig', errors='replace')
            reader = csv.DictReader(io.StringIO(decoded))
            rows = []
            headers = reader.fieldnames or []
            for row_number, row in enumerate(reader, start=2):
                if not any(str(value or '').strip() for value in row.values()):
                    continue
                row_data = dict(row)
                row_data['_sheet'] = 'CSV'
                row_data['_row_number'] = row_number
                row_data['_headers'] = headers
                row_data['_values'] = [row.get(header, '') for header in headers]
                row_data['_ordered_cells'] = [
                    {'header': header, 'value': row.get(header, '')}
                    for header in headers
                ]
                rows.append(row_data)
            preview_rows = rows
            return preview_rows, preview_sheets, import_sheets, sheet_names, rows

        if file_path.suffix.lower() in {'.xlsx', '.xls'}:
            import openpyxl
            wb = openpyxl.load_workbook(filename=str(file_path), data_only=True)
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                sample_rows = list(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 10), values_only=True))
                header_row_index = 1
                header_score = -1
                for idx, row in enumerate(sample_rows, start=1):
                    non_empty = sum(1 for value in row if value not in (None, ''))
                    if non_empty > header_score:
                        header_score = non_empty
                        header_row_index = idx
                if header_score <= 0:
                    headers = []
                else:
                    headers = [str(value or '').strip() for value in sample_rows[header_row_index - 1]]

                sheet_rows = []
                for row in ws.iter_rows(min_row=header_row_index + 1, values_only=True):
                    def _normalize_cell(v):
                        if v is None:
                            return ''
                        # Convert floats that are whole numbers to int strings (164.0 -> '164')
                        if isinstance(v, float) and v == int(v):
                            return str(int(v))
                        return str(v)
                    values = [_normalize_cell(v) for v in row]
                    if not any(value.strip() for value in values):
                        continue
                    max_len = max(len(headers), len(values))
                    row_data = {}
                    ordered_cells = []
                    for i in range(max_len):
                        header = headers[i] if i < len(headers) and headers[i] else f'Column {i + 1}'
                        value = values[i] if i < len(values) else ''
                        row_data[header] = value
                        ordered_cells.append({'header': header, 'value': value})
                    row_data['_sheet'] = sheet_name
                    row_data['_row_number'] = len(sheet_rows) + header_row_index + 1
                    row_data['_header_row'] = header_row_index
                    row_data['_headers'] = headers
                    row_data['_values'] = values
                    row_data['_ordered_cells'] = ordered_cells
                    sheet_rows.append(row_data)

                import_sheets[sheet_name] = sheet_rows
                preview_sheets[sheet_name] = {
                    'rows': _build_excel_preview(ws),
                    'row_count': ws.max_row,
                    'col_count': ws.max_column,
                }

            sheet_names = list(preview_sheets.keys())
            selected = sheet_names[0] if sheet_names else None
            preview_rows = import_sheets.get(selected, []) if selected else []
            return preview_rows, preview_sheets, import_sheets, sheet_names, []

        raise ValueError("Unsupported file type. Use .csv, .xls, or .xlsx")

    error = None
    success = None
    preview_rows = []
    import_type = 'suppliers'
    preview_sheet_name = None
    preview_sheet = None
    show_import_modal = False
    upload_token = None
    sheet_names = []
    preview_sheets = {}
    import_sheets = {}

    if request.method == "POST":
        import_type = request.POST.get('import_type', 'suppliers')
        uploaded = request.FILES.get('file')
        confirm = request.POST.get('confirm') == '1'
        selected_sheet = request.POST.get('sheet', '').strip()
        upload_token = request.POST.get('upload_token', '').strip()
        supported_uploads = {'.csv', '.xlsx', '.xls'}

        if not uploaded:
            if confirm and upload_token:
                stored_path = _load_uploaded_file_path(upload_token)
                if not stored_path:
                    error = "The uploaded file could not be found. Please preview the file again."
                else:
                    try:
                        preview_rows, preview_sheets, import_sheets, sheet_names, csv_rows = _parse_upload(stored_path)
                        if confirm:
                            if preview_sheets:
                                sheet_to_import = selected_sheet or sheet_names[0]
                                rows_for_import = import_sheets.get(sheet_to_import, [])
                            else:
                                rows_for_import = csv_rows or preview_rows
                            count, import_warnings = _do_import(import_type, rows_for_import, request.user)
                            success = f"Successfully imported {count} records."
                            if import_warnings:
                                success += f" ({len(import_warnings)} row(s) skipped — see warnings below.)"
                            show_import_modal = False
                            extra_warnings = import_warnings
                    except Exception as exc:
                        error = f"Import error: {exc}"
            else:
                error = "Please select a file to import."
        else:
            name = uploaded.name.lower()
            suffix = Path(name).suffix.lower()
            if suffix not in supported_uploads:
                error = "Unsupported file type. Please upload a CSV, XLSX, or XLS file."
                show_import_modal = False
            else:
                try:
                    token, stored_path = _save_uploaded_file(uploaded)
                    upload_token = token
                    request.session["import_upload_token"] = token
                    request.session["import_upload_name"] = uploaded.name
                    request.session.modified = True
                    preview_rows, preview_sheets, import_sheets, sheet_names, csv_rows = _parse_upload(stored_path)
                    has_preview_data = bool(preview_rows or preview_sheets or csv_rows)
                    if preview_sheets and has_preview_data:
                        preview_sheet_name = sheet_names[0]
                        preview_sheet = preview_sheets.get(preview_sheet_name)
                    elif preview_rows and has_preview_data:
                        preview_sheet = None
                    else:
                        preview_sheet = None
                    show_import_modal = has_preview_data
                    if not has_preview_data:
                        error = "The uploaded file does not contain previewable data."

                except Exception as e:
                    error = f"Import error: {e}"

    extra = {
        "error": error,
        "success": success,
        "import_warnings": locals().get('extra_warnings', []),
        "preview_rows": preview_rows,
        "import_type": import_type,
        "preview_sheet_name": preview_sheet_name,
        "preview_sheet": preview_sheet,
        "sheet_names": sheet_names,
        "show_import_modal": show_import_modal,
        "upload_token": upload_token,
    }
    if preview_sheets:
        extra['preview_sheets'] = preview_sheets

    context = _page_context(request, is_admin=True, page_title="Import Data", extra=extra)
    return render(request, "admin_panel/import_data.html", context)


def _do_import(import_type, rows, user):
    def _norm(value):
        return str(value or '').strip()

    def _get_value(row, *keys, default=''):
        normalized = {str(k).strip().lower().replace(' ', '').replace('_', ''): v for k, v in row.items() if not str(k).startswith('_')}
        for key in keys:
            lookup = str(key).strip().lower().replace(' ', '').replace('_', '')
            if lookup in normalized and _norm(normalized[lookup]):
                return _norm(normalized[lookup])
        return _norm(default)

    def _is_nonzero(val):
        """Return True if val is a non-empty, non-zero numeric value."""
        try:
            return bool(_norm(val)) and float(_norm(val).replace(',', '')) != 0
        except (ValueError, AttributeError):
            return bool(_norm(val))

    count = 0
    warnings = []  # list of warning messages for skipped rows

    if import_type == 'suppliers':
        # Pre-build a map of all fund cluster codes (lowercased) -> FundCluster object
        fund_cluster_map = {
            fc.code.strip().lower(): fc
            for fc in FundCluster.objects.filter(is_active=True)
        }

        seen_import_keys = set()
        for r in rows:
            raw_import = _supplier_import_raw_import(r)
            parsed = _supplier_import_row_values(r)
            non_empty_values = [_norm(value) for value in raw_import.get('values', []) if _norm(value)]
            if isinstance(r, dict) and not non_empty_values:
                non_empty_values = [_norm(value) for key, value in r.items() if not str(key).startswith('_') and _norm(value)]

            name = parsed.get('account_name', '')
            if not name:
                row_number = r.get('_row_number') if isinstance(r, dict) else None
                name = f"Imported Row {row_number or count + 1}"

            account_number = parsed.get('or_number', '')
            try:
                amount = Decimal(parsed.get('amount', '') or '0').quantize(Decimal('0.01'))
            except InvalidOperation:
                amount = Decimal('0.00')
            parsed_date = _parse_supplier_date_value(parsed.get('date')) or _parse_supplier_date_value(parsed.get('mr')) or _parse_supplier_date_value(parsed.get('mr_or'))
            import_key = (
                _norm(name),
                _norm(account_number),
                _norm(parsed.get('mr_or', '')),
                parsed_date.isoformat() if parsed_date else '',
            )
            if import_key in seen_import_keys:
                continue
            seen_import_keys.add(import_key)

            # --- Fund Cluster validation ---
            raw_fc_code = _norm(parsed.get('fund_cluster', ''))
            fund_cluster_obj = None
            if raw_fc_code:
                def _find_fund_cluster(code_str):
                    """Try multiple normalizations to match a fund cluster code."""
                    # 1. Exact lowercased match
                    fc = fund_cluster_map.get(code_str.strip().lower())
                    if fc:
                        return fc
                    # 2. Float string: '164.0' -> '164'
                    try:
                        int_str = str(int(float(code_str.strip())))
                        fc = fund_cluster_map.get(int_str.lower())
                        if fc:
                            return fc
                    except (ValueError, OverflowError):
                        pass
                    # 3. Prefix match: '164 - General Fund' -> extract '164'
                    import re as _re
                    prefix_match = _re.match(r'^(\S+)', code_str.strip())
                    if prefix_match:
                        prefix = prefix_match.group(1).lower()
                        fc = fund_cluster_map.get(prefix)
                        if fc:
                            return fc
                    # 4. Contains match: any DB code that appears inside the value
                    lower_val = code_str.strip().lower()
                    for db_code, fc_obj in fund_cluster_map.items():
                        if db_code in lower_val or lower_val in db_code:
                            return fc_obj
                    return None

                fund_cluster_obj = _find_fund_cluster(raw_fc_code)
                if fund_cluster_obj is None:
                    # Fund cluster code not found — skip row and warn
                    warnings.append(
                        f"Row skipped (Payee: '{name}'): Fund Cluster '{raw_fc_code}' is not registered in the system."
                    )
                    continue

            duplicate_exists = Supplier.objects.filter(
                account_name=name,
                account_number=account_number,
                mr_or=parsed.get('mr_or', ''),
                date=parsed_date,
            ).exists()
            if duplicate_exists:
                continue

            # --- Auto-detect is_vat from tax columns ---
            prof_tax_val = parsed.get('professional_tax', '')
            tax_5_val    = parsed.get('tax_5', '')
            tax_2_val    = parsed.get('tax_2', '')
            auto_is_vat  = _is_nonzero(tax_5_val) or _is_nonzero(tax_2_val)

            other_deductions_raw = str(parsed.get('other_deductions', '') or '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
            try:
                other_deductions_val = str(Decimal(other_deductions_raw or '0').quantize(Decimal('0.01'))) if other_deductions_raw else ''
            except Exception:
                other_deductions_val = ''

            # --- Auto-detect Account Title by UACS object code ---
            def _normalize_uacs(val):
                s = str(val or '').strip()
                if s.endswith('.0'):
                    s = s[:-2]
                return s

            uacs_code = _normalize_uacs(parsed.get('uacs', ''))
            account_title_val = ''
            if uacs_code:
                # Look up ManagementOption (Account Title) or AccountTitleGroup by UACS code
                opt = ManagementOption.objects.filter(
                    category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
                    uacs=uacs_code,
                    is_active=True
                ).first()
                if opt:
                    account_title_val = opt.value
                else:
                    group = AccountTitleGroup.objects.filter(uacs=uacs_code).first()
                    if group:
                        account_title_val = group.name

            # --- Auto-detect custom tax rates on import ---
            try:
                active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
                t1 = active_taxes[0] if len(active_taxes) > 0 else None
                t2 = active_taxes[1] if len(active_taxes) > 1 else None
                t1_rate = t1.uacs if t1 else '5%/3%'
                t2_rate = t2.uacs if t2 else '2%/1%'
            except Exception:
                t1_rate = '5%/3%'
                t2_rate = '2%/1%'

            t1_vat, t1_nv = _get_tax_rates(t1_rate)
            t2_vat, t2_nv = _get_tax_rates(t2_rate)

            tax_5_rate_vat_val = str((t1_vat * 100).quantize(Decimal('0.01')))
            tax_5_rate_non_vat_val = str((t1_nv * 100).quantize(Decimal('0.01')))
            tax_2_rate_vat_val = str((t2_vat * 100).quantize(Decimal('0.01')))
            tax_2_rate_non_vat_val = str((t2_nv * 100).quantize(Decimal('0.01')))

            if auto_is_vat:
                try:
                    gross_val_raw = parsed.get('gross_amount', '')
                    if _is_nonzero(gross_val_raw):
                        gross_dec = Decimal(str(gross_val_raw).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                    else:
                        tax_5_dec = Decimal(str(tax_5_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        tax_2_dec = Decimal(str(tax_2_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        prof_dec = Decimal(str(prof_tax_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        other_ded_dec = Decimal(str(other_deductions_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        gross_dec = amount + tax_5_dec + tax_2_dec + prof_dec + other_ded_dec

                    base_dec = (gross_dec / Decimal('1.12')).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                    if base_dec > 0:
                        if _is_nonzero(tax_5_val):
                            tax_5_dec = Decimal(str(tax_5_val).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                            tax_5_rate_vat_val = str(((tax_5_dec / base_dec) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                        else:
                            tax_5_rate_vat_val = '0.00'

                        if _is_nonzero(tax_2_val):
                            tax_2_dec = Decimal(str(tax_2_val).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                            tax_2_rate_vat_val = str(((tax_2_dec / base_dec) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                        else:
                            tax_2_rate_vat_val = '0.00'
                except Exception:
                    pass

            if not isinstance(raw_import, dict):
                raw_import = {}
            raw_import['supplier_extras'] = {
                'account_title': account_title_val,
                'dv_payroll': parsed.get('dv_payroll', ''),
                'ors_burs': parsed.get('ors_burs', ''),
                'responsibility_center': parsed.get('responsibility_center', ''),
                'uacs': uacs_code,
                'professional_tax': prof_tax_val,
                'tax_5': tax_5_val,
                'tax_2': tax_2_val,
                'other_deductions': other_deductions_val,
                'gross_amount': parsed.get('gross_amount', ''),
                'fund_cluster': fund_cluster_obj.code if fund_cluster_obj else raw_fc_code,
                'fund_cluster_id': str(fund_cluster_obj.pk) if fund_cluster_obj else '',
                'is_vat': 'true' if auto_is_vat else 'false',
                'tax_5_rate_vat': tax_5_rate_vat_val,
                'tax_5_rate_non_vat': tax_5_rate_non_vat_val,
                'tax_2_rate_vat': tax_2_rate_vat_val,
                'tax_2_rate_non_vat': tax_2_rate_non_vat_val,
            }

            Supplier.objects.create(
                account_name=name,
                account_number=account_number,
                mr_or=parsed.get('mr_or', ''),
                mr=parsed.get('mr', ''),
                code_or=parsed.get('code_or', ''),
                code_or2=parsed.get('code_or2', ''),
                series_month=parsed.get('series_month', ''),
                date=parsed_date,
                or_number=account_number,
                series_day=parsed.get('series_day', ''),
                code_line=parsed.get('code_line', ''),
                line=parsed.get('line', ''),
                code_noc=parsed.get('code_noc', ''),
                nature_of_collections=parsed.get('nature_of_collections', ''),
                amount=amount,
                remarks=parsed.get('remarks', ''),
                address=parsed.get('address', ''),
                tin=parsed.get('tin', ''),
                status='active',
                created_by=user,
                raw_import=raw_import,
            )
            count += 1
    elif import_type == 'radai':
        fund_cluster_map = {
            fc.code.strip().lower(): fc
            for fc in FundCluster.objects.filter(is_active=True)
        }
        existing_serials = set(
            Radai.objects.exclude(mr_or='').values_list('mr_or', flat=True)
        )
        for r in rows:
            try:
                amount = Decimal(_get_value(r, 'amount', default='0').replace(',', ''))
            except InvalidOperation:
                amount = Decimal('0')
            raw_date_str = _get_value(r, 'date')
            parsed_date = _parse_supplier_date_value(raw_date_str) if raw_date_str else None
            account_name = _get_value(r, 'account_name', 'payee')
            if not account_name:
                warnings.append(f"Row {r.get('_row_number', '?')}: Skipped — missing Payee")
                continue
            mr_or = _get_value(r, 'mr_or', 'serial no.', 'serial', 'check serial')
            if mr_or and mr_or in existing_serials:
                warnings.append(f"Row {r.get('_row_number', '?')}: Skipped — duplicate Serial No. '{mr_or}'")
                continue
            reference_code = _get_value(r, 'reference_code', 'ors/burs no.', 'ors/burs no. & responsibility center', 'reference code')
            responsibility_center = _get_value(r, 'responsibility_center', 'responsibility center code', 'responsibility center')
            nature = _get_value(r, 'nature_of_collections', 'nature of payment', 'nature')
            dv_payroll = _get_value(r, 'dv_payroll', 'dv/payroll no.')
            uacs = _get_value(r, 'uacs', 'uacs object code')
            remarks = _get_value(r, 'remarks')
            fc_code = _get_value(r, 'fund_cluster', 'fund cluster', 'cluster')

            if responsibility_center and reference_code:
                reference_code = f"{reference_code} | {responsibility_center}"
            elif responsibility_center:
                reference_code = responsibility_center
            fund_cluster = None
            if fc_code:
                fc_code_clean = fc_code.strip().lower()
                if fc_code_clean in fund_cluster_map:
                    fund_cluster = fund_cluster_map[fc_code_clean]
                else:
                    try:
                        int_code = str(int(float(fc_code_clean)))
                        fund_cluster = fund_cluster_map.get(int_code)
                    except (ValueError, OverflowError):
                        pass
                # Auto-create fund cluster if not found
                if not fund_cluster:
                    fund_cluster = FundCluster.objects.create(
                        code=fc_code.strip(),
                        name=fc_code.strip(),
                        is_active=True
                    )
                    fund_cluster_map[fc_code_clean] = fund_cluster
                    warnings.append(f"Row {r.get('_row_number', '?')}: Auto-created fund cluster '{fc_code}'")
            Radai.objects.create(
                account_name=account_name,
                amount=amount,
                date=parsed_date,
                mr_or=mr_or,
                reference_code=reference_code,
                nature_of_collections=nature,
                remarks=remarks,
                fund_cluster=fund_cluster,
                created_by=user,
                raw_import={
                    'radai_extras': {
                        'dv_payroll': dv_payroll,
                        'uacs': uacs,
                    }
                },
            )
            if mr_or:
                existing_serials.add(mr_or)
            count += 1
    elif import_type == 'fund_clusters':
        for r in rows:
            code = _get_value(r, 'code', 'cluster code', 'fund code')
            name = _get_value(r, 'name', 'cluster name', 'fund name')
            if not code or not name:
                continue
            try:
                bal = Decimal(_get_value(r, 'balance', 'amount', default='0') or '0')
            except InvalidOperation:
                bal = Decimal('0')
            FundCluster.objects.update_or_create(
                code=code,
                defaults={"name": name, "balance": bal,
                          "description": _get_value(r, 'description', 'desc'),
                          "is_active": str(_get_value(r, 'is_active', 'active', default='true')).lower() != 'false'}
            )
            count += 1
    _audit(user, f"Imported {import_type}", {"count": count})
    return count, warnings


@login_required
def backup_restore(request):
    if not _is_admin(request.user):
        return redirect("admin_dashboard")

    error = None
    success = None
    restore_preview = None
    restore_token = request.session.get("backup_restore_token")

    if request.method == "POST":
        action = request.POST.get("action", "").strip().lower()
        admin_password = request.POST.get("admin_password", "")

        if not admin_password or not request.user.check_password(admin_password):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"error": "Admin password is incorrect"}, status=400)
            error = "Admin password is incorrect"
        elif action == "download":
            try:
                archive_path, backup_name, _ = _pack_backup_archive(request.user)
                # Store backup on server and enforce retention
                _store_backup(archive_path, backup_name)
                _enforce_backup_retention()
                response = HttpResponse(archive_path.read_bytes(), content_type="application/octet-stream")
                response["Content-Disposition"] = f'attachment; filename="{backup_name}"'
                response["Access-Control-Expose-Headers"] = "Content-Disposition"
                _audit(request.user, "Backup downloaded", {"filename": backup_name})
                return response
            except Exception as exc:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({"error": f"Backup failed: {exc}"}, status=400)
                error = f"Backup failed: {exc}"
        elif action == "preview":
            uploaded = request.FILES.get("backup_file")
            if not uploaded:
                error = "Please select a backup file."
            else:
                token = secrets.token_hex(16)
                temp_dir = Path(tempfile.gettempdir()) / "finalproject_backup_uploads"
                temp_dir.mkdir(parents=True, exist_ok=True)
                stored_path = temp_dir / f"{token}.bak"
                try:
                    with open(stored_path, "wb") as dest:
                        for chunk in uploaded.chunks():
                            dest.write(chunk)
                    restore_preview = _inspect_backup_archive(stored_path)
                    restore_preview.update({
                        "filename": uploaded.name,
                        "size": uploaded.size,
                    })
                    request.session["backup_restore_token"] = token
                    request.session["backup_restore_path"] = str(stored_path)
                    restore_token = token
                except Exception as exc:
                    if stored_path.exists():
                        stored_path.unlink(missing_ok=True)
                    error = f"Invalid backup: {exc}"
        elif action == "restore":
            token = request.POST.get("restore_token") or request.session.get("backup_restore_token")
            stored_path_str = request.session.get("backup_restore_path")
            if not token or not stored_path_str:
                error = "No backup preview is available. Please upload the file again."
            else:
                stored_path = Path(stored_path_str)
                try:
                    manifest = _apply_restore_archive(stored_path, request.user)
                    request.session.pop("backup_restore_token", None)
                    request.session.pop("backup_restore_path", None)
                    restore_preview = None
                    success = f"System restored from backup created at {manifest.get('created_at', 'unknown time')}"
                except Exception as exc:
                    error = f"Restore failed: {exc}"
        elif action == "reset":
            from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, RoleConfig
            from admin_panel.models import AuditLog, ManagementOption, AccountTitleGroup
            from django.contrib.auth.models import User
            try:
                with transaction.atomic():
                    Transaction.objects.all().delete()
                    Cheque.objects.all().delete()
                    Report.objects.all().delete()
                    Supplier.objects.all().delete()
                    FundCluster.objects.all().delete()
                    AccountTitleGroup.objects.all().delete()
                    ManagementOption.objects.all().delete()
                    RoleConfig.objects.all().delete()
                    User.objects.exclude(id=request.user.id).delete()
                    Profile.objects.exclude(user_id=request.user.id).delete()
                    SystemSetting.objects.all().delete()
                    SystemSetting.objects.create(pk=1, system_name="registry")
                    AuditLog.objects.all().delete()
                    AuditLog.objects.create(admin=request.user, action="System reset executed", details={"reset_by": request.user.username})
                success = "System has been successfully reset. All transactional data has been cleared."
            except Exception as exc:
                error = f"System reset failed: {exc}"

    if not restore_preview and restore_token and request.session.get("backup_restore_path"):
        try:
            restore_preview = _inspect_backup_archive(Path(request.session["backup_restore_path"]))
            restore_preview.update({
                "filename": Path(request.session["backup_restore_path"]).name,
            })
        except Exception:
            restore_preview = None

    context = _page_context(request, is_admin=True, page_title="Backup & Restore", extra={
        "error": error,
        "success": success,
        "restore_preview": restore_preview,
        "restore_token": restore_token,
        "system_setting": SystemSetting.get_settings(),
    })
    return render(request, "admin_panel/backup_restore.html", context)


@login_required
def export_cheques(request):
    if not _is_admin(request.user):
        return redirect('cheque_list')

    fmt = request.GET.get('format', 'excel')
    status_filter = request.GET.get('status', '')
    qs = Cheque.objects.select_related('payee', 'fund_cluster', 'created_by').order_by('-date')
    if status_filter:
        qs = qs.filter(status=status_filter)

    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="cheques.csv"'
        writer = csv.writer(response)
        writer.writerow(['Cheque #', 'Payee', 'Fund Cluster', 'Amount', 'Date', 'Status', 'Purpose', 'Bank', 'Created By'])
        for c in qs:
            writer.writerow([
                c.cheque_number, c.payee_name,
                c.fund_cluster.code if c.fund_cluster else '',
                str(c.amount), str(c.date), c.status, c.purpose,
                c.bank_name, c.created_by.get_full_name() if c.created_by else '',
            ])
        _audit(request.user, "Exported cheques (CSV)", {"count": qs.count()})
        return response
    else:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Cheques"
        headers = ['Cheque #', 'Payee', 'Fund Cluster', 'Amount', 'Date', 'Status', 'Purpose', 'Bank', 'Account #', 'Created By']
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="194214")
            cell.alignment = Alignment(horizontal="center")
        for row, c in enumerate(qs, 2):
            ws.append([
                c.cheque_number, c.payee_name,
                c.fund_cluster.code if c.fund_cluster else '',
                float(c.amount), str(c.date), c.status, c.purpose,
                c.bank_name, c.account_number,
                c.created_by.get_full_name() if c.created_by else '',
            ])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="cheques.xlsx"'
        _audit(request.user, "Exported cheques (Excel)", {"count": qs.count()})
        return response


@login_required
def export_suppliers(request):
    if not _is_admin(request.user):
        return redirect('suppliers')
    fmt = request.GET.get('format', 'excel')
    qs = Supplier.objects.order_by('account_name')

    def _supplier_export_row(supplier):
        return [
            supplier.mr_or,
            supplier.mr,
            supplier.code_or,
            supplier.code_or2,
            supplier.series_month,
            supplier.date.isoformat() if supplier.date else '',
            supplier.or_number or supplier.account_number,
            supplier.series_day,
            supplier.account_name,
            supplier.code_line,
            supplier.line,
            supplier.code_noc,
            supplier.nature_of_collections,
            f'{supplier.amount:.2f}',
            supplier.remarks,
        ]

    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="suppliers.csv"'
        writer = csv.writer(response)
        writer.writerow(_supplier_import_headers())
        for s in qs:
            writer.writerow(_supplier_export_row(s))
        return response
    else:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Suppliers"
        ws.append(_supplier_import_headers())
        for s in qs:
            ws.append(_supplier_export_row(s))
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="suppliers.xlsx"'
        return response


@login_required
def download_import_template(request):
    if not _is_admin(request.user):
        return redirect('import_data')

    import_type = request.GET.get('import_type', 'suppliers').strip().lower()
    fmt = request.GET.get('format', 'xlsx').strip().lower()

    if import_type == 'fund_clusters':
        headers = ['code', 'name', 'description', 'balance', 'is_active']
        filename_base = 'fund_clusters_import_template'
    elif import_type == 'radai':
        headers = _radai_import_headers()
        filename_base = 'radai_import_template'
    elif import_type == 'account_titles':
        headers = ['Table Header (bold)', 'UACS Object Code']
        filename_base = 'account_titles_import_template'
    else:
        headers = _supplier_import_headers()
        filename_base = 'suppliers_import_template'

    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename_base}.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        return response

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Template'

    if import_type == 'radai':
        ws.append(['Date', 'Serial No.', 'DV/Payroll No.', 'ORS/BURS No.', 'Responsibility Center Code', 'Payee', 'UACS Object Code', 'Nature of Payment', 'Amount', 'Fund Cluster'])

        for cell in ws[1]:
            cell.font = Font(bold=True, color='FFFFFF', size=11)
            cell.fill = PatternFill('solid', fgColor='194214')
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )

        col_widths = [12, 14, 16, 16, 22, 30, 18, 30, 14, 14]
        for i, width in enumerate(col_widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width

        ws.row_dimensions[1].height = 28
    else:
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='194214')
            cell.alignment = Alignment(horizontal='center')
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(16, min(28, len(str(col[0].value or '')) + 4))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename_base}.xlsx"'
    return response


# ─────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────

@login_required
def audit_log(request):
    if not _is_admin(request.user):
        return redirect('admin_dashboard')
    logs = AuditLog.objects.select_related('admin').order_by('-timestamp')
    search = request.GET.get('q', '').strip()
    if search:
        logs = logs.filter(Q(action__icontains=search) | Q(admin__username__icontains=search))
    logs = logs[:500]
    context = _page_context(request, is_admin=True, page_title="Audit Log", extra={"logs": logs, "search": search})
    return render(request, "admin_panel/audit_log.html", context)


# ─────────────────────────────────────────────
# USER MANAGEMENT
# ─────────────────────────────────────────────

_ADJECTIVES = [
    "chunky", "fluffy", "sneaky", "goofy", "zippy", "wiggly", "bouncy", "dizzy",
    "jolly", "loopy", "noodly", "perky", "quirky", "rubbery", "silly", "tippy",
    "wobbly", "zesty", "cuddly", "drowsy", "fizzy", "giddy", "huggy", "jumpy",
    "kicky", "lanky", "mimzy", "pixie", "snuggly", "toasty", "whimsy", "yummy",
]

_NOUNS = [
    "panda", "waffle", "noodle", "penguin", "muffin", "koala", "pickle", "biscuit",
    "taco", "pudding", "sprout", "bean", "donut", "gnome", "hammock", "jellybean",
    "kiwi", "llama", "meerkat", "owl", "pancake", "quokka", "raccoon", "sloth",
    "tofu", "unicorn", "walrus", "yak", "zebra", "bumblebee", "chipmunk", "dumpling",
    "earlobe", "froglet", "guppy", "hedgehog", "igloo", "jigsaw", "ketchup", "lemur",
]

def _generate_temp_username():
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    suffix = random.randint(10, 99)
    return f"{adj}.{noun}{suffix}"


def _generate_temp_password(length=10):
    """Generate a temp password that always meets validation requirements."""
    lower = "abcdefghjkmnpqrstuvwxyz"
    upper = "ABCDEFGHJKMNPQRSTUVWXYZ"
    digits = "23456789"
    special = "!@#$%&*"
    pw = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    all_chars = lower + upper + digits + special
    pw += [secrets.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pw)
    return ''.join(pw)


@login_required
def user_management(request):
    if not _is_admin(request.user):
        return redirect("cashier_dashboard")

    # Generate next employee ID
    from datetime import datetime
    import re
    from cashier.models import Profile

    # Regenerate/populate missing employee IDs for existing profiles
    profiles_without_id = Profile.objects.filter(Q(employee_id__isnull=True) | Q(employee_id=""))
    if profiles_without_id.exists():
        current_year = datetime.now().year
        max_val = 0
        for p in Profile.objects.exclude(employee_id__isnull=True).exclude(employee_id=""):
            eid = p.employee_id
            nums = re.findall(r'\d+', eid)
            if nums:
                try:
                    val = int(nums[-1])
                    if val > max_val:
                        max_val = val
                except ValueError:
                    pass
        next_val = max_val + 1
        for p in profiles_without_id:
            p.employee_id = f"EMP-{current_year}-{next_val:05d}"
            p.save(update_fields=["employee_id"])
            next_val += 1

    current_year = datetime.now().year
    max_val = 0
    for p in Profile.objects.exclude(employee_id__isnull=True).exclude(employee_id=""):
        eid = p.employee_id
        nums = re.findall(r'\d+', eid)
        if nums:
            try:
                val = int(nums[-1])
                if val > max_val:
                    max_val = val
            except ValueError:
                pass
    next_val = max_val + 1
    next_employee_id = f"EMP-{current_year}-{next_val:05d}"

    error = None

    if request.method == "POST":
        action = request.POST.get("action", "create").strip().lower()
        admin_password = request.POST.get("admin_password", "")

        if not admin_password or not request.user.check_password(admin_password):
            error = "Admin password is incorrect"
        elif action == "delete":
            user_id = request.POST.get("user_id", "").strip()
            if not user_id:
                error = "User ID is required"
            else:
                target_user = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
                if target_user.pk == request.user.pk:
                    error = "You cannot delete your own account"
                else:
                    _audit(request.user, "Admin deleted user", {"deleted_user_id": target_user.id, "username": target_user.username})
                    target_user.delete()
                    return redirect("user_management")
        elif action == "resend":
            user_id = request.POST.get("user_id", "").strip()
            if not user_id:
                error = "User ID is required"
            else:
                target_user = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
                target_email = target_user.email
                if target_user.profile.status == 'active':
                    error = "Cannot resend credentials for an active user."
                elif not target_email:
                    error = "This user has no email address on file."
                else:
                    try:
                        import secrets as _resend_secrets
                        new_password = ''.join(_resend_secrets.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))
                        target_user.set_password(new_password)
                        target_user.save()
                        target_user.profile.must_change_password = True
                        target_user.profile.save(update_fields=["must_change_password"])
                        from .mfa_utils import send_otp_email
                        from .models import SystemSetting as _ResendSS
                        _rs = _ResendSS.get_settings()
                        _subject = f"{_rs.system_name} – Your New Login Credentials"
                        _html = (
                            f"<p>Hello {target_user.first_name} {target_user.last_name},</p>"
                            f"<p>Your login credentials for <strong>{_rs.system_name}</strong> have been reset.</p>"
                            f"<p><strong>Username:</strong> {target_user.username}<br>"
                            f"<strong>New Temporary Password:</strong> {new_password}</p>"
                            f"<p>Please log in and change your password as soon as possible.</p>"
                        )
                        send_otp_email(
                            target_email, new_password,
                            system_name=_rs.system_name, system_logo=_rs.system_logo,
                            extra_subject=_subject, extra_html=_html,
                        )
                        _audit(request.user, "Admin resent credentials", {"user_id": target_user.id, "username": target_user.username})
                        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                            return JsonResponse({"ok": True, "message": f"New credentials emailed to {target_email}."})
                        messages.success(request, f"New credentials emailed to {target_email}.")
                        return redirect("user_management")
                    except Exception as _resend_err:
                        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                            return JsonResponse({"ok": False, "error": str(_resend_err)})
                        error = f"Failed to send credentials: {_resend_err}"
        elif action == "edit":
            user_id = request.POST.get("user_id", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            middle_initial = request.POST.get("middle_initial", "").strip()
            email = request.POST.get("email", "").strip()
            username = request.POST.get("username", "").strip()
            password = request.POST.get("password", "")
            profile_picture = request.FILES.get("profile_picture")
            employee_id = request.POST.get("employee_id", "").strip()
            department = request.POST.get("department", "").strip()
            position = request.POST.get("position", "").strip()
            role = request.POST.get("role", "cashier").strip()
            rfid_uid = request.POST.get("rfid_uid", "").strip() or None
            is_disbursing_officer = "is_disbursing_officer" in request.POST

            if not user_id:
                error = "User ID is required"
            elif not all([last_name, first_name, email, username]):
                error = "All required fields must be filled"
            else:
                user = get_object_or_404(User, pk=user_id)
                try:
                    profile = user.profile
                except Profile.DoesNotExist:
                    profile = Profile(user=user)

                if Profile.objects.exclude(user=user).filter(email_hash=hashlib.sha256(email.lower().encode()).hexdigest()).exists():
                    error = "Email address is already in use"
                elif User.objects.exclude(pk=user.pk).filter(username=username).exists():
                    error = "Username already exists"
                elif employee_id and Profile.objects.exclude(user=user).filter(employee_id=employee_id).exists():
                    error = "Employee ID is already in use"
                elif rfid_uid and Profile.objects.exclude(user=user).filter(rfid_uid=rfid_uid).exists():
                    error = "RFID UID is already registered"
                elif is_disbursing_officer and Profile.objects.exclude(user=user).filter(is_disbursing_officer=True).exists():
                    error = "A Disbursing Officer is already set. You must unset the current one before assigning a new one."
                elif role == "admin" and Profile.objects.exclude(user=user).filter(role="admin").exists():
                    error = "An Admin account is already set. You must unset or downgrade the current Admin before assigning a new one."
                elif password:
                    pw_err = _validate_password(password)
                    if pw_err:
                        error = pw_err
                else:
                    user.first_name = first_name
                    user.last_name = last_name
                    user.email = email
                    user.username = username
                    if password:
                        user.set_password(password)
                    user.save()

                    profile.middle_initial = middle_initial
                    profile.role = role or profile.role or "cashier"
                    profile.employee_id = employee_id or None
                    profile.department = department
                    profile.position = position
                    if profile_picture:
                        profile.profile_picture = profile_picture
                    profile.rfid_uid = rfid_uid
                    profile.is_disbursing_officer = is_disbursing_officer
                    profile.save()

                    if user.pk == request.user.pk and password:
                        old_key = request.session.session_key
                        update_session_auth_hash(request, user)
                        new_key = request.session.session_key
                        profile.active_session_key = new_key
                        profile.save(update_fields=['active_session_key'])
                        if old_key and new_key:
                            from cashier.models import UserDevice
                            UserDevice.objects.filter(session_key=old_key).update(session_key=new_key)

                    _audit(request.user, "Admin edited user", {"edited_user_id": user.id, "username": user.username})
                    return redirect("user_management")
        else:
            last_name = request.POST.get("last_name", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            middle_initial = request.POST.get("middle_initial", "").strip()
            email = request.POST.get("email", "").strip()
            profile_picture = request.FILES.get("profile_picture")
            employee_id = request.POST.get("employee_id", "").strip()
            department = request.POST.get("department", "").strip()
            position = request.POST.get("position", "").strip()
            role = request.POST.get("role", "cashier").strip()
            rfid_uid = request.POST.get("rfid_uid", "").strip() or None
            is_disbursing_officer = "is_disbursing_officer" in request.POST

            if not all([last_name, first_name, email]):
                error = "Last name, first name, and email are required"
            elif Profile.objects.filter(email_hash=hashlib.sha256(email.lower().encode()).hexdigest()).exists():
                error = "Email address is already in use"
            elif employee_id and Profile.objects.filter(employee_id=employee_id).exists():
                error = "Employee ID is already in use"
            elif rfid_uid and Profile.objects.filter(rfid_uid=rfid_uid).exists():
                error = "RFID UID is already registered"
            elif is_disbursing_officer and Profile.objects.filter(is_disbursing_officer=True).exists():
                error = "A Disbursing Officer is already set. You must unset the current one before assigning a new one."
            elif role == "admin" and Profile.objects.filter(role="admin").exists():
                error = "An Admin account is already set. You must unset or downgrade the current Admin before assigning a new one."
            else:
                temp_password = _generate_temp_password()
                username = _generate_temp_username()
                while User.objects.filter(username=username).exists():
                    username = _generate_temp_username()
                user = User.objects.create_user(username=username, password=temp_password, first_name=first_name, last_name=last_name, email=email)
                profile_role = role or 'cashier'
                profile, _ = Profile.objects.get_or_create(user=user, defaults={"role": profile_role})
                profile.role = profile_role
                profile.middle_initial = middle_initial
                profile.employee_id = employee_id or None
                profile.department = department
                profile.position = position
                profile.status = "pending"
                if profile_picture:
                    profile.profile_picture = profile_picture
                if rfid_uid:
                    profile.rfid_uid = rfid_uid
                profile.is_disbursing_officer = is_disbursing_officer
                profile.must_change_password = True
                profile.save()
                _audit(request.user, "Admin created new user", {"created_user_id": user.id, "username": user.username, "role": profile.role})
                # Email temp credentials
                import logging as _email_logging
                _email_logger = _email_logging.getLogger("admin_panel.email")
                try:
                    from .mfa_utils import send_otp_email
                    from .models import SystemSetting
                    sys_set = SystemSetting.get_settings()
                    subject = f"{sys_set.system_name} – Your Temporary Account Credentials"
                    html_body = f"""
                    <p>Hello {first_name} {last_name},</p>
                    <p>An account has been created for you in <strong>{sys_set.system_name}</strong>.</p>
                    <p><strong>Username:</strong> {username}<br>
                    <strong>Temporary Password:</strong> {temp_password}</p>
                    <p>Please log in and change your password as soon as possible.</p>
                    """
                    send_otp_email(email, temp_password, system_name=sys_set.system_name, system_logo=sys_set.system_logo, extra_subject=subject, extra_html=html_body)
                    _email_logger.info("Credentials email sent to %s for user %s", email, username)
                except Exception as _email_err:
                    _email_logger.error("Failed to send credentials email to %s for user %s: %s", email, username, _email_err, exc_info=True)
                    from django.contrib import messages
                    messages.warning(request, f"User created successfully, but the credentials email could not be sent to {email}. Error: {_email_err}")
                return redirect("user_management")

    cashiers = User.objects.select_related("profile").filter(profile__isnull=False).order_by("last_name", "first_name")
    context = _page_context(request, is_admin=True, page_title="User Management", extra={
        "error": error,
        "cashiers": cashiers,
        "next_employee_id": next_employee_id,
    })
    return render(request, "admin_panel/user_management.html", context)


create_cashier = user_management


# ─────────────────────────────────────────────
# RFID / AUTH
# ─────────────────────────────────────────────

@login_required
def rfid_lookup(request):
    uid = request.POST.get("uid") if request.method == "POST" else request.GET.get("uid")
    if not uid:
        return JsonResponse({"ok": False, "error": "uid required"}, status=400)
    try:
        profile = Profile.objects.select_related("user").get(rfid_uid=uid)
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    user = profile.user
    data = {
        "ok": True,
        "user": {"id": user.id, "username": user.username, "first_name": user.first_name, "last_name": user.last_name, "email": user.email},
        "profile": {"role": profile.role, "employee_id": profile.employee_id, "department": profile.department, "position": profile.position, "status": profile.status},
    }
    try:
        AuditLog.objects.create(admin=request.user, action="RFID lookup", details={"uid": uid, "found_user": user.id})
    except Exception:
        pass
    return JsonResponse(data)


@login_required
def rfid_auth(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    uid = request.POST.get("uid")
    password = request.POST.get("password")
    if not uid or not password:
        return JsonResponse({"ok": False, "error": "uid_and_password_required"}, status=400)
    try:
        profile = Profile.objects.select_related("user").get(rfid_uid=uid)
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    user = profile.user
    user_auth = authenticate(request, username=user.username, password=password)
    if user_auth is None:
        return JsonResponse({"ok": False, "error": "invalid_credentials"}, status=403)
    
    # Check if this physical device is blocked
    ip = _get_client_ip(request)
    dev_info = _get_client_device_and_os(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    if _is_device_blocked(ip, dev_info['device_name'], dev_info['windows_username'], user_agent):
        return JsonResponse({"ok": False, "error": "device_blocked", "message": "This device has been blocked by the system administrator."}, status=403)

    login(request, user_auth)
    if profile.status != 'active':
        profile.status = 'active'
        profile.save(update_fields=['status'])
    _audit(request.user, "RFID auth success", {"uid": uid, "user": user_auth.id})
    return JsonResponse({"ok": True, "role": profile.role})


@login_required
def role_permissions(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    from cashier.models import DEFAULT_ROLE_PERMISSIONS, ALL_PERMISSIONS
    return JsonResponse({"ok": True, "roles": DEFAULT_ROLE_PERMISSIONS, "all_permissions": ALL_PERMISSIONS})


@login_required
def save_role_permissions(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    return JsonResponse({"ok": False, "error": "Role permissions are fixed and cannot be modified."}, status=400)


@login_required
def start_fingerprint_enroll(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    return JsonResponse({"ok": False, "error": "not_implemented", "message": "Fingerprint enrollment requires hardware adapter."}, status=501)


@login_required
def start_face_enroll(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    return JsonResponse({"ok": False, "error": "not_implemented", "message": "Face enrollment requires camera/hardware adapter."}, status=501)


@login_required
def face_enroll(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != 'POST':
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    user_id = request.POST.get('user_id')
    img = request.FILES.get('image')
    if not user_id or not img:
        return JsonResponse({"ok": False, "error": "user_id_and_image_required"}, status=400)
    try:
        profile = Profile.objects.get(user__id=user_id)
        filename = f"face_{user_id}_{int(__import__('time').time())}.jpg"
        profile.face_embedding.save(filename, img, save=True)
        _audit(request.user, 'face_enroll', {'user_id': user_id})
        return JsonResponse({"ok": True})
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "profile_not_found"}, status=404)
    except Exception as e:
        return JsonResponse({"ok": False, "error": 'save_failed', 'msg': str(e)}, status=500)


@login_required
def voice_enroll(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != 'POST':
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    user_id = request.POST.get('user_id')
    aud = request.FILES.get('audio')
    if not user_id or not aud:
        return JsonResponse({"ok": False, "error": "user_id_and_audio_required"}, status=400)
    try:
        profile = Profile.objects.get(user__id=user_id)
        filename = f"voice_{user_id}_{int(__import__('time').time())}.wav"
        profile.voice_sample.save(filename, aud, save=True)
        _audit(request.user, 'voice_enroll', {'user_id': user_id})
        return JsonResponse({"ok": True})
    except Profile.DoesNotExist:
        return JsonResponse({"ok": False, "error": "profile_not_found"}, status=404)
    except Exception as e:
        return JsonResponse({"ok": False, "error": 'save_failed', 'msg': str(e)}, status=500)


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

@login_required
def account_settings(request):
    return redirect("profile_settings")


@login_required
def profile_settings(request):
    profile = _get_profile(request.user)
    is_admin = _is_admin(request.user)
    error = None
    success = None
    password_error = False
    show_current_password_modal = False
    show_new_password_modal = False

    if request.method == "POST":
        form_type = request.POST.get("form_type", "profile")
        if form_type == "verify_password":
            current_password = request.POST.get("current_password", "")
            if not current_password:
                error = "Current password is required"
                password_error = True
                show_current_password_modal = True
            elif not request.user.check_password(current_password):
                error = "Current password is incorrect"
                password_error = True
                show_current_password_modal = True
            else:
                request.session["password_verified"] = True
                show_new_password_modal = True
        elif form_type == "password":
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")
            if not request.session.get("password_verified"):
                error = "Please verify your current password first"
                password_error = True
                show_current_password_modal = True
            elif not new_password or not confirm_password:
                error = "New password and confirm password are required"
                password_error = True
                show_new_password_modal = True
            elif new_password != confirm_password:
                error = "Passwords do not match"
                password_error = True
                show_new_password_modal = True
            else:
                pw_err = _validate_password(new_password)
                if pw_err:
                    error = pw_err
                    password_error = True
                    show_new_password_modal = True
                else:
                    request.user.set_password(new_password)
                    request.user.save()
                    old_key = request.session.session_key
                    update_session_auth_hash(request, request.user)
                    new_key = request.session.session_key
                    profile.active_session_key = new_key
                    profile.save(update_fields=['active_session_key'])
                    if old_key and new_key:
                        from cashier.models import UserDevice
                        UserDevice.objects.filter(session_key=old_key).update(session_key=new_key)
                    request.session.pop("password_verified", None)
                    success = "Password changed successfully"
        else:
            last_name = request.POST.get("last_name", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            middle_initial = request.POST.get("middle_initial", "").strip()
            email = request.POST.get("email", "").strip()
            username = request.POST.get("username", "").strip()
            profile_picture = request.FILES.get("profile_picture")
            if not all([last_name, first_name, email, username]):
                error = "Last name, given name, email, and username are required"
            elif Profile.objects.exclude(user=request.user).filter(email_hash=hashlib.sha256(email.lower().encode()).hexdigest()).exists():
                error = "Email address is already in use"
            elif User.objects.exclude(pk=request.user.pk).filter(username=username).exists():
                error = "Username already exists"
            else:
                request.user.last_name = last_name
                request.user.first_name = first_name
                request.user.email = email
                request.user.username = username
                request.user.save()
                profile.middle_initial = middle_initial
                if profile_picture:
                    profile.profile_picture = profile_picture
                profile.save()
                success = "Account settings updated successfully!"

    context = _page_context(request, is_admin=is_admin, page_title="Profile Settings", extra={
        "error": error, "success": success, "profile": profile,
        "password_error": password_error,
        "show_current_password_modal": show_current_password_modal,
        "show_new_password_modal": show_new_password_modal,
    })
    return render(request, "profile_settings.html", context)


@login_required
def business_info(request):
    if not _is_admin(request.user):
        return redirect("profile_settings")
    system_setting = SystemSetting.get_settings()
    error = None
    success = None
    if request.method == "POST":
        system_name = request.POST.get("system_name", "").strip()
        entity_name = request.POST.get("entity_name", "").strip()
        system_logo = request.FILES.get("system_logo")
        system_logo_hover = request.FILES.get("system_logo_hover")
        lockscreen_wallpaper = request.FILES.get("lockscreen_wallpaper")
        login_background = request.FILES.get("login_background")
        if not system_name:
            error = "System name is required"
        else:
            system_setting.system_name = system_name
            system_setting.entity_name = entity_name
            system_setting.address = request.POST.get("address", "").strip()
            if system_logo:
                system_setting.system_logo = system_logo
            if system_logo_hover:
                system_setting.system_logo_hover = system_logo_hover
            if lockscreen_wallpaper:
                system_setting.lockscreen_wallpaper = lockscreen_wallpaper
            if login_background:
                system_setting.login_background = login_background
            # Retention settings
            system_setting.retention_enabled = request.POST.get("retention_enabled") == "on"
            system_setting.retention_auto_delete = request.POST.get("retention_auto_delete") == "on"
            try:
                system_setting.retention_max_age_days = int(request.POST.get("retention_max_age_days", 90))
            except (ValueError, TypeError):
                system_setting.retention_max_age_days = 90
            try:
                system_setting.retention_max_count = int(request.POST.get("retention_max_count", 10))
            except (ValueError, TypeError):
                system_setting.retention_max_count = 10
            system_setting.save()
            success = "System Information updated successfully"
    context = _page_context(request, is_admin=True, page_title=" System Information", extra={
        "error": error, "success": success, "system_setting": system_setting,
        "next_report_number": system_setting.next_report_number(),
    })
    return render(request, "system_settings.html", context)


@login_required
def test_email_settings(request):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)

    import json
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    to_email = (data.get("to_email") or "").strip()
    if not to_email:
        return JsonResponse({"ok": False, "error": "No recipient email provided."})

    host = (data.get("email_host") or "").strip()
    port = int(data.get("email_port") or 587)
    use_tls = data.get("email_use_tls", True)
    username = (data.get("email_host_user") or "").strip()
    password = (data.get("email_host_password") or "").strip()
    from_email = (data.get("default_from_email") or "").strip() or username

    if not host or not username or not password:
        return JsonResponse({"ok": False, "error": "SMTP host, username, and password are required."})

    from django.core.mail import EmailMessage
    from django.core.mail.backends.smtp import EmailBackend as SmtpBackend

    try:
        backend = SmtpBackend(
            host=host, port=port, use_tls=use_tls,
            username=username, password=password,
        )
        msg = EmailMessage(
            subject="Test Email — System SMTP Configuration",
            body=(
                "This is a test email from your system.\n\n"
                "If you received this, your SMTP settings are working correctly.\n\n"
                f"SMTP Host: {host}\n"
                f"Port: {port}\n"
                f"TLS: {use_tls}\n"
                f"From: {from_email}\n"
            ),
            from_email=from_email,
            to=[to_email],
        )
        msg.content_subtype = "plain"
        msg.connection = backend
        msg.send()
        backend.close()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})

@login_required
def screen_timeout(request):
    sys_set = SystemSetting.get_settings()
    profile = _get_profile(request.user)
    error = None
    success = None
    if request.method == "POST":
        # Per-user lockscreen wallpaper
        wallpaper = request.FILES.get("lockscreen_wallpaper")
        if wallpaper:
            profile.lockscreen_wallpaper = wallpaper
            profile.save(update_fields=["lockscreen_wallpaper"])
            success = "Lockscreen wallpaper updated"

        # Per-user individual timeout settings
        user_lock_raw = request.POST.get("user_auto_lock_seconds", "").strip()
        user_logout_raw = request.POST.get("user_auto_logout_seconds", "").strip()

        if user_lock_raw == "default" or not user_lock_raw:
            profile.auto_lock_seconds = None
        else:
            try:
                profile.auto_lock_seconds = max(10, min(3600, int(user_lock_raw)))
            except (TypeError, ValueError):
                pass

        if user_logout_raw == "default" or not user_logout_raw:
            profile.auto_logout_seconds = None
        else:
            try:
                profile.auto_logout_seconds = max(60, min(86400, int(user_logout_raw)))
            except (TypeError, ValueError):
                pass

        profile.save()
        if not success:
            success = "Your screen timeout preferences have been updated."

        # System-wide settings (admin only)
        if _is_admin(request.user):
            login_wallpaper = request.FILES.get("login_wallpaper")
            if login_wallpaper:
                sys_set.lockscreen_wallpaper = login_wallpaper
                sys_set.save(update_fields=["lockscreen_wallpaper"])
                success = "Login wallpaper updated"
            auto_lock_raw = request.POST.get("auto_lock_seconds", "").strip()
            auto_logout_raw = request.POST.get("auto_logout_seconds", "").strip()
            if auto_lock_raw:
                try:
                    sys_set.auto_lock_seconds = max(10, min(3600, int(auto_lock_raw)))
                except (TypeError, ValueError):
                    pass
            if auto_logout_raw:
                try:
                    sys_set.auto_logout_seconds = max(60, min(86400, int(auto_logout_raw)))
                except (TypeError, ValueError):
                    pass
            sys_set.save()
            if not login_wallpaper and not wallpaper:
                success = "Screen timeout preferences and system defaults updated."

    context = _page_context(request, is_admin=_is_admin(request.user), page_title="Screen Timeout", extra={
        "error": error, 
        "success": success, 
        "system_setting": sys_set,
        "profile": profile,
    })
    return render(request, "admin_panel/screen_timeout.html", context)


@login_required
def account_titles_management(request):
    """Main page: list of named Account Title Groups."""
    from cashier.models import DEFAULT_ROLE_PERMISSIONS
    profile = getattr(request.user, 'profile', None)
    role = 'admin' if request.user.is_superuser else (getattr(profile, 'role', 'guest') if profile else 'guest')
    role_config = DEFAULT_ROLE_PERMISSIONS.get(role, {"navigation": []})
    if 'account_titles_management' not in role_config.get('navigation', []):
        return redirect("cashier_dashboard")

    error = None
    success = None
    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        if action == 'add_group':
            name = request.POST.get('name', '').strip()
            uacs = request.POST.get('group_uacs', '').strip()
            if not name:
                error = 'Group name is required.'
            else:
                obj, created = AccountTitleGroup.objects.get_or_create(
                    name=name, defaults={'uacs': uacs}
                )
                if not created and obj.uacs != uacs:
                    obj.uacs = uacs
                    obj.save(update_fields=['uacs'])
                success = 'Group created.' if created else 'A group with that name already exists.'
                if uacs:
                    _sync_suppliers_by_uacs(uacs, name)
        elif action == 'edit_group':
            group_id = request.POST.get('group_id', '').strip()
            name     = request.POST.get('name', '').strip()
            uacs     = request.POST.get('group_uacs', '').strip()
            if not name:
                error = 'Group name is required.'
            else:
                AccountTitleGroup.objects.filter(pk=group_id).update(name=name, uacs=uacs)
                success = 'Group updated.'
                if uacs:
                    _sync_suppliers_by_uacs(uacs, name)
        elif action == 'delete_group':
            group_id = request.POST.get('group_id', '').strip()
            AccountTitleGroup.objects.filter(pk=group_id).delete()
            success = 'Group deleted.'
        elif action == 'edit_title':
            title_id = request.POST.get('title_id', '').strip()
            title_value = request.POST.get('title_value', '').strip()
            title_uacs = request.POST.get('title_uacs', '').strip()
            if not title_value:
                error = 'Account title is required.'
            else:
                ManagementOption.objects.filter(pk=title_id).update(value=title_value, uacs=title_uacs)
                success = 'Account title updated.'
                if title_uacs:
                    _sync_suppliers_by_uacs(title_uacs, title_value)
        elif action == 'delete_title':
            title_id = request.POST.get('title_id', '').strip()
            ManagementOption.objects.filter(pk=title_id).delete()
            success = 'Account title deleted.'
        elif action == 'import_confirm':
            import json as _json
            raw = request.POST.get('import_data', '')
            try:
                groups_data = _json.loads(raw)
            except Exception:
                groups_data = []
            imported_groups = 0
            imported_titles = 0
            for gd in groups_data:
                gname = (gd.get('name') or '').strip()
                guacs = (gd.get('uacs') or '').strip()
                if not gname:
                    continue
                grp, _ = AccountTitleGroup.objects.get_or_create(
                    name=gname, defaults={'uacs': guacs}
                )
                imported_groups += 1
                for td in gd.get('titles', []):
                    tval  = (td.get('value') or '').strip()
                    tuacs = (td.get('uacs') or '').strip()
                    if not tval:
                        continue
                    obj, created = ManagementOption.objects.get_or_create(
                        category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
                        value=tval,
                        defaults={'is_active': True, 'uacs': tuacs, 'group': grp},
                    )
                    if not created:
                        obj.group = grp
                        if tuacs:
                            obj.uacs = tuacs
                        obj.save(update_fields=['group', 'uacs'])
                    imported_titles += 1
                    if tuacs:
                        _sync_suppliers_by_uacs(tuacs, tval)
            if guacs:
                _sync_suppliers_by_uacs(guacs, gname)
            success = f'Imported {imported_groups} table(s) with {imported_titles} account title(s).'

    q = request.GET.get('q', '').strip()
    search_results = None

    groups = AccountTitleGroup.objects.prefetch_related(
        Prefetch('options', queryset=ManagementOption.objects.filter(category=ManagementOption.CATEGORY_ACCOUNT_TITLE), to_attr='prefetched_titles')
    ).annotate(
        title_count=Count('options')
    ).order_by('name')

    if q:
        matching_options = ManagementOption.objects.filter(
            category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
        ).filter(
            Q(value__icontains=q) | Q(uacs__icontains=q)
        ).select_related('group').order_by('group__name', 'value')

        search_results = []
        seen_group_ids = set()
        for opt in matching_options:
            if opt.group_id and opt.group_id not in seen_group_ids:
                seen_group_ids.add(opt.group_id)
                search_results.append({
                    'group': opt.group,
                    'titles': [],
                })
            if opt.group_id:
                search_results[-1]['titles'].append(opt)
            elif not opt.group_id:
                search_results.append({
                    'group': None,
                    'titles': [opt],
                })

    total_matches = sum(len(sr['titles']) for sr in (search_results or []))

    context = _page_context(request, is_admin=True, page_title='Account Titles', extra={
        'error':          error,
        'success':        success,
        'groups':         groups,
        'q':              q,
        'search_results': search_results,
        'total_matches':  total_matches,
    })
    return render(request, 'admin_panel/account_titles.html', context)


@login_required
def account_titles_import_preview(request):
    """AJAX: parse uploaded Excel/CSV, return JSON preview."""
    from cashier.models import DEFAULT_ROLE_PERMISSIONS
    profile = getattr(request.user, 'profile', None)
    role = 'admin' if request.user.is_superuser else (getattr(profile, 'role', 'guest') if profile else 'guest')
    role_config = DEFAULT_ROLE_PERMISSIONS.get(role, {"navigation": []})
    if 'account_titles_management' not in role_config.get('navigation', []):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    fname = uploaded.name.lower()
    groups = []   # [{name, uacs, titles:[{value, uacs}]}]

    def _clean_uacs(val):
        if val is None:
            return ''
        s = str(val).strip()
        return '' if s.lower() in ('', 'x', 'none', '-', 'n/a') else s

    try:
        if fname.endswith('.xlsx') or fname.endswith('.xls'):
            import openpyxl
            wb = openpyxl.load_workbook(uploaded, data_only=True)
            ws = wb.active
            current_group = None
            for row in ws.iter_rows():
                # Find first non-empty cell in the row
                first_cell = None
                for cell in row:
                    if cell.value is not None and str(cell.value).strip():
                        first_cell = cell
                        break
                if first_cell is None:
                    continue
                text = str(first_cell.value).strip()
                is_bold = bool(
                    first_cell.font and first_cell.font.bold
                )
                # Second column = UACS
                uacs_val = ''
                for cell in row:
                    if cell.column != first_cell.column and cell.value is not None:
                        uacs_val = _clean_uacs(cell.value)
                        break
                if is_bold:
                    current_group = {'name': text, 'uacs': _clean_uacs(uacs_val), 'titles': []}
                    groups.append(current_group)
                else:
                    if current_group is None:
                        current_group = {'name': 'Ungrouped', 'uacs': '', 'titles': []}
                        groups.append(current_group)
                    current_group['titles'].append({'value': text, 'uacs': uacs_val})
        elif fname.endswith('.csv'):
            import csv as _csv
            import io
            content = uploaded.read().decode('utf-8-sig', errors='replace')
            reader = _csv.reader(io.StringIO(content))
            current_group = None
            for row in reader:
                if not row or not row[0].strip():
                    continue
                text = row[0].strip()
                uacs_val = _clean_uacs(row[1] if len(row) > 1 else '')
                # Heuristic: ALL-CAPS or ends with no punctuation = group header
                is_header = text == text.upper() and len(text) > 2
                if is_header:
                    current_group = {'name': text, 'uacs': uacs_val, 'titles': []}
                    groups.append(current_group)
                else:
                    if current_group is None:
                        current_group = {'name': 'Ungrouped', 'uacs': '', 'titles': []}
                        groups.append(current_group)
                    current_group['titles'].append({'value': text, 'uacs': uacs_val})
        else:
            return JsonResponse({'error': 'Only .xlsx or .csv files are supported.'}, status=400)
    except Exception as exc:
        return JsonResponse({'error': f'Parse error: {exc}'}, status=400)

    return JsonResponse({'groups': groups})


@login_required
def account_title_group_detail(request, pk):
    """Detail page: view/add/edit/delete titles inside a group."""
    from cashier.models import DEFAULT_ROLE_PERMISSIONS
    profile = _get_profile(request.user)
    nav = DEFAULT_ROLE_PERMISSIONS.get(profile.role if profile else '', {}).get('navigation', [])
    if 'account_titles_management' not in nav:
        return redirect("cashier_dashboard")

    group = get_object_or_404(AccountTitleGroup, pk=pk)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        if action == 'add':
            value = request.POST.get('value', '').strip()
            uacs  = request.POST.get('uacs', '').strip()
            if not value:
                error = 'Account title is required.'
            else:
                obj, created = ManagementOption.objects.get_or_create(
                    category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
                    value=value,
                    defaults={'is_active': True, 'uacs': uacs, 'group': group},
                )
                if not created:
                    obj.group = group
                    obj.uacs  = uacs
                    obj.save(update_fields=['group', 'uacs'])
                success = 'Account title saved.'
                if uacs:
                    _sync_suppliers_by_uacs(uacs, value)
        elif action == 'edit':
            option_id = request.POST.get('option_id', '').strip()
            value     = request.POST.get('value', '').strip()
            uacs      = request.POST.get('uacs', '').strip()
            if not value:
                error = 'Account title is required.'
            else:
                ManagementOption.objects.filter(
                    pk=option_id,
                    category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
                ).update(value=value, uacs=uacs)
                success = 'Account title updated.'
                if uacs:
                    _sync_suppliers_by_uacs(uacs, value)
        elif action == 'delete':
            option_id = request.POST.get('option_id', '').strip()
            ManagementOption.objects.filter(
                pk=option_id,
                category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
            ).delete()
            success = 'Account title removed.'

    options = ManagementOption.objects.filter(
        category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
        group=group,
    ).order_by('value')

    context = _page_context(request, is_admin=True, page_title=group.name, extra={
        'error':   error,
        'success': success,
        'group':   group,
        'options': options,
    })
    return render(request, 'admin_panel/account_title_group_detail.html', context)


@login_required
def account_title_group_api(request, pk):
    """JSON API for account title group data (used by view modal)."""
    if not _is_admin(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    group = get_object_or_404(AccountTitleGroup, pk=pk)
    options = ManagementOption.objects.filter(
        category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
        group=group,
    ).order_by('value')

    return JsonResponse({
        "ok": True,
        "group": {
            "name": group.name,
            "uacs": getattr(group, 'uacs', '') or '',
        },
        "options": [
            {"value": opt.value, "uacs": getattr(opt, 'uacs', '') or ''}
            for opt in options
        ]
    })


@login_required
def remark_management(request):
    if not _is_admin(request.user):
        return redirect("cashier_dashboard")

    error = None
    success = None
    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        if action == 'add':
            value = request.POST.get('value', '').strip()
            if not value:
                error = 'Remark value is required.'
            else:
                ManagementOption.objects.get_or_create(
                    category=ManagementOption.CATEGORY_REMARK,
                    value=value,
                    defaults={'is_active': True},
                )
                success = 'Remark saved.'
        elif action == 'delete':
            option_id = request.POST.get('option_id', '').strip()
            ManagementOption.objects.filter(
                pk=option_id,
                category=ManagementOption.CATEGORY_REMARK,
            ).delete()
            success = 'Remark removed.'

    options = ManagementOption.objects.filter(
        category=ManagementOption.CATEGORY_REMARK,
    ).order_by('value')

    context = _page_context(request, is_admin=True, page_title='Remark Management', extra={
        'error': error,
        'success': success,
        'options': options,
        'heading': 'Remark Management',
        'placeholder': 'Add remark option',
    })
    return render(request, 'admin_panel/option_management.html', context)


@login_required
def taxes_management(request):
    if not _is_admin(request.user):
        return redirect('cashier_dashboard')

    error = None
    success = None
    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        if action == 'add':
            name = request.POST.get('name', '').strip()
            value = request.POST.get('value', '').strip()
            if not name or not value:
                error = 'Both Tax Name and Tax Value are required.'
            else:
                ManagementOption.objects.get_or_create(
                    category=ManagementOption.CATEGORY_TAX,
                    value=name,
                    defaults={'uacs': value, 'is_active': True},
                )
                success = 'Tax saved.'
        elif action == 'delete':
            option_id = request.POST.get('option_id', '').strip()
            ManagementOption.objects.filter(
                pk=option_id,
                category=ManagementOption.CATEGORY_TAX,
            ).delete()
            success = 'Tax removed.'

    existing = ManagementOption.objects.filter(
        category=ManagementOption.CATEGORY_TAX,
    ).order_by('value')
    if not existing.exists():
        defaults = [
            {'name': 'Tax (5%/3%)', 'value': '5%/3%'},
            {'name': 'Tax (2%/1%)', 'value': '2%/1%'},
            {'name': 'Professional Tax', 'value': '0%'},
        ]
        for item in defaults:
            ManagementOption.objects.get_or_create(
                category=ManagementOption.CATEGORY_TAX,
                value=item['name'],
                defaults={'uacs': item['value'], 'is_active': True},
            )
        existing = ManagementOption.objects.filter(
            category=ManagementOption.CATEGORY_TAX,
        ).order_by('value')

    context = _page_context(request, is_admin=True, page_title='Taxes', extra={
        'error': error,
        'success': success,
        'options': existing,
        'heading': 'Tax Management',
        'placeholder': 'Add tax label',
    })
    return render(request, 'admin_panel/option_management.html', context)


@login_required
def system_settings(request):
    return business_info(request)


@login_required
def verify_password(request):
    if request.method == "POST":
        import json
        password = None
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
                password = data.get("password")
            except Exception:
                pass
        if not password:
            password = request.POST.get("password")
        if not password:
            return JsonResponse({"ok": False, "error": "Password required"}, status=400)
        if request.user.check_password(password):
            return JsonResponse({"ok": True})
        return JsonResponse({"ok": False, "error": "Wrong password"}, status=403)
    return JsonResponse({"ok": False, "error": "POST required"}, status=400)


@login_required
def lockscreen_view(request):
    error = None
    next_url = request.GET.get('next') or request.POST.get('next')
    hard = request.GET.get('hard', '0') == '1' or request.session.get('screen_lock_hard', False)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    request.session['screen_locked'] = True
    if hard:
        request.session['screen_lock_hard'] = True
    request.session.modified = True

    if request.method == "POST":
        hard = request.POST.get('hard', '0') == '1' or request.session.get('screen_lock_hard', False)
        password = request.POST.get('password')

        if not hard:
            request.session['screen_locked'] = False
            request.session['screen_lock_hard'] = False
            profile = _get_profile(request.user)
            destination = next_url if (next_url and not next_url.startswith('/lockscreen')) else \
                reverse("admin_dashboard") if profile.role == "admin" else reverse("cashier_dashboard")
            if is_ajax:
                return JsonResponse({"status": "success", "redirect_url": destination})
            return redirect(destination)

        if not password:
            error = "Password is required"
        elif request.user.check_password(password):
            request.session['screen_locked'] = False
            request.session['screen_lock_hard'] = False
            profile = _get_profile(request.user)
            destination = next_url if (next_url and not next_url.startswith('/lockscreen')) else \
                reverse("admin_dashboard") if profile.role == "admin" else reverse("cashier_dashboard")
            if is_ajax:
                return JsonResponse({"status": "success", "redirect_url": destination})
            return redirect(destination)
        else:
            error = "Wrong password!"
            if is_ajax:
                return JsonResponse({"status": "error", "error": error}, status=401)

    profile = _get_profile(request.user)
    context = {
        "current_profile": profile,
        "error": error,
        "next": next_url or "",
        "hard_lock": hard,
    }
    return render(request, "lockscreen.html", context)


@login_required
def mfa_setup(request):
    profile = _get_profile(request.user)
    is_admin = _is_admin(request.user)
    error = None
    success = None
    show_qr = False
    new_secret = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "enable_totp":
            new_secret = request.POST.get("secret", "")
            code = request.POST.get("code", "").strip()
            from .mfa_utils import verify_totp, generate_recovery_codes, hash_recovery_code
            if not code:
                error = "Please enter the code from your authenticator app."
            elif verify_totp(new_secret, code):
                profile.totp_secret = new_secret
                profile.totp_enabled = True
                raw_codes = generate_recovery_codes()
                profile.mfa_recovery_codes = [hash_recovery_code(c) for c in raw_codes]
                profile.save(update_fields=["totp_secret", "totp_enabled", "mfa_recovery_codes"])
                success = "Authenticator app enabled successfully."
                request.session["mfa_recovery_codes_raw"] = raw_codes
            else:
                error = "Invalid code. Make sure your authenticator app is set up correctly."
        elif action == "disable_totp":
            otp_code = request.POST.get("otp_code", "").strip()
            expected = request.session.get("disable_totp_otp_code")
            otp_created = request.session.get("disable_totp_otp_created")
            if not otp_code:
                error = "Please enter the OTP code sent to your email."
            elif not expected:
                error = "No verification code pending. Please request a new code."
            else:
                from django.utils import timezone as _tz
                from datetime import timedelta
                otp_expired = False
                if otp_created:
                    try:
                        created_dt = _tz.datetime.fromisoformat(otp_created)
                        if _tz.is_naive(created_dt):
                            created_dt = _tz.make_aware(created_dt)
                        otp_expired = (_tz.now() - created_dt) > timedelta(minutes=10)
                    except Exception:
                        otp_expired = True
                if otp_expired:
                    error = "OTP code has expired. Please request a new code."
                    request.session.pop("disable_totp_otp_code", None)
                    request.session.pop("disable_totp_otp_created", None)
                elif otp_code != expected:
                    error = "Invalid OTP code. Please try again."
                else:
                    profile.totp_secret = ""
                    profile.totp_enabled = False
                    profile.mfa_recovery_codes = []
                    profile.save(update_fields=["totp_secret", "totp_enabled", "mfa_recovery_codes"])
                    request.session.pop("disable_totp_otp_code", None)
                    request.session.pop("disable_totp_otp_created", None)
                    _audit(request.user, "Authenticator app disabled")
                    success = "Authenticator app disabled."
        elif action == "enable_email_otp":
            if not request.user.email:
                error = "You must have an email address set on your account."
            else:
                profile.email_otp_enabled = True
                from .mfa_utils import generate_recovery_codes, hash_recovery_code
                raw_codes = generate_recovery_codes()
                profile.mfa_recovery_codes = [hash_recovery_code(c) for c in raw_codes]
                profile.save(update_fields=["email_otp_enabled", "mfa_recovery_codes"])
                success = "Email OTP enabled."
                request.session["mfa_recovery_codes_raw"] = raw_codes
        elif action == "disable_email_otp":
            otp_code = request.POST.get("otp_code", "").strip()
            expected = request.session.get("disable_email_otp_code")
            otp_created = request.session.get("disable_email_otp_created")
            if not otp_code:
                error = "Please enter the OTP code sent to your email."
            elif not expected:
                error = "No verification code pending. Please request a new code."
            else:
                from django.utils import timezone as _tz
                from datetime import timedelta
                otp_expired = False
                if otp_created:
                    try:
                        created_dt = _tz.datetime.fromisoformat(otp_created)
                        if _tz.is_naive(created_dt):
                            created_dt = _tz.make_aware(created_dt)
                        otp_expired = (_tz.now() - created_dt) > timedelta(minutes=10)
                    except Exception:
                        otp_expired = True
                if otp_expired:
                    error = "OTP code has expired. Please request a new code."
                    request.session.pop("disable_email_otp_code", None)
                    request.session.pop("disable_email_otp_created", None)
                elif otp_code != expected:
                    error = "Invalid OTP code. Please try again."
                else:
                    profile.email_otp_enabled = False
                    profile.save(update_fields=["email_otp_enabled"])
                    request.session.pop("disable_email_otp_code", None)
                    request.session.pop("disable_email_otp_created", None)
                    _audit(request.user, "Email OTP disabled")
                    success = "Email OTP disabled."
        elif action == "generate_recovery_codes":
            from .mfa_utils import generate_recovery_codes, hash_recovery_code
            raw_codes = generate_recovery_codes()
            profile.mfa_recovery_codes = [hash_recovery_code(c) for c in raw_codes]
            profile.save(update_fields=["mfa_recovery_codes"])
            request.session["mfa_recovery_codes_raw"] = raw_codes
            _audit(request.user, "Recovery codes regenerated")
            success = "New recovery codes generated."

            # Send recovery codes to user email
            if request.user.email:
                try:
                    from django.core.mail import send_mail
                    from django.conf import settings
                    codes_text = "\n".join(raw_codes)
                    send_mail(
                        subject="Your Recovery Codes",
                        message=f"Hello {request.user.first_name or request.user.username},\n\n"
                                f"Here are your new recovery codes for {getattr(settings, 'SYSTEM_NAME', 'the system')}:\n\n"
                                f"{codes_text}\n\n"
                                f"Each code can only be used once. Save them in a secure location.\n\n"
                                f"If you did not request this, please contact your administrator.",
                        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
                        recipient_list=[request.user.email],
                        fail_silently=True,
                    )
                    success += " Codes sent to your email."
                except Exception:
                    success += " Could not send email, but codes are shown above."

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax and request.method == 'POST':
        if error:
            return JsonResponse({"ok": False, "error": error})
        if success:
            return JsonResponse({"ok": True, "success": success})
        return JsonResponse({"ok": False, "error": "Unknown action."})

    if not profile.totp_enabled:
        from .mfa_utils import generate_totp_secret
        new_secret = generate_totp_secret()
        show_qr = True

    recovery_codes_raw = request.session.get("mfa_recovery_codes_raw", None)
    recovery_count = len(profile.mfa_recovery_codes) if profile.mfa_recovery_codes else 0

    context = _page_context(request, is_admin=is_admin, page_title="MFA Settings", extra={
        "error": error,
        "success": success,
        "profile": profile,
        "show_qr": show_qr,
        "new_secret": new_secret,
        "recovery_codes_raw": recovery_codes_raw,
        "recovery_codes_remaining": recovery_count,
    })
    return render(request, "admin_panel/mfa_setup.html", context)


@login_required
@require_POST
def mfa_regenerate_recovery_codes(request):
    profile = _get_profile(request.user)
    if not profile.totp_enabled and not profile.email_otp_enabled:
        return JsonResponse({"ok": False, "error": "MFA is not enabled."}, status=400)
    from .mfa_utils import generate_recovery_codes, hash_recovery_code
    raw_codes = generate_recovery_codes()
    profile.mfa_recovery_codes = [hash_recovery_code(c) for c in raw_codes]
    profile.save(update_fields=["mfa_recovery_codes"])
    _audit(request.user, "MFA recovery codes regenerated")
    return JsonResponse({"ok": True, "codes": raw_codes, "count": len(raw_codes)})


@login_required
def mfa_totp_qr(request):
    from .mfa_utils import get_totp_uri
    import qrcode.image.svg
    secret = request.GET.get("secret", "")
    if not secret:
        return HttpResponse("Missing secret", status=400)
    uri = get_totp_uri(secret, request.user.username)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgImage)
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
    from .models import SystemSetting
    from django.utils import timezone as _tz
    sys_set = SystemSetting.get_settings()
    otp = generate_email_otp()
    request.session["email_otp_code"] = otp
    request.session["email_otp_created"] = _tz.now().isoformat()
    try:
        send_otp_email(user.email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo)
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@login_required
@require_POST
def mfa_send_disable_email_otp(request):
    profile = _get_profile(request.user)
    if not profile.email_otp_enabled:
        return JsonResponse({"ok": False, "error": "Email OTP is not enabled."}, status=400)
    if not request.user.email:
        return JsonResponse({"ok": False, "error": "No email address on file."}, status=400)
    from .mfa_utils import generate_email_otp, send_otp_email
    from .models import SystemSetting
    from django.utils import timezone as _tz
    sys_set = SystemSetting.get_settings()
    otp = generate_email_otp()
    request.session["disable_email_otp_code"] = otp
    request.session["disable_email_otp_created"] = _tz.now().isoformat()
    try:
        send_otp_email(request.user.email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo,
                       extra_subject=f'{sys_set.system_name} – Disable Email OTP Verification',
                       extra_html=_disable_email_otp_html(request.user.email, otp, sys_set.system_name, sys_set.system_logo))
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


def _disable_email_otp_html(email, otp, system_name=None, system_logo=None):
    if not system_name:
        system_name = "Registry"
    from .mfa_utils import _logo_data_uri
    logo_data = _logo_data_uri(system_logo)
    logo_html = ""
    if logo_data:
        logo_html = f'<img src="{logo_data}" alt="{system_name}" style="max-width:120px; height:auto; margin-bottom:16px; display:block; margin-left:auto; margin-right:auto;">'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f4f6f8; font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8; padding:40px 0;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px; text-align:center; background:#7f1d1d;">
{logo_html}
<h1 style="color:#fff; font-size:20px; font-weight:600; margin:0;">{system_name}</h1>
</td></tr>
<tr><td style="padding:32px 40px;">
<p style="color:#374151; font-size:15px; margin:0 0 8px;">You are about to <strong>disable Email OTP</strong> on your account. Enter this code to confirm:</p>
<div style="background:#fef2f2; border:1px solid #fca5a5; border-radius:10px; padding:18px; text-align:center; margin:16px 0;">
<span style="font-size:38px; font-weight:700; letter-spacing:8px; color:#991b1b;">{otp}</span>
</div>
<p style="color:#6b7280; font-size:13px; margin:0;">This code expires in <strong>10 minutes</strong>. If you did not request this, secure your account immediately.</p>
</td></tr>
<tr><td style="padding:16px 40px; background:#f9fafb; text-align:center; border-top:1px solid #e5e7eb;">
<p style="color:#9ca3af; font-size:11px; margin:0;">&copy; {system_name}</p>
</td></tr>
</table></td></tr></table>
</body></html>"""


@login_required
@require_POST
def mfa_send_disable_totp_otp(request):
    profile = _get_profile(request.user)
    if not profile.totp_enabled:
        return JsonResponse({"ok": False, "error": "Authenticator app is not enabled."}, status=400)
    if not request.user.email:
        return JsonResponse({"ok": False, "error": "No email address on file."}, status=400)
    from .mfa_utils import generate_email_otp, send_otp_email
    from .models import SystemSetting
    from django.utils import timezone as _tz
    sys_set = SystemSetting.get_settings()
    otp = generate_email_otp()
    request.session["disable_totp_otp_code"] = otp
    request.session["disable_totp_otp_created"] = _tz.now().isoformat()
    try:
        send_otp_email(request.user.email, otp, system_name=sys_set.system_name, system_logo=sys_set.system_logo,
                       extra_subject=f'{sys_set.system_name} – Disable Authenticator App Verification',
                       extra_html=_disable_totp_otp_html(request.user.email, otp, sys_set.system_name, sys_set.system_logo))
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


def _disable_totp_otp_html(email, otp, system_name=None, system_logo=None):
    if not system_name:
        system_name = "Registry"
    from .mfa_utils import _logo_data_uri
    logo_data = _logo_data_uri(system_logo)
    logo_html = ""
    if logo_data:
        logo_html = f'<img src="{logo_data}" alt="{system_name}" style="max-width:120px; height:auto; margin-bottom:16px; display:block; margin-left:auto; margin-right:auto;">'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f4f6f8; font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8; padding:40px 0;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px; text-align:center; background:#7f1d1d;">
{logo_html}
<h1 style="color:#fff; font-size:20px; font-weight:600; margin:0;">{system_name}</h1>
</td></tr>
<tr><td style="padding:32px 40px;">
<p style="color:#374151; font-size:15px; margin:0 0 8px;">You are about to <strong>disable your Authenticator App</strong>. Enter this code to confirm:</p>
<div style="background:#fef2f2; border:1px solid #fca5a5; border-radius:10px; padding:18px; text-align:center; margin:16px 0;">
<span style="font-size:38px; font-weight:700; letter-spacing:8px; color:#991b1b;">{otp}</span>
</div>
<p style="color:#6b7280; font-size:13px; margin:0;">This code expires in <strong>10 minutes</strong>. If you did not request this, secure your account immediately.</p>
</td></tr>
<tr><td style="padding:16px 40px; background:#f9fafb; text-align:center; border-top:1px solid #e5e7eb;">
<p style="color:#9ca3af; font-size:11px; margin:0;">&copy; {system_name}</p>
</td></tr>
</table></td></tr></table>
</body></html>"""


@login_required
@require_POST
def trigger_lock(request):
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




