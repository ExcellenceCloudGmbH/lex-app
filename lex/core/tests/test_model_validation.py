from django.test import SimpleTestCase
from django.db import models
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.core.models.LexModel import LexModel
from lex.core.models.CalculationModel import CalculationModel


class ModelValidationTest(SimpleTestCase):
    """Tests for ModelRegistration._validate_model_definition."""

    # ── Reserved class names ──────────────────────────────────────────────

    def test_exact_reserved_name_History(self):
        class History(LexModel):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved by the LEX framework"):
            ModelRegistration._validate_model_definition(History)

    def test_exact_reserved_name_MetaHistory(self):
        class MetaHistory(LexModel):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved by the LEX framework"):
            ModelRegistration._validate_model_definition(MetaHistory)

    def test_exact_reserved_name_LexModel(self):
        class LexModel(models.Model):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved by the LEX framework"):
            ModelRegistration._validate_model_definition(LexModel)

    # ── Reserved class name prefixes ──────────────────────────────────────

    def test_prefix_Historical(self):
        class HistoricalQuarter(LexModel):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "must not start with 'Historical'"):
            ModelRegistration._validate_model_definition(HistoricalQuarter)

    def test_prefix_MetaHistorical(self):
        class MetaHistoricalQuarter(LexModel):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "must not start with 'MetaHistorical'"):
            ModelRegistration._validate_model_definition(MetaHistoricalQuarter)

    def test_prefix_Meta(self):
        class MetaSomething(LexModel):
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "must not start with 'Meta'"):
            ModelRegistration._validate_model_definition(MetaSomething)

    # ── Reserved field names (user-declared) ──────────────────────────────

    def test_reserved_field_valid_from(self):
        class BadFieldModel(LexModel):
            valid_from = models.DateTimeField()
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved field(s)"):
            ModelRegistration._validate_model_definition(BadFieldModel)

    def test_reserved_field_sys_to(self):
        class BadSysFieldModel(LexModel):
            sys_to = models.DateTimeField()
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved field(s)"):
            ModelRegistration._validate_model_definition(BadSysFieldModel)

    def test_reserved_field_meta_history_type(self):
        class BadMetaFieldModel(LexModel):
            meta_history_type = models.CharField(max_length=10)
            class Meta:
                app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "reserved field(s)"):
            ModelRegistration._validate_model_definition(BadMetaFieldModel)

    # ── Inherited fields from abstract parents are allowed ────────────────

    def test_inherited_created_by_not_flagged(self):
        """LexModel's own created_by / edited_by must not trigger the check."""
        class InheritedFieldModel(LexModel):
            name = models.CharField(max_length=100)
            class Meta:
                app_label = 'lex_core'

        try:
            ModelRegistration._validate_model_definition(InheritedFieldModel)
        except ValueError:
            self.fail("Inherited LexModel fields should not be flagged.")

    def test_inherited_is_calculated_not_flagged(self):
        """CalculationModel's is_calculated must not trigger the check."""
        class MyCalcModel(CalculationModel):
            value = models.DecimalField(max_digits=10, decimal_places=2)
            class Meta:
                app_label = 'lex_core'

        try:
            ModelRegistration._validate_model_definition(MyCalcModel)
        except ValueError:
            self.fail("Inherited CalculationModel fields should not be flagged.")

    # ── Valid models pass without error ───────────────────────────────────

    def test_valid_model_passes(self):
        class Quarter(LexModel):
            name = models.CharField(max_length=100)
            start_date = models.DateField()
            class Meta:
                app_label = 'lex_core'

        try:
            ModelRegistration._validate_model_definition(Quarter)
        except ValueError:
            self.fail("A valid model should not raise ValueError.")

