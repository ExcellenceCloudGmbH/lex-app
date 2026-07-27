from contextlib import suppress

from django.db import connection
from django.test import TransactionTestCase
from lex.process_admin.utils.model_registration import ModelRegistration
from simple_history.models import registered_models


class DynamicBitemporalModelTestCase(TransactionTestCase):
    """Shared setup/teardown for dynamically-registered bitemporal test models."""

    model_class = None

    def setUp(self):
        super().setUp()
        if self.model_class is None:
            raise ValueError("DynamicBitemporalModelTestCase requires model_class")

        if self.model_class in registered_models:
            del registered_models[self.model_class]

        ModelRegistration()._register_standard_model(self.model_class, [])

        self.HistoryModel = self.model_class.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = set(connection.introspection.table_names())
        with connection.schema_editor() as schema_editor:
            if self.MetaModel._meta.db_table in tables:
                schema_editor.delete_model(self.MetaModel)
            if self.HistoryModel._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel)
            if self.model_class._meta.db_table in tables:
                schema_editor.delete_model(self.model_class)

            schema_editor.create_model(self.model_class)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            with suppress(Exception):
                schema_editor.delete_model(self.MetaModel)
            with suppress(Exception):
                schema_editor.delete_model(self.HistoryModel)
            with suppress(Exception):
                schema_editor.delete_model(self.model_class)

        if self.model_class in registered_models:
            del registered_models[self.model_class]

        super().tearDown()
