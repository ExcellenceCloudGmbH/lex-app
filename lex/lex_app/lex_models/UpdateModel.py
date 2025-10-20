from abc import abstractmethod

from django.db import transaction
from django_lifecycle import (
    hook,
    AFTER_UPDATE,
    AFTER_CREATE,
    BEFORE_SAVE,
)
from lex.lex_app.lex_models.CalculationModel import CalculationModel

from lex.lex_app.lex_models.LexModel import LexModel


class UpdateModel(LexModel):
    class Meta:
        abstract = True

    @abstractmethod
    def update(self):
        pass

    # TODO: For the Celery task cases, this hook should be updated

    @hook(BEFORE_SAVE)
    def before_save(self):
        from lex.lex_app.rest_api.signals import update_calculation_status

        # Check if it's a new instance
        if self._state.adding:
            self.is_creation = True
        else:
            self.is_creation = False
        self.is_calculated = CalculationModel.IN_PROGRESS
        self.untrack()
        self.save(skip_hooks=True)
        self.track()
        update_calculation_status(self)




    @hook(AFTER_UPDATE, on_commit=True)
    @hook(AFTER_CREATE, on_commit=True)
    def calculate_hook(self):
        self.untrack()
        from lex.lex_app.rest_api.signals import update_calculation_status

        # update_calculation_status(self)
        try:
            if hasattr(self, "is_atomic") and not self.is_atomic:
                self.update()
                self.is_calculated = CalculationModel.SUCCESS
            else:
                with transaction.atomic():
                    self.update()
                    self.is_calculated = CalculationModel.SUCCESS
        except Exception as e:
            self.is_calculated = CalculationModel.ERROR
            raise e
        finally:
            self.save(skip_hooks=True)
            update_calculation_status(self)


