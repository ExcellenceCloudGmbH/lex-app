from lex.utilities.config.generic_app_config import GenericAppConfig
from lex.lex_app.apps import LexAppConfig
from django.db.migrations.optimizer import MigrationOptimizer

# Monkey patch MigrationOptimizer to skip optimization
# This is necessary because optimization becomes exponentially slow with many models
original_optimize = MigrationOptimizer.optimize

def optimize_skip(self, operations, app_label):
    print(f"Skipping migration optimization for {app_label}")
    return operations

MigrationOptimizer.optimize = optimize_skip


class UtilitiesConfig(LexAppConfig):
    """
    Configuration for the utilities app containing shared utilities, decorators, and helper functions.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.utilities'
    verbose_name = 'Utilities'