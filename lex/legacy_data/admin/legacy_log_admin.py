from lex.legacy_data.admin.read_only_admin import ReadOnlyAdmin


class LegacyLogAdmin(ReadOnlyAdmin):
    list_display = ("id", "group", "logfile", "input_validation")
    search_fields = ("id", "group")
