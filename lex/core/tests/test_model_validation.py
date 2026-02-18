from django.test import SimpleTestCase
from django.db import models
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.core.models.LexModel import LexModel

class ModelValidationTest(SimpleTestCase):
    def test_validation_logic(self):
        # 1. Test Restricted Class Names
        
        # Exact match 'History'
        class History(LexModel):
            class Meta: app_label = 'lex_core'
            
        with self.assertRaisesMessage(ValueError, "reserved by the framework"):
            ModelRegistration._validate_model_definition(History)

        # Starts with 'Historical'
        class HistoricalTest(LexModel):
            class Meta: app_label = 'lex_core'
            
        with self.assertRaisesMessage(ValueError, "cannot start with 'Historical'"):
            ModelRegistration._validate_model_definition(HistoricalTest)

        # 2. Test Restricted Fields
        
        # 'valid_from'
        class InvalidFieldModel(LexModel):
            valid_from = models.DateTimeField()
            class Meta: app_label = 'lex_core'
            
        with self.assertRaisesMessage(ValueError, "defines reserved fields"):
            ModelRegistration._validate_model_definition(InvalidFieldModel)

        # 'sys_to'
        class InvalidSysFieldModel(LexModel):
            sys_to = models.DateTimeField()
            class Meta: app_label = 'lex_core'

        with self.assertRaisesMessage(ValueError, "defines reserved fields"):
            ModelRegistration._validate_model_definition(InvalidSysFieldModel)

        # 3. Valid Model should pass
        class ValidModel(LexModel):
            name = models.CharField(max_length=100)
            class Meta: app_label = 'lex_core'

        try:
            ModelRegistration._validate_model_definition(ValidModel)
        except ValueError:
            self.fail("ValidModel raised ValueError unexpectedly!")
