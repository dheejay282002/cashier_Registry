"""Drop-in ``runserver`` that applies security headers to static responses.

In DEBUG, ``django.contrib.staticfiles`` serves STATIC_URL paths through
``StaticFilesHandler``, which short-circuits before Django middleware runs,
so the dev server leaked ``Server: WSGIServer/0.2 CPython/...`` and omitted
``X-Content-Type-Options``/CSP on ``/static/*`` (ZAP: "Server Leaks Version
Info", "X-Content-Type-Options Header Missing" on /static/fonts/inter-500.css).

This command subclasses staticfiles' runserver and wraps the outermost WSGI
layer so every response - static or dynamic - carries the same three headers
as ``SecurityHeadersMiddleware``.  It takes precedence because ``admin_panel``
is listed before ``django.contrib.staticfiles`` in INSTALLED_APPS
(``get_commands`` keeps the first-listed app for a command name).
"""

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)

from admin_panel.middleware import SecurityHeadersMiddleware


class StaticSecurityHeaders:
    """Outermost WSGI wrapper: force Server / nosniff / CSP on every response."""

    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        def apply_headers(status, headers, exc_info=None):
            rewritten = []
            seen = set()
            for name, value in headers:
                lower = name.lower()
                seen.add(lower)
                if lower == "server":
                    rewritten.append((name, ""))
                elif lower == "x-content-type-options":
                    rewritten.append((name, "nosniff"))
                else:
                    rewritten.append((name, value))
            if "content-security-policy" not in seen:
                rewritten.append(
                    ("Content-Security-Policy", SecurityHeadersMiddleware.CONTENT_SECURITY_POLICY)
                )
            if "server" not in seen:
                rewritten.append(("Server", ""))
            if "x-content-type-options" not in seen:
                rewritten.append(("X-Content-Type-Options", "nosniff"))
            return start_response(status, rewritten, exc_info)

        return self.application(environ, apply_headers)


class Command(StaticfilesRunserverCommand):
    def get_handler(self, *args, **options):
        return StaticSecurityHeaders(super().get_handler(*args, **options))
