from django.shortcuts import redirect

import ipaddress
import os
from collections import defaultdict
from urllib.parse import urlsplit

from django.conf import settings as django_settings
from django.http import HttpResponsePermanentRedirect
from django.middleware.csrf import CsrfViewMiddleware

# Set REGISTRY_FORCE_HTTPS=0 to disable the http -> https upgrade entirely.
_FORCE_HTTPS = os.getenv("REGISTRY_FORCE_HTTPS", "1").strip().lower() not in ("0", "false", "no")

# Snapshots of the static values defined in registry/settings.py, captured at
# import time (before any request mutates them). The ALLOWED_HOSTS entries
# here are permanent local fallbacks (localhost / 127.0.0.1).
_BASE_ALLOWED_HOSTS = tuple(django_settings.ALLOWED_HOSTS)
_BASE_CSRF_TRUSTED_ORIGINS = tuple(django_settings.CSRF_TRUSTED_ORIGINS)

# Hosts loaded from the database on the previous request (None = first run).
_DB_HOSTS = None


def refresh_allowed_hosts():
    """Sync settings.ALLOWED_HOSTS and settings.CSRF_TRUSTED_ORIGINS with the
    hosts configured in the admin UI (System Settings -> Allowed Hosts).

    Uses a delta against the previous request so entries added elsewhere
    (e.g. "testserver" appended by Django's test runner) are preserved while
    hosts deleted in the UI are dropped immediately — no restart needed.
    """
    global _DB_HOSTS
    try:
        from .models import AllowedHost
        db_hosts = AllowedHost.get_hosts()
    except Exception:
        # Database unreachable (e.g. before the first migrate) — keep the
        # current list so the static fallback in registry/settings.py applies.
        return
    db_set = set(db_hosts)
    current = list(django_settings.ALLOWED_HOSTS)
    removed = set() if _DB_HOSTS is None else (_DB_HOSTS - db_set)
    new_hosts = [h for h in current if h not in removed]
    new_hosts.extend(h for h in db_hosts if h not in new_hosts)
    new_hosts.extend(h for h in _BASE_ALLOWED_HOSTS if h not in new_hosts)
    django_settings.ALLOWED_HOSTS = list(dict.fromkeys(new_hosts))
    _DB_HOSTS = db_set

    # Keep CSRF trusted origins in sync so forms POSTed from a newly added
    # host pass Django's Origin check. Deployments sit behind TLS-terminating
    # proxies (ngrok, Cloudflare, Railway), so both schemes are trusted for
    # every host — the proxy makes Django see http while the browser sends an
    # https Origin.
    origins = list(_BASE_CSRF_TRUSTED_ORIGINS)
    for host in django_settings.ALLOWED_HOSTS:
        if host == "*":
            continue
        pattern = f"*{host}" if host.startswith(".") else host
        origins.append(f"https://{pattern}")
        origins.append(f"http://{pattern}")
    django_settings.CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(origins))


class AllowedHostsMiddleware:
    """Loads the admin-managed allowed hosts before anything validates the
    request host. Place it right after SecurityMiddleware so every
    request.get_host() call (CSRF, redirects, views) sees the current list.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        refresh_allowed_hosts()
        return self.get_response(request)


def _is_local_host(raw_host):
    """True for localhost / loopback / private LAN hosts (no TLS available)."""
    host = (raw_host or "").strip().lower()
    if host.startswith("["):                      # IPv6 literal, e.g. [::1]:8000
        host = host[1:host.find("]")] if "]" in host else host
        return host in ("::1", "0:0:0:0:0:0:0:1")
    host = host.split(":", 1)[0]                  # drop :port (hostname / IPv4)
    if host in ("localhost", "127.0.0.1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private   # loopback + 10/172.192 LAN
    except ValueError:
        return False


class HttpsRedirectMiddleware:
    """Keeps public traffic on https while plain-http local access keeps working.

    TLS terminates at the tunnel/proxy in front of the server (ngrok,
    Cloudflare, Railway), which forwards plain HTTP and reports the original
    scheme via X-Forwarded-Proto — trusted through SECURE_PROXY_SSL_HEADER in
    registry/settings.py.

    - Request: a proxy-fronted http:// visit to a public host is redirected
      (301) to https://. localhost / 127.0.0.1 / private LAN addresses are
      exempt (they have no certificate), as are direct connections without an
      X-Forwarded-Proto header. REGISTRY_FORCE_HTTPS=0 disables this.
    - Response: on plain-http requests the Secure flag is dropped from the
      session/CSRF cookies, so SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE can
      stay on for https without breaking http LAN logins.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _FORCE_HTTPS and not request.is_secure() and "HTTP_X_FORWARDED_PROTO" in request.META:
            try:
                host = request.get_host()
            except Exception:
                host = None
            if host and not _is_local_host(host):
                return HttpResponsePermanentRedirect(f"https://{host}{request.get_full_path()}")
        response = self.get_response(request)
        if not request.is_secure():
            for cookie_name in (django_settings.SESSION_COOKIE_NAME, django_settings.CSRF_COOKIE_NAME):
                morsel = response.cookies.get(cookie_name)
                if morsel is not None:
                    morsel.pop("secure", None)
        return response


class DynamicCsrfViewMiddleware(CsrfViewMiddleware):
    """Drop-in replacement for Django's CsrfViewMiddleware.

    Django computes the CSRF trusted-origin sets with @cached_property, i.e.
    once per process, so hosts added later in the admin UI would not be
    trusted until a restart. These properties re-read
    settings.CSRF_TRUSTED_ORIGINS on every request instead.
    """

    @property
    def csrf_trusted_origins_hosts(self):
        return [
            urlsplit(origin).netloc.lstrip("*")
            for origin in django_settings.CSRF_TRUSTED_ORIGINS
        ]

    @property
    def allowed_origins_exact(self):
        return {origin for origin in django_settings.CSRF_TRUSTED_ORIGINS if "*" not in origin}

    @property
    def allowed_origin_subdomains(self):
        allowed_origin_subdomains = defaultdict(list)
        for parsed in (
            urlsplit(origin)
            for origin in django_settings.CSRF_TRUSTED_ORIGINS
            if "*" in origin
        ):
            allowed_origin_subdomains[parsed.scheme].append(parsed.netloc.lstrip("*"))
        return allowed_origin_subdomains


class LockScreenMiddleware:
    """
    Lock screen removed — no-op (kept so imports/settings references stay valid).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """ZAP 2.17.0 passive-scan remediation.

    Emits three headers on every dynamic response:

    * Content-Security-Policy — the app loads no external resources (all JS,
      CSS, icons and fonts are local; every fetch()/XHR is same-origin, and
      the AI chatbot is proxied server-side via /chatbot/message/). The code
      base uses extensive inline <script>, inline event handlers and inline
      <style> blocks, so 'unsafe-inline' is required for script-src/style-src
      until those are externalised.
    * X-Content-Type-Options — also set via SECURE_CONTENT_TYPE_NOSNIFF in
      settings; kept here as defence-in-depth so no response can miss it.
    * Server — wsgiref's runserver adds "Server: WSGIServer/0.2 CPython/x"
      only when the application did not already send a Server header (see
      wsgiref/handlers.py send_headers). Setting an empty value suppresses
      the version disclosure. In production the reverse proxy / WSGI server
      (Apache, nginx, gunicorn) controls this header.
    """

    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers["Server"] = ""
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = self.CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


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

                if request.user.is_superuser or getattr(profile, 'role', '') == 'admin':
                    return self.get_response(request)

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
                    kicked_user = request.user if request.user.is_authenticated else None
                    kick_reason = (
                        "session_terminated" if is_terminated
                        else "signed_in_another_device"
                    )
                    if kicked_user is not None:
                        try:
                            from .models import AuditLog
                            AuditLog.objects.create(
                                admin=kicked_user,
                                action="Logged out",
                                details={
                                    "ip": request.META.get('REMOTE_ADDR', ''),
                                    "method": kick_reason,
                                },
                            )
                        except Exception:
                            pass
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

