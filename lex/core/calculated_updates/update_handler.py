from lex.core.mixins.calculated import CalculatedModelMixin
# TODO: DependencyAnalysisMixin was not migrated from old structure
# from lex.process_admin.models.model_process_admin import DependencyAnalysisMixin


def calc_and_save(entry):
    entry.calculate()
    entry.save()


class CalculatedModelUpdateHandler:
    instance = None

    def __init__(self):
        # TODO: Change file to rely on 'model_graph_store.py' instead
        self.model_collection = None
        self.post_save_behaviour = calc_and_save
        CalculatedModelUpdateHandler.instance = self

    def set_model_collection(self, model_collection):
        self.model_collection = model_collection

    @staticmethod
    def set_post_save_behaviour(func):
        CalculatedModelUpdateHandler.instance.post_save_behaviour = func

    @staticmethod
    def reset_post_save_behaviour():
        CalculatedModelUpdateHandler.instance.post_save_behaviour = calc_and_save

    @staticmethod
    def register_save(updated_entry):
        # TODO: Properly handle this case
        # TODO: DependencyAnalysisMixin check was disabled - needs to be reimplemented
        # if not issubclass(type(updated_entry), DependencyAnalysisMixin):
        #     return
        
        # Check if updated_entry has the required method
        if not hasattr(updated_entry, 'get_dependent_entries'):
            return

        # Get all entries in calculated models dependent on 'updated_entry'
        dependent_entries = updated_entry.get_dependent_entries().keys()
        dependent_calculated_entries = list(
            filter(
                lambda entry: issubclass(type(entry), CalculatedModelMixin),
                dependent_entries
            )
        )

        # Update each such entry
        for entry in dependent_calculated_entries:
            CalculatedModelUpdateHandler.instance.post_save_behaviour(entry)
