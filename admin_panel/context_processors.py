from django.core.cache import cache
from .models import SystemSetting
from cashier.models import Profile, DEFAULT_ROLE_PERMISSIONS


def system_settings(request):
    settings_obj = cache.get('system_settings_obj')
    if not settings_obj:
        settings_obj = SystemSetting.get_settings()
        cache.set('system_settings_obj', settings_obj, 300)
    auto_lock = getattr(settings_obj, 'auto_lock_seconds', 30)
    auto_logout = getattr(settings_obj, 'auto_logout_seconds', 3600)
    try:
        auto_lock = max(10, min(3600, int(auto_lock)))
    except (TypeError, ValueError):
        auto_lock = 30
    try:
        auto_logout = max(60, min(86400, int(auto_logout)))
    except (TypeError, ValueError):
        auto_logout = 3600

    wallpaper = settings_obj.lockscreen_wallpaper
    role_permissions = None
    if hasattr(request, 'user') and request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.lockscreen_wallpaper:
                wallpaper = profile.lockscreen_wallpaper
            if profile.auto_lock_seconds is not None:
                auto_lock = profile.auto_lock_seconds
            if profile.auto_logout_seconds is not None:
                auto_logout = profile.auto_logout_seconds
            role = 'admin' if request.user.is_superuser else profile.role
            role_permissions = DEFAULT_ROLE_PERMISSIONS.get(role)
        except (Profile.DoesNotExist, AttributeError):
            role_permissions = DEFAULT_ROLE_PERMISSIONS.get('admin')

    return {
        "system_name": settings_obj.system_name,
        "system_logo": settings_obj.system_logo,
        "system_logo_hover": settings_obj.system_logo_hover,
        "entity_name": settings_obj.entity_name,
        "lockscreen_wallpaper": wallpaper,
        "login_background": settings_obj.login_background,
        "auto_lock_seconds": auto_lock,
        "auto_logout_seconds": auto_logout,
        "role_permissions": role_permissions,
    }
