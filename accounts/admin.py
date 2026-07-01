from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'department', 'employee_id', 'is_ldap_user', 'created_at')
    list_filter = ('role', 'is_ldap_user', 'department')
    search_fields = ('user__username', 'user__email', 'employee_id', 'department')
    readonly_fields = ('created_at', 'updated_at')
