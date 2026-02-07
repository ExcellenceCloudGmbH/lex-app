from lex.legacy_data.admin.read_only_admin import ReadOnlyAdmin

class LegacyUserChangeLogAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'user_name', 'message', 'calculationId')
    search_fields = ('user_name', 'message', 'calculationId')
