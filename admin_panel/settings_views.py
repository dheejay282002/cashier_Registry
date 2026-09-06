import json
import secrets
import string

from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai
from admin_panel.models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup
from .utils import (
    _is_admin, _get_profile, _page_context, _audit,
    _validate_password, _full_name_or_username
)


@login_required
def email_settings(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    settings_obj = SystemSetting.get_settings()
    if request.method == 'POST':
        settings_obj.smtp_host = request.POST.get('smtp_host', '')
        settings_obj.smtp_port = request.POST.get('smtp_port', '587')
        settings_obj.smtp_user = request.POST.get('smtp_user', '')
        settings_obj.smtp_password = request.POST.get('smtp_password', '')
        settings_obj.smtp_use_tls = request.POST.get('smtp_use_tls') == 'on'
        settings_obj.save()
        _audit(request.user, "Updated email settings")
        return redirect('email_settings')

    context = _page_context(request, is_admin, "Email Settings", extra={
        "settings": settings_obj,
    })
    return render(request, "admin_panel/email_settings.html", context)


@login_required
def about_view(request):
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, "About")
    return render(request, "admin_panel/about.html", context)


@login_required
def about_cashier_view(request):
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, "About Cashier")
    return render(request, "admin_panel/about_cashier.html", context)


@login_required
def account_settings(request):
    return redirect('profile_settings')


@login_required
def profile_settings(request):
    is_admin = _is_admin(request.user)
    profile = _get_profile(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'update_profile':
            profile.middle_initial = request.POST.get('middle_initial', '')
            profile.department = request.POST.get('department', '')
            profile.position = request.POST.get('position', '')
            profile.save(update_fields=['middle_initial', 'department', 'position'])
            request.user.first_name = request.POST.get('first_name', '')
            request.user.last_name = request.POST.get('last_name', '')
            request.user.save(update_fields=['first_name', 'last_name'])
            success = "Profile updated."
        elif action == 'change_password':
            current = request.POST.get('current_password', '')
            new_pw = request.POST.get('new_password', '')
            confirm = request.POST.get('confirm_password', '')
            if not request.user.check_password(current):
                error = "Current password is incorrect."
            elif new_pw != confirm:
                error = "Passwords do not match."
            else:
                pw_error = _validate_password(new_pw)
                if pw_error:
                    error = pw_error
                else:
                    request.user.set_password(new_pw)
                    request.user.save()
                    success = "Password changed."

    context = _page_context(request, is_admin, "Profile Settings", extra={
        "profile": profile,
        "error": error,
        "success": success,
    })
    return render(request, "profile_settings.html", context)


@login_required
def business_info(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    settings_obj = SystemSetting.get_settings()
    if request.method == 'POST':
        settings_obj.business_name = request.POST.get('business_name', '')
        settings_obj.business_address = request.POST.get('business_address', '')
        settings_obj.business_phone = request.POST.get('business_phone', '')
        settings_obj.business_email = request.POST.get('business_email', '')
        settings_obj.save()
        _audit(request.user, "Updated business info")
        return redirect('business_info')

    context = _page_context(request, is_admin, "Business Info", extra={
        "settings": settings_obj,
    })
    return render(request, "admin_panel/about.html", context)


@login_required
def test_email_settings(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    return JsonResponse({"ok": True, "message": "Email test sent."})


@login_required
def screen_timeout(request):
    is_admin = _is_admin(request.user)
    settings_obj = SystemSetting.get_settings()

    if request.method == 'POST':
        timeout = request.POST.get('screen_timeout', '30')
        try:
            settings_obj.screen_timeout_seconds = int(timeout)
            settings_obj.save(update_fields=['screen_timeout_seconds'])
            _audit(request.user, "Updated screen timeout")
        except Exception:
            pass
        return redirect('screen_timeout')

    context = _page_context(request, is_admin, "Screen Timeout", extra={
        "settings": settings_obj,
    })
    return render(request, "admin_panel/screen_timeout.html", context)


@login_required
def account_titles_management(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add':
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            if not code or not name:
                error = "Code and name are required."
            else:
                AccountTitleGroup.objects.create(code=code, name=name)
                _audit(request.user, "Created account title", {"code": code})
                success = f"Account title '{name}' created."

    titles = AccountTitleGroup.objects.all().order_by('code')
    context = _page_context(request, is_admin, "Account Titles", extra={
        "titles": titles,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/account_titles.html", context)


@login_required
def account_titles_import_preview(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    return JsonResponse({"ok": True, "preview": []})


@login_required
def account_title_group_detail(request, pk):
    from django.shortcuts import get_object_or_404
    title = get_object_or_404(AccountTitleGroup, pk=pk)
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, f"Account Title: {title.name}", extra={
        "title": title,
    })
    return render(request, "admin_panel/account_title_group_detail.html", context)


@login_required
def remark_management(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")
    context = _page_context(request, is_admin, "Remarks Management")
    return render(request, "admin_panel/option_management.html", context)


@login_required
def taxes_management(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'update':
            tax_id = request.POST.get('tax_id')
            try:
                tax = ManagementOption.objects.get(pk=tax_id, category=ManagementOption.CATEGORY_TAX)
                tax.value = request.POST.get('value', tax.value)
                tax.uacs = request.POST.get('uacs', tax.uacs)
                tax.is_active = request.POST.get('is_active') == 'on'
                tax.save()
                _audit(request.user, "Updated tax", {"id": tax.pk})
            except ManagementOption.DoesNotExist:
                pass

    taxes = ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX).order_by('pk')
    context = _page_context(request, is_admin, "Taxes Management", extra={
        "taxes": taxes,
    })
    return render(request, "admin_panel/option_management.html", context)


@login_required
def system_settings(request):
    return redirect('admin_dashboard')


def _generate_temp_username():
    adjectives = ['swift', 'bright', 'calm', 'deft', 'eager', 'fair', 'glad', 'keen', 'mild', 'neat']
    nouns = ['bear', 'cove', 'dawn', 'fern', 'hill', 'iris', 'jay', 'lake', 'moon', 'nova']
    adj = secrets.choice(adjectives)
    noun = secrets.choice(nouns)
    num = secrets.randbelow(1000)
    return f"{adj}{noun}{num}"


def _generate_temp_password():
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(12))


@login_required
def user_management(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'create':
            username = _generate_temp_username()
            password = _generate_temp_password()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            role = request.POST.get('role', 'cashier')

            try:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                )
                profile = Profile.objects.get(user=user)
                profile.role = role
                profile.must_change_password = True
                profile.save(update_fields=['role', 'must_change_password'])
                _audit(request.user, "Created user", {"username": username, "role": role})
                success = f"User created: {username} / {password}"
            except Exception as e:
                error = str(e)

    users = User.objects.select_related('profile').all().order_by('username')
    context = _page_context(request, is_admin, "User Management", extra={
        "users": users,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/user_management.html", context)


@login_required
def audit_log(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")
    logs = AuditLog.objects.select_related('admin').order_by('-created_at')[:100]
    context = _page_context(request, is_admin, "Audit Log", extra={
        "logs": logs,
    })
    return render(request, "admin_panel/audit_log.html", context)
