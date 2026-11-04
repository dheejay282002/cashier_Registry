import os

from django.contrib import admin
from django.http import Http404
from django.urls import path, include
from django.conf import settings
from django.views.static import serve as django_serve

urlpatterns = [
    path('', include('admin_panel.urls')),
    path('django-admin/', admin.site.urls),
]


def _serve_media(request, path):
    """Serve uploads from MEDIA_ROOT, falling back to the bundled media folder.

    The desktop app keeps MEDIA_ROOT in the writable per-user data directory,
    while the read-only media that ships with the installer lives in the app
    directory (REGISTRY_MEDIA_FALLBACK_ROOT).
    """
    roots = [settings.MEDIA_ROOT]
    fallback = os.environ.get('REGISTRY_MEDIA_FALLBACK_ROOT')
    if fallback:
        roots.append(fallback)

    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        try:
            return django_serve(request, path, document_root=root)
        except Http404:
            continue
    raise Http404


def _serve_static(request, path):
    """Serve the read-only assets bundled with the desktop application."""
    return django_serve(request, path, document_root=settings.BASE_DIR / 'static')


desktop_mode = os.getenv('REGISTRY_DESKTOP_MODE', '').lower() in ('1', 'true', 'yes', 'on')

if settings.DEBUG or desktop_mode:
    urlpatterns += [
        path('media/<path:path>', _serve_media),
    ]

if desktop_mode and not settings.DEBUG:
    urlpatterns += [
        path('static/<path:path>', _serve_static),
    ]
