# Core mixins
# Import mixins explicitly when needed to avoid circular dependencies
# during Django initialization

__all__ = [
    'CalculatedModelMixin', 
    'ModelCombinationGenerator', 
    'ModelClusterManager', 
    'calc_and_save_sync',
    'AdminReportsModificationRestriction', 
    'ExampleModelModificationRestriction'
]

from lex.core.mixins.CalculatedModelMixin import ModelCombinationGenerator, calc_and_save_sync, ModelClusterManager
from lex.core.mixins.ModelModificationRestriction import AdminReportsModificationRestriction, \
    ExampleModelModificationRestriction
