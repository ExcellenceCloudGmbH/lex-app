from django.contrib import admin


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

def register_api_key_admin(site=None):
    """Register scoped API keys (``LexAPIKey``) in the Django admin.

    lex disables admin autodiscovery (``CustomAdminConfig``), so nothing would
    register them otherwise. Operators list, revoke and expire keys here; a key
    created in the admin is shown once, like with ``lex create_api_key``.
    Importing ``rest_framework_api_key.admin`` also registers the classic keys.
    """
    from django.contrib import admin as django_admin
    from rest_framework_api_key.admin import APIKeyModelAdmin

    from lex.authentication.models import LexAPIKey

    site = site or django_admin.site
    if site.is_registered(LexAPIKey):
        return

    class LexAPIKeyAdmin(APIKeyModelAdmin):
        list_display = (*APIKeyModelAdmin.list_display, "user")
        list_filter = (*APIKeyModelAdmin.list_filter, "revoked")
        search_fields = (*APIKeyModelAdmin.search_fields, "user__username")
        raw_id_fields = ("user",)

    site.register(LexAPIKey, LexAPIKeyAdmin)
