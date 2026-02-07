from lex.legacy_data.admin.read_only_admin import ReadOnlyAdmin

class LegacyCalculationLogAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'message_type', 'calculationId', 'method', 'is_notification')
    search_fields = ('message', 'calculationId')
    list_filter = ('message_type', 'is_notification')
