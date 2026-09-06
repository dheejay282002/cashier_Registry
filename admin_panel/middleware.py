from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import quote


# Paths that are always allowed even when session is locked
_ALWAYS_ALLOWED = {
    '/',           # login page
    '/logout/',
    '/lockscreen/',
    '/verify-password/',
    '/lock/',
}

_ALLOWED_PREFIXES = (
    '/static/',
    '/media/',
    '/admin/',
)


class LockScreenMiddleware:
    """
    Intercepts every request for an authenticated user.
    If the session flag 'screen_locked' is True, redirect to /lockscreen/
    so that pressing the browser back-button, editing the URL bar, or any
    other navigation cannot bypass the lock screen.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.session.get('screen_locked')
            and request.path not in _ALWAYS_ALLOWED
            and not any(request.path.startswith(p) for p in _ALLOWED_PREFIXES)
        ):
            # AJAX / fetch requests: return 423 so JS can handle gracefully
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            if is_ajax:
                from django.http import JsonResponse
                return JsonResponse({'locked': True}, status=423)

            hard = '1' if request.session.get('screen_lock_hard') else '0'
            next_path = quote(
                request.path
                + (('?' + request.META.get('QUERY_STRING', '')) if request.META.get('QUERY_STRING') else '')
            )
            return redirect(f"/lockscreen/?next={next_path}&hard={hard}")

        return self.get_response(request)


from django.urls import resolve
from django.core.exceptions import PermissionDenied

class RolePermissionsMiddleware:
    """
    Intercepts requests for authenticated users and restricts access
    to navigation links and module actions based on their role configuration.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Superuser and admin bypass checks
        if request.user.is_superuser:
            return self.get_response(request)

        # Get user profile
        from admin_panel.views import _get_profile
        profile = _get_profile(request.user)
        if profile.role == 'admin':
            return self.get_response(request)

        # Get the resolved URL name
        try:
            resolver_match = resolve(request.path_info)
            url_name = resolver_match.url_name
        except Exception:
            return self.get_response(request)

        if not url_name:
            return self.get_response(request)

        # Map URL names to their respective navigation keys and action modules
        # format: url_name -> (nav_key, action_module, action_name)
        URL_PERMISSION_MAP = {
            # Dashboards
            'admin_dashboard': ('dashboard', None, None),
            'cashier_dashboard': ('dashboard', None, None),
            
            # User Settings
            'profile_settings': ('profile_settings', None, None),
            'account_settings': ('profile_settings', None, None),
            
            # System settings
            'business_info': ('system_settings', None, None),
            'system_settings': ('system_settings', None, None),
            'account_titles_management': ('system_settings', None, None),
            'account_title_group_detail': ('system_settings', None, None),
            'account_titles_import_preview': ('system_settings', None, None),
            'taxes_management': ('system_settings', None, None),
            
            # User Management
            'user_management': ('user_management', 'users', None),
            'create_cashier': ('user_management', 'users', None),
            
            # Fund clusters
            'fund_clusters': ('fund_clusters', 'fund_clusters', None),
            
            # Suppliers
            'suppliers': ('suppliers', 'suppliers', None),
            'cashier_suppliers': ('suppliers', 'suppliers', None),
            'update_supplier_remark': ('suppliers', 'suppliers', 'edit'),
            
            # RADAI
            'radai': ('radai', 'radai', None),
            'cashier_radai': ('radai', 'radai', None),
            'radai_detail': ('radai', 'radai', None),
            'update_radai_remark': ('radai', 'radai', 'edit'),
            'update_radai_settings': ('radai', 'radai', 'edit'),
            'export_radai': ('radai', 'radai', None),
            
            # Registry
            'registry': ('registry', None, None),
            
            # Cheques
            'cheque_list': ('cheques', 'cheques', 'view'),
            'cheque_create': ('cheque_create', 'cheques', 'create'),
            'cheque_edit': ('cheques', 'cheques', 'edit'),
            'cheque_print': ('cheques', 'cheques', 'print'),
            'cheque_pdf': ('cheques', 'cheques', 'print'),
            'cheque_void': ('cheques', 'cheques', 'void'),
            'cheque_delete': ('cheques', 'cheques', 'void'),
            'cheque_advance_status': ('cheques', 'cheques', 'advance_status'),
            
            # Reports
            'reports': ('reports', 'reports', 'view'),
            'report_detail': ('reports', 'reports', 'view'),
            'report_delete': ('reports', 'reports', 'generate'),
            'report_print': ('reports', 'reports', 'view'),
            'report_print_modal': ('reports', 'reports', 'view'),
            'report_export_preview': ('reports', 'reports', 'view'),
            'export_report_pdf': ('reports', 'reports', 'view'),
            'fund_cluster_info': ('reports', 'reports', 'view'),
            
            # Import / Export
            'import_data': ('import_data', None, None),
            'download_import_template': ('import_data', None, None),
            'export_cheques': ('export', None, None),
            'export_suppliers': ('export', None, None),
            
            # Audit log
            'audit_log': ('audit_log', None, None),
            
            # Manage Devices
            'manage_devices': ('manage_devices', None, None),
            'terminate_session': ('manage_devices', None, None),
            'toggle_device_block': ('manage_devices', None, None),
            'unterminate_session': ('manage_devices', None, None),
            'delete_device': ('manage_devices', None, None),
            'send_user_message': (None, None, None),
            'poll_messages': (None, None, None),
            'get_conversation': (None, None, None),
            'get_all_users': (None, None, None),
            'get_my_conversations': (None, None, None),
            
            # Remark management
            'remark_management': ('remark_management', None, None),
            
            # Backup & Restore
            'backup_restore': ('backup_restore', None, None),
        }

        if url_name in URL_PERMISSION_MAP:
            nav_key, module, action = URL_PERMISSION_MAP[url_name]
            
            from cashier.models import DEFAULT_ROLE_PERMISSIONS
            role_config = DEFAULT_ROLE_PERMISSIONS.get(profile.role, {"navigation": [], "permissions": {}})
            
            # Check navigation permission
            if nav_key:
                allowed_nav = role_config.get('navigation', [])
                if nav_key not in allowed_nav:
                    raise PermissionDenied("You do not have access to this section.")
                    
            # Check action permission
            if module:
                resolved_action = action
                if not resolved_action:
                    # Resolve action dynamically based on POST request
                    if request.method == 'POST':
                        post_action = request.POST.get('action', '').strip().lower()
                        if post_action in ('create', 'add'):
                            resolved_action = 'create'
                        elif post_action == 'edit':
                            resolved_action = 'edit'
                        elif post_action == 'delete':
                            resolved_action = 'delete'
                        else:
                            resolved_action = 'view'
                    else:
                        resolved_action = 'view'
                        
                allowed_actions = role_config.get('permissions', {}).get(module, [])
                if resolved_action not in allowed_actions:
                    raise PermissionDenied("You do not have permission to perform this action.")

        return self.get_response(request)


class SingleDeviceLoginMiddleware:
    """
    Enforces one-active-session-per-user.

    When a user logs in we store their new session key on their Profile
    (active_session_key).  Every subsequent request checks that the current
    session key still matches.  If a different session has since been
    registered (another device / browser tab logged in) the old session is
    forcefully terminated and the original browser is redirected to the login
    page with a warning.

    Paths that are excluded from the check:
      - Login / logout / lock-screen pages (to avoid redirect loops).
      - Static/media files.
    """

    _SKIP_PATHS = {
        '/',
        '/logout/',
        '/lockscreen/',
        '/verify-password/',
        '/lock/',
        '/access-denied/',
        '/session-check/',      # the polling endpoint (below)
    }

    _SKIP_PREFIXES = (
        '/static/',
        '/media/',
        '/admin/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.path not in self._SKIP_PATHS
            and not any(request.path.startswith(p) for p in self._SKIP_PREFIXES)
        ):
            try:
                profile = request.user.profile
                active_key = profile.active_session_key or ''
                current_key = request.session.session_key or ''

                from django.utils import timezone
                now = timezone.now()
                if not profile.last_activity or (now - profile.last_activity).total_seconds() > 10:
                    profile.last_activity = now
                    profile.save(update_fields=['last_activity'])
                    
                    from cashier.models import UserDevice
                    UserDevice.objects.filter(session_key=current_key).update(last_activity=now)

                from cashier.models import UserDevice
                device_qs = UserDevice.objects.filter(session_key=current_key)
                if device_qs.filter(is_blocked=True).exists():
                    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    if is_ajax:
                        from django.http import JsonResponse
                        return JsonResponse({'blocked': True}, status=403)
                    from django.shortcuts import render
                    return render(request, "admin_panel/device_blocked.html", {
                        "device_ip": request.META.get('REMOTE_ADDR'),
                        "username": request.user.username
                    })

                is_terminated = device_qs.filter(is_terminated=True).exists()

                if is_terminated or (active_key and current_key and active_key != current_key):
                    from django.contrib.auth import logout
                    from django.http import JsonResponse
                    request.session.flush()
                    logout(request)

                    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    if is_ajax:
                        return JsonResponse(
                            {'kicked': True,
                             'message': 'Your session has been terminated by the administrator.' if is_terminated else 'You have been logged out because your account was signed in on another device.'},
                            status=401
                        )
                    from django.shortcuts import redirect
                    return redirect('/?kicked=1')
            except Exception:
                pass

        return self.get_response(request)

