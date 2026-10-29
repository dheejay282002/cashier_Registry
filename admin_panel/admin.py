from django.contrib import admin
from .models import SystemSetting, AllowedHost


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
	list_display = ("system_name",)


@admin.register(AllowedHost)
class AllowedHostAdmin(admin.ModelAdmin):
	list_display = ("host", "created_at")
	search_fields = ("host",)
