from django.contrib import admin
from .models import Profile


# @admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'user__email', 'user__first_name', 'user__last_name')
    list_filter = ('user__is_active', 'user__is_staff')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('uma_permissions',)
    
    def user__email(self, obj):
        return obj.user.email
    user__email.short_description = 'Email'
    
    def user__first_name(self, obj):
        return obj.user.first_name
    user__first_name.short_description = 'First Name'
    
    def user__last_name(self, obj):
        return obj.user.last_name
    user__last_name.short_description = 'Last Name'