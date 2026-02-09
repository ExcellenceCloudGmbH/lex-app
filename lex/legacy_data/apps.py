from django.contrib import admin
from lex.lex_app.apps import LexAppConfig
from process_admin.utils import ModelRegistration


class LegacyDataConfig(LexAppConfig):
    name = 'lex.legacy_data'
    verbose_name = 'Legacy Data (V1 Archive)'

    def register_models(self):
        # We override register_models to provide custom Read-Only Admin for legacy data
        # avoiding the standard ModelRegistration which assumes standard permissions.
        
        from django.db import connection
        from django.core.exceptions import SynchronousOnlyOperation
        
        # Helper to check if a table exists
        def table_exists(table_name):
            try:
                # Try standard synchronous check
                return table_name in connection.introspection.table_names()
            except SynchronousOnlyOperation:
                # If we are in an async context (e.g. ASGI startup), we can't safely 
                # check the DB synchronously. We assume True to allow registration.
                # If the table doesn't exist, accessing the admin page will error, 
                # but startup won't crash. This is better than omitting them entirely.
                return True
            except Exception:
                # Fallback for other DB errors (e.g. not ready)
                return False

        from lex.legacy_data.models import LegacyCalculationLog, LegacyUserChangeLog, LegacyCalculationId
        from lex.legacy_data.admin import LegacyCalculationLogAdmin, LegacyUserChangeLogAdmin, LegacyCalculationIdAdmin
        from lex.process_admin.settings import processAdminSite
        from lex.legacy_data.serializers.legacy_data_serializers import (
            LegacyCalculationLogSerializer, 
            LegacyUserChangeLogSerializer, 
            LegacyCalculationIdSerializer
        )
        from lex.core.mixins.ModelModificationRestriction import AdminReportsModificationRestriction

        # Inject custom read-only serializers onto the models dynamically
        # This avoids circular imports in models.py while ensuring the API uses our restricted serializers.
        LegacyCalculationLog.api_serializers = {"default": LegacyCalculationLogSerializer}
        LegacyUserChangeLog.api_serializers = {"default": LegacyUserChangeLogSerializer}
        LegacyCalculationId.api_serializers = {"default": LegacyCalculationIdSerializer}

        # Inject strict modification restrictions to prevent DELETE operations gracefully (HTTP 403)
        # instead of crashing with HTTP 500 at the model level.
        read_only_restriction = AdminReportsModificationRestriction()
        LegacyCalculationLog.modification_restriction = read_only_restriction
        LegacyUserChangeLog.modification_restriction = read_only_restriction
        LegacyCalculationId.modification_restriction = read_only_restriction

        # Explicitly disable history tracking for these models
        # (This adds them to the ignore list used by ModelRegistration if standard registration runs)
        if not hasattr(self, 'untracked_models'):
             self.untracked_models = []
        self.untracked_models.extend(["legacycalculationlog", "legacyuserchangelog", "legacycalculationid"])

        # Only register the model in Admin if its underlying table actually exists in the DB.
        # This prevents the "V1 Archive" section from appearing on fresh installs 
        # or environments where legacy data was never migrated.

        if table_exists(LegacyCalculationLog._meta.db_table):
            if not admin.site.is_registered(LegacyCalculationLog):
                admin.site.register(LegacyCalculationLog, LegacyCalculationLogAdmin)
            processAdminSite.register([LegacyCalculationLog])
        
        if table_exists(LegacyUserChangeLog._meta.db_table):
            if not admin.site.is_registered(LegacyUserChangeLog):
                admin.site.register(LegacyUserChangeLog, LegacyUserChangeLogAdmin)
            processAdminSite.register([LegacyUserChangeLog])
        
        if table_exists(LegacyCalculationId._meta.db_table):
            if not admin.site.is_registered(LegacyCalculationId):
                admin.site.register(LegacyCalculationId, LegacyCalculationIdAdmin)
            processAdminSite.register([LegacyCalculationId])


        # 3. Call parent just in case there are other models (though we don't expect any)
        # super().register_models()
