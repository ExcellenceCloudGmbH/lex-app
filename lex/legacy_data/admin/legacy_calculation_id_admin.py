from lex.legacy_data.admin.read_only_admin import ReadOnlyAdmin

class LegacyCalculationIdAdmin(ReadOnlyAdmin):
    list_display = ('calculation_id', 'context_id', 'calculation_record')
    search_fields = ('calculation_id', 'context_id', 'calculation_record')
