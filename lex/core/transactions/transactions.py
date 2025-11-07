from django.db import transaction

from lex.core.calculated_updates.objects_to_recalculate_store import ObjectsToRecalculateStore
from lex.core.calculated_updates.update_handler import CalculatedModelUpdateHandler


# Executes 'func' as an atomic transaction while only updating the entries dependent on the
# altered ones on committing the transaction
def as_transaction(func):
    def entire_transaction():
        with transaction.atomic():
            CalculatedModelUpdateHandler.set_post_save_behaviour(
                lambda entry: ObjectsToRecalculateStore.insert(entry)
            )
            transaction.on_commit(ObjectsToRecalculateStore.do_recalculations)
            func()
            CalculatedModelUpdateHandler.reset_post_save_behaviour()

    return entire_transaction
