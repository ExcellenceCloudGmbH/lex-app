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