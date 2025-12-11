"""
Calculated Model Framework for Django with Parallel Processing Support

This module provides a comprehensive framework for creating and processing Django models
that support combinatorial field expansion and parallel processing using Celery workers.
The framework is designed for scenarios where you need to generate many model variations
based on field combinations and process them efficiently.

Core Components:
b
1. **CalculatedModelMixin**: The main mixin class that provides calculated model functionality
2. **ModelCombinationGenerator**: Handles combinatorial expansion of defining fields
3. **ModelClusterManager**: Manages clustering of models for parallel processing
4. **CeleryTaskDispatcher**: Handles Celery task dispatch and failure management
5. **Custom Exception Classes**: Provide detailed error context for debugging

Key Concepts:

- **defining_fields**: Fields that create unique model combinations through combinatorial expansion
- **parallelizable_fields**: Fields used to group models for efficient parallel processing
- **Model Creation Workflow**: Four-step process from combination generation to processing
- **Error Handling**: Comprehensive exception hierarchy with detailed context information
- **Fallback Processing**: Automatic fallback to synchronous processing when Celery fails

Architecture Overview:

The framework follows a clear separation of concerns:

1. **Combination Generation** (ModelCombinationGenerator):
   - Expands defining_fields into all possible combinations
   - Handles field overrides from method parameters
   - Creates deep copies of models for each combination
   - Provides detailed error messages for field expansion failures

2. **Clustering Management** (ModelClusterManager):
   - Organizes models into hierarchical clusters based on parallelizable_fields
   - Creates nested dictionary structures for efficient grouping
   - Flattens clusters into processing groups for Celery dispatch
   - Handles edge cases like empty clusters and invalid field values

3. **Task Dispatch** (CeleryTaskDispatcher):
   - Manages Celery task creation and dispatch
   - Monitors task completion and handles failures
   - Provides automatic fallback to synchronous processing
   - Includes comprehensive error handling and logging

4. **Main Orchestration** (CalculatedModelMixin):
   - Coordinates the entire model creation and processing workflow
   - Provides a clean, simple interface for model creation
   - Maintains backward compatibility with existing code
   - Includes detailed logging and error reporting

Performance Characteristics:

- **Small Scale** (< 100 models): Efficient synchronous processing
- **Medium Scale** (100-1000 models): Benefits from parallelization
- **Large Scale** (> 1000 models): Requires parallel processing for reasonable performance
- **Memory Usage**: Optimized for large model combinations through streaming processing
- **Error Recovery**: Robust error handling with multiple fallback strategies

Usage Patterns:

Basic Usage:
```python
class MyModel(CalculatedModelMixin):
    defining_fields = ['region', 'product']
    parallelizable_fields = ['region']
    
    def get_selected_key_list(self, key):
        # Return possible values for each field
        pass
    
    def calculate(self):
        # Perform calculations
        pass

# Create all combinations
MyModel.create()

# Create with overrides
MyModel.create(region=['US', 'EU'])
```

Error Handling:
```python
try:
    MyModel.create()
except ModelCombinationError as e:
    # Handle field expansion errors
    logger.error(f"Field expansion failed: {e}")
except CeleryDispatchError as e:
    # Handle Celery failures (automatic fallback occurs)
    logger.warning(f"Celery dispatch failed, using fallback: {e}")
```

Dependencies:

- Django: Core model framework and ORM
- Celery: Distributed task processing (optional, with fallback)
- Python typing: Type hints for better code documentation
- logging: Comprehensive logging throughout the framework

Thread Safety:

The framework is designed to be thread-safe:
- Model combination generation uses deep copying to avoid shared state
- Celery tasks are isolated and don't share mutable state
- Error handling is stateless and doesn't modify global variables
- Logging is thread-safe through Python's logging module

See Also:

- calculated_model_usage_examples.py: Comprehensive usage examples and best practices
- ModelRegistration.py: Enhanced model registration with better organization
- model_collection.py: Improved model collection management
- model_container.py: Enhanced model container with better documentation

Version History:

- v1.0: Original implementation with basic calculated model support
- v2.0: Refactored implementation with improved architecture and error handling
- v2.1: Added comprehensive documentation and type hints
- v2.2: Enhanced parallel processing and Celery integration
"""

import itertools
import logging

import os
from abc import abstractmethod
from copy import deepcopy
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from django.db import transaction
from django.db.models import Model, UniqueConstraint
from django.db.models.base import ModelBase

from lex.lex_app import settings
from lex.api.utils import operation_context
from lex.core.exceptions import *
from lex.core.models.base import LexModel

if TYPE_CHECKING:
    pass  # CalculatedModelMixin is defined in this file

logger = logging.getLogger(__name__)

def _flatten(list_2d):
    return list(itertools.chain.from_iterable(list_2d))
from django.db import connection

def get_transaction_depth():
    """Returns the current savepoint/transaction nesting level"""
    return len(connection.savepoint_ids) if hasattr(connection, 'savepoint_ids') else 0

def assert_in_transaction():
    """Enforce transaction context requirement"""
    assert connection.in_atomic_block, "This function must run inside a transaction"


class ModelCombinationGenerator:
    """
    Handles combinatorial expansion of models based on defining fields.
    
    This class provides static methods to generate all possible combinations of models
    by expanding each defining field with its possible values. The expansion process
    creates new model instances for each combination of field values, allowing for
    comprehensive model generation based on the defining_fields configuration.
    """
    
    @staticmethod
    def generate_model_combinations(
        base_model: 'CalculatedModelMixin',
        defining_fields: List[str],
        field_overrides: Dict[str, Any]
    ) -> List['CalculatedModelMixin']:
        """
        Generate all combinations of models based on defining fields.
        
        This method takes a base model and expands it into multiple model instances
        by applying all possible combinations of values for the defining fields.
        Fields are processed in a specific order: fields with overrides first,
        then remaining fields.
        
        Args:
            base_model: The base model instance to expand
            defining_fields: List of field names that define unique combinations
            field_overrides: Dictionary of field values to override from kwargs
            
        Returns:
            List of model instances with all field combinations applied
            
        Raises:
            ModelCombinationError: If field expansion fails or invalid field configuration
            
        Example:
            If defining_fields = ['region', 'product'] and:
            - region has values ['US', 'EU'] 
            - product has values ['A', 'B']
            Then 4 models will be generated with combinations:
            (US, A), (US, B), (EU, A), (EU, B)
        """
        if not base_model:
            raise ModelCombinationError(
                "Base model cannot be None",
                model_class=base_model.__class__.__name__ if base_model else "Unknown"
            )
        
        if not defining_fields:
            logger.debug(f"No defining fields provided for {base_model.__class__.__name__}, returning single model")
            return [base_model]
        
        try:
            models = [base_model]
            
            # Process fields in order: overridden fields first, then others
            # This ensures that field overrides take precedence and are processed first,
            # which can help with performance by reducing the search space early
            ordered_defining_fields = sorted(
                defining_fields, 
                key=lambda x: 0 if x in field_overrides.keys() else 1
            )
            
            logger.debug(
                f"Processing {len(ordered_defining_fields)} defining fields for {base_model.__class__.__name__}: "
                f"{ordered_defining_fields}"
            )
            
            # Iteratively expand the model list for each defining field
            # Each iteration multiplies the number of models by the number of values for that field
            # For example: 1 model → 3 regions → 3 models → 2 products → 6 models
            for field_name in ordered_defining_fields:
                try:
                    # Handle dotted field names (e.g., 'parent.child') by taking the last part
                    # This supports complex field references while maintaining backward compatibility
                    processed_field_name = field_name.__str__().split('.')[-1]
                    
                    logger.debug(f"Expanding field '{processed_field_name}' (from '{field_name}')")
                    
                    # Expand the current model list by creating copies for each field value
                    # This is the core combinatorial expansion logic
                    models = ModelCombinationGenerator._expand_models_for_field(
                        models, processed_field_name, field_overrides
                    )
                    
                    logger.debug(f"After expanding '{processed_field_name}': {len(models)} model combinations")
                    
                except Exception as field_error:
                    raise ModelCombinationError(
                        f"Failed to expand defining field '{field_name}': {str(field_error)}",
                        field_name=field_name,
                        model_class=base_model.__class__.__name__,
                        current_model_count=len(models),
                        field_overrides=list(field_overrides.keys())
                    ) from field_error
            
            if not models:
                raise ModelCombinationError(
                    "Model combination generation resulted in empty model list",
                    model_class=base_model.__class__.__name__,
                    defining_fields=defining_fields,
                    field_overrides=list(field_overrides.keys())
                )
            
            logger.info(
                f"Successfully generated {len(models)} model combinations for {base_model.__class__.__name__} "
                f"from {len(defining_fields)} defining fields"
            )
            
            return models
            
        except ModelCombinationError:
            # Re-raise ModelCombinationError as-is
            raise
        except Exception as e:
            raise ModelCombinationError(
                f"Unexpected error during model combination generation: {str(e)}",
                model_class=base_model.__class__.__name__,
                defining_fields=defining_fields,
                field_overrides=list(field_overrides.keys())
            ) from e
    
    @staticmethod
    def _expand_models_for_field(
        models: List['CalculatedModelMixin'],
        field_name: str,
        field_overrides: Dict[str, Any]
    ) -> List['CalculatedModelMixin']:
        """
        Expand model list for a specific defining field.
        
        For each model in the input list, this method creates multiple copies
        based on the possible values for the specified field. The field values
        are obtained either from overrides or from the model's get_selected_key_list method.
        
        Args:
            models: List of models to expand
            field_name: Name of the field to expand
            field_overrides: Dictionary of field values to override
            
        Returns:
            Expanded list of models with field values applied
            
        Raises:
            ModelCombinationError: If field expansion fails or field values are invalid
        """
        if not models:
            raise ModelCombinationError(
                f"Cannot expand field '{field_name}' on empty model list",
                field_name=field_name
            )
        
        expanded_models = []
        
        # Process each existing model to create expanded versions
        # This is where the actual combinatorial expansion happens
        for model_index, model in enumerate(models):
            try:
                # Get the possible values for this field from overrides or the model itself
                field_values = ModelCombinationGenerator._get_field_values(
                    model, field_name, field_overrides
                )
                
                if not field_values:
                    logger.warning(
                        f"Field '{field_name}' has no values for model {model_index + 1}/{len(models)} "
                        f"of type {model.__class__.__name__}, skipping expansion"
                    )
                    # If no field values, keep the original model unchanged
                    # This prevents the model from being lost in the expansion process
                    expanded_models.append([model])
                    continue
                
                if not isinstance(field_values, (list, tuple)):
                    raise ModelCombinationError(
                        f"Field values must be a list or tuple, got {type(field_values).__name__}",
                        field_name=field_name,
                        model_class=model.__class__.__name__,
                        field_values_type=type(field_values).__name__
                    )
                
                # Create deep copies of the model for each possible field value
                # Deep copy is essential to ensure each model instance is independent
                # and modifications to one don't affect others
                try:
                    model_copies = [deepcopy(model) for _ in range(len(field_values))]
                except Exception as copy_error:
                    raise ModelCombinationError(
                        f"Failed to create model copies for field '{field_name}': {str(copy_error)}",
                        field_name=field_name,
                        model_class=model.__class__.__name__,
                        field_values_count=len(field_values)
                    ) from copy_error
                
                # Assign each field value to its corresponding model copy
                # This creates the actual field combinations: one model per field value
                for copy_index, (model_copy, field_value) in enumerate(zip(model_copies, field_values)):
                    try:
                        setattr(model_copy, field_name, field_value)
                    except Exception as setattr_error:
                        raise ModelCombinationError(
                            f"Failed to set field '{field_name}' to value '{field_value}' on model copy {copy_index + 1}: {str(setattr_error)}",
                            field_name=field_name,
                            model_class=model.__class__.__name__,
                            field_value=field_value,
                            copy_index=copy_index
                        ) from setattr_error
                
                # Add this group of model copies to the expanded models list
                # Each group represents all variations of the current model for this field
                expanded_models.append(model_copies)
                
            except ModelCombinationError:
                # Re-raise ModelCombinationError as-is
                raise
            except Exception as model_error:
                raise ModelCombinationError(
                    f"Unexpected error expanding model {model_index + 1}/{len(models)} for field '{field_name}': {str(model_error)}",
                    field_name=field_name,
                    model_class=model.__class__.__name__,
                    model_index=model_index
                ) from model_error
        
        try:
            result = _flatten(expanded_models)
            logger.debug(f"Successfully expanded {len(models)} models to {len(result)} models for field '{field_name}'")
            return result
        except Exception as flatten_error:
            raise ModelCombinationError(
                f"Failed to flatten expanded models for field '{field_name}': {str(flatten_error)}",
                field_name=field_name,
                original_model_count=len(models),
                expanded_groups_count=len(expanded_models)
            ) from flatten_error
    
    @staticmethod
    def _get_field_values(
        model: 'CalculatedModelMixin',
        field_name: str,
        field_overrides: Dict[str, Any]
    ) -> List[Any]:
        """
        Get values for a field from overrides or model's get_selected_key_list.
        
        This method first checks if the field has an override value in field_overrides.
        If not, it calls the model's get_selected_key_list method to get the possible
        values for the field.
        
        Args:
            model: The model instance to get field values from
            field_name: Name of the field to get values for
            field_overrides: Dictionary of field values to override
            
        Returns:
            List of possible values for the field
            
        Raises:
            ModelCombinationError: If field values cannot be retrieved or are invalid
        """
        try:
            if field_name in field_overrides:
                field_values = field_overrides[field_name]
                logger.debug(f"Using override values for field '{field_name}': {field_values}")
                
                # Validate override values
                if field_values is None:
                    raise ModelCombinationError(
                        f"Override value for field '{field_name}' is None",
                        field_name=field_name,
                        model_class=model.__class__.__name__
                    )
                
                # Ensure field_values is a list
                if not isinstance(field_values, (list, tuple)):
                    field_values = [field_values]
                
                return list(field_values)
            else:
                # Get values from model's get_selected_key_list method
                if not hasattr(model, 'get_selected_key_list'):
                    raise ModelCombinationError(
                        f"Model {model.__class__.__name__} does not have 'get_selected_key_list' method required for field '{field_name}'",
                        field_name=field_name,
                        model_class=model.__class__.__name__
                    )
                
                try:
                    field_values = model.get_selected_key_list(field_name)
                    logger.debug(f"Retrieved values from get_selected_key_list for field '{field_name}': {field_values}")
                    
                    if field_values is None:
                        raise ModelCombinationError(
                            f"get_selected_key_list returned None for field '{field_name}'",
                            field_name=field_name,
                            model_class=model.__class__.__name__
                        )
                    
                    # Ensure field_values is a list
                    if not isinstance(field_values, (list, tuple)):
                        field_values = [field_values]
                    
                    return list(field_values)
                    
                except Exception as get_values_error:
                    raise ModelCombinationError(
                        f"get_selected_key_list failed for field '{field_name}': {str(get_values_error)}",
                        field_name=field_name,
                        model_class=model.__class__.__name__
                    ) from get_values_error
                    
        except ModelCombinationError:
            # Re-raise ModelCombinationError as-is
            raise
        except Exception as e:
            raise ModelCombinationError(
                f"Unexpected error getting field values for '{field_name}': {str(e)}",
                field_name=field_name,
                model_class=model.__class__.__name__
            ) from e


class ModelClusterManager:
    """
    Manages clustering of models based on parallelizable fields for Celery dispatch.
    
    This class provides functionality to organize models into hierarchical clusters
    based on parallelizable fields, which allows for efficient parallel processing
    in Celery workers. The clustering creates groups of models that can be processed
    independently, optimizing resource utilization and processing time.
    """
    
    @staticmethod
    def create_clusters(
        models: List['CalculatedModelMixin'],
        parallelizable_fields: List[str]
    ) -> Dict[Any, Any]:
        """
        Create hierarchical clusters based on parallelizable fields.
        
        This method organizes models into a nested dictionary structure where
        each level corresponds to a parallelizable field. Models with the same
        values for parallelizable fields are grouped together, enabling efficient
        parallel processing.
        
        Args:
            models: List of models to cluster
            parallelizable_fields: Fields to use for clustering hierarchy
            
        Returns:
            Nested dictionary representing the cluster hierarchy
            
        Raises:
            ModelClusteringError: If clustering fails or invalid field configuration
            
        Example:
            If parallelizable_fields = ['region', 'category'] and models have:
            - Model1: region='US', category='A'
            - Model2: region='US', category='B' 
            - Model3: region='EU', category='A'
            
            Returns: {
                'US': {'A': [Model1], 'B': [Model2]},
                'EU': {'A': [Model3]}
            }
        """
        if not models:
            logger.warning("No models provided for clustering, returning empty cluster")
            return {}
        
        try:
            if not parallelizable_fields:
                # If no parallelizable fields, return all models in a single group
                logger.debug(f"No parallelizable fields specified, creating single cluster with {len(models)} models")
                return {None: models}
            
            logger.debug(
                f"Creating clusters for {len(models)} models using {len(parallelizable_fields)} "
                f"parallelizable fields: {parallelizable_fields}"
            )
            
            # Validate that all models have the required parallelizable fields
            for field_name in parallelizable_fields:
                for model_index, model in enumerate(models):
                    if not hasattr(model, field_name):
                        raise ModelClusteringError(
                            f"Model {model_index + 1} of type {model.__class__.__name__} does not have parallelizable field '{field_name}'",
                            parallelizable_fields=parallelizable_fields,
                            model_count=len(models),
                            missing_field=field_name,
                            model_class=model.__class__.__name__
                        )
            
            cluster_result = ModelClusterManager._build_cluster_hierarchy(models, parallelizable_fields)
            
            # Validate clustering result
            if not isinstance(cluster_result, dict):
                raise ModelClusteringError(
                    f"Cluster hierarchy building returned invalid type {type(cluster_result).__name__}, expected dict",
                    parallelizable_fields=parallelizable_fields,
                    model_count=len(models)
                )
            
            logger.info(f"Successfully created clusters for {len(models)} models using parallelizable fields: {parallelizable_fields}")
            return cluster_result
            
        except ModelClusteringError:
            # Re-raise ModelClusteringError as-is
            raise
        except Exception as e:
            raise ModelClusteringError(
                f"Unexpected error during clustering: {str(e)}",
                parallelizable_fields=parallelizable_fields,
                model_count=len(models)
            ) from e

    @staticmethod
    def flatten_clusters_to_groups(cluster_dict: Dict[Any, Any]) -> List[List['CalculatedModelMixin']]:
        """
        Convert nested cluster dictionary to flat list of model groups.
        
        This method recursively traverses the nested cluster dictionary and
        extracts all leaf nodes (which contain lists of models) into a flat
        list of groups. Each group can then be processed independently.
        
        Args:
            cluster_dict: Nested dictionary from create_clusters()
            
        Returns:
            List of model groups, where each group is a list of models
            
        Raises:
            ModelClusteringError: If cluster flattening fails or invalid cluster structure
            
        Example:
            Input: {'US': {'A': [Model1], 'B': [Model2]}, 'EU': {'A': [Model3]}}
            Output: [[Model1], [Model2], [Model3]]
        """
        if not cluster_dict:
            logger.debug("Empty cluster dictionary provided, returning empty groups list")
            return []
        
        if not isinstance(cluster_dict, dict):
            raise ModelClusteringError(
                f"Cluster dictionary must be a dict, got {type(cluster_dict).__name__}",
                cluster_type=type(cluster_dict).__name__
            )
        
        try:
            groups = []
            
            def _add_to_group(local_cluster: Dict[Any, Any], groups_list: List[List['CalculatedModelMixin']]) -> List[List['CalculatedModelMixin']]:
                """
                Recursively extract model groups from nested cluster dictionary.
                
                Args:
                    local_cluster: Current level of the cluster dictionary
                    groups_list: Accumulator for model groups
                    
                Returns:
                    Updated groups list with extracted model groups
                    
                Raises:
                    ModelClusteringError: If invalid cluster structure is encountered
                """
                if not isinstance(local_cluster, dict):
                    raise ModelClusteringError(
                        f"Expected dict at cluster level, got {type(local_cluster).__name__}",
                        cluster_level_type=type(local_cluster).__name__
                    )
                
                for key, value in local_cluster.items():
                    try:
                        if isinstance(value, dict):
                            # Recurse into nested dictionary
                            groups_list = _add_to_group(value, groups_list)
                        elif isinstance(value, (list, tuple)):
                            # Leaf node contains list of models
                            if value:  # Only add non-empty groups
                                groups_list.append(list(value))
                            else:
                                logger.warning(f"Found empty group at cluster key '{key}', skipping")
                        else:
                            raise ModelClusteringError(
                                f"Invalid cluster value type at key '{key}': expected dict or list, got {type(value).__name__}",
                                cluster_key=key,
                                value_type=type(value).__name__
                            )
                    except Exception as key_error:
                        raise ModelClusteringError(
                            f"Error processing cluster key '{key}': {str(key_error)}",
                            cluster_key=key
                        ) from key_error
                
                return groups_list
            
            result_groups = _add_to_group(cluster_dict, groups)
            
            # Validate result
            if not isinstance(result_groups, list):
                raise ModelClusteringError(
                    f"Cluster flattening returned invalid type {type(result_groups).__name__}, expected list"
                )
            
            # Validate each group
            for group_index, group in enumerate(result_groups):
                if not isinstance(group, (list, tuple)):
                    raise ModelClusteringError(
                        f"Group {group_index + 1} is not a list or tuple: {type(group).__name__}",
                        group_index=group_index,
                        group_type=type(group).__name__
                    )
            
            logger.debug(f"Successfully flattened clusters to {len(result_groups)} groups")
            return result_groups
            
        except ModelClusteringError:
            # Re-raise ModelClusteringError as-is
            raise
        except Exception as e:
            raise ModelClusteringError(
                f"Unexpected error during cluster flattening: {str(e)}"
            ) from e
    
    @staticmethod
    def _build_cluster_hierarchy(
        models: List['CalculatedModelMixin'],
        parallelizable_fields: List[str]
    ) -> Dict[Any, Any]:
        """
        Build the nested cluster dictionary structure.
        
        This method creates a hierarchical clustering structure by iterating
        through parallelizable fields and organizing models based on their
        field values. The last field in the list creates the final grouping
        of actual model instances.
        
        Args:
            models: List of models to organize into clusters
            parallelizable_fields: Fields defining the clustering hierarchy
            
        Returns:
            Nested dictionary with models organized by field values
            
        Raises:
            ModelClusteringError: If hierarchy building fails or invalid field values
        """
        if not models:
            return {}
        
        if not parallelizable_fields:
            raise ModelClusteringError(
                "Cannot build cluster hierarchy without parallelizable fields",
                model_count=len(models)
            )
        
        try:
            cluster_dict = {}
            
            # Build the hierarchical cluster structure by processing each model
            # The clustering creates a nested dictionary where each level corresponds to a parallelizable field
            for model_index, model in enumerate(models):
                try:
                    # Start at the root of the cluster dictionary for this model
                    local_dict = cluster_dict
                    
                    # Navigate through all but the last parallelizable field to build the hierarchy
                    # Each field creates a new level in the nested dictionary structure
                    # For example, with fields ['region', 'scenario']:
                    # - First iteration creates: {'US': {}, 'EU': {}}
                    # - Second iteration creates: {'US': {'opt': [], 'pess': []}, 'EU': {...}}
                    for field_index, parallel_field in enumerate(parallelizable_fields[:-1]):
                        try:
                            # Get the field value for this model to determine which branch to follow
                            field_value = getattr(model, parallel_field, None)
                            
                            if field_value is None:
                                logger.warning(
                                    f"Model {model_index + 1} has None value for parallelizable field '{parallel_field}', "
                                    f"using 'None' as cluster key"
                                )
                            
                            # Navigate to or create the appropriate branch in the hierarchy
                            if field_value in local_dict:
                                # Branch already exists, navigate to it
                                local_dict = local_dict[field_value]
                            else:
                                # Create new branch and navigate to it
                                local_dict[field_value] = {}
                                local_dict = local_dict[field_value]
                                
                        except Exception as field_error:
                            raise ModelClusteringError(
                                f"Error accessing parallelizable field '{parallel_field}' on model {model_index + 1}: {str(field_error)}",
                                parallelizable_fields=parallelizable_fields,
                                model_count=len(models),
                                field_name=parallel_field,
                                model_index=model_index,
                                model_class=model.__class__.__name__
                            ) from field_error
                    
                    # Handle the last parallelizable field (creates the actual model groups)
                    # The last field level contains lists of models instead of nested dictionaries
                    # This is where models are actually grouped together for processing
                    if parallelizable_fields:
                        last_field = parallelizable_fields[-1]
                        try:
                            # Get the value for the final clustering field
                            last_field_value = getattr(model, last_field, None)
                            
                            if last_field_value is None:
                                logger.warning(
                                    f"Model {model_index + 1} has None value for last parallelizable field '{last_field}', "
                                    f"using 'None' as cluster key"
                                )
                            
                            # Add the model to the appropriate group
                            # Models with the same last_field_value will be processed together
                            if last_field_value in local_dict:
                                # Group already exists, add model to it
                                local_dict[last_field_value].append(model)
                            else:
                                # Create new group with this model as the first member
                                local_dict[last_field_value] = [model]
                                
                        except Exception as last_field_error:
                            raise ModelClusteringError(
                                f"Error accessing last parallelizable field '{last_field}' on model {model_index + 1}: {str(last_field_error)}",
                                parallelizable_fields=parallelizable_fields,
                                model_count=len(models),
                                field_name=last_field,
                                model_index=model_index,
                                model_class=model.__class__.__name__
                            ) from last_field_error
                    
                except Exception as model_error:
                    raise ModelClusteringError(
                        f"Error processing model {model_index + 1} during hierarchy building: {str(model_error)}",
                        parallelizable_fields=parallelizable_fields,
                        model_count=len(models),
                        model_index=model_index,
                        model_class=model.__class__.__name__
                    ) from model_error
            
            # Validate the resulting cluster dictionary
            if not cluster_dict:
                logger.warning("Cluster hierarchy building resulted in empty dictionary")
            
            return cluster_dict
            
        except ModelClusteringError:
            # Re-raise ModelClusteringError as-is
            raise
        except Exception as e:
            raise ModelClusteringError(
                f"Unexpected error building cluster hierarchy: {str(e)}",
                parallelizable_fields=parallelizable_fields,
                model_count=len(models)
            ) from e


def calc_and_save_sync(models, *args):
    """
    Synchronous version of calc_and_save for fallback scenarios.
    
    This function processes models synchronously when Celery is not available
    or when Celery tasks fail. It includes enhanced error handling and logging
    to provide better visibility into synchronous processing operations.
    
    Args:
        models: List of models to process
        *args: Arguments to pass to model calculate() methods
        
    Raises:
        CalculatedModelError: If synchronous processing fails
    """
    if not models:
        logger.debug("No models provided for synchronous processing")
        return
    
    if not isinstance(models, (list, tuple)):
        raise CalculatedModelError(
            f"Models must be a list or tuple for synchronous processing, got {type(models).__name__}",
            sync_models_type=type(models).__name__
        )
    
    model_count = len(models)
    logger.info(f"Starting synchronous processing of {model_count} models")
    
    processed_count = 0
    error_count = 0
    errors = []
    
    for i, model in enumerate(models):
        try:
            if model is None:
                logger.warning(f"Model {i + 1}/{model_count} is None, skipping")
                continue
            
            logger.debug(f"Processing model {i + 1}/{model_count} of type {model.__class__.__name__}")
            
            # Calculate the model
            try:
                model.save()
                model.lex_func()(*args)
                logger.debug(f"Calculation completed for model {i + 1}")
            except Exception as calc_error:
                raise CalculatedModelError(
                    f"Calculation failed for model {i + 1}: {str(calc_error)}",
                    model_class=model.__class__.__name__,
                    model_index=i,
                    total_models=model_count
                ) from calc_error
            
            # Save the model
            try:
                model.save()
                processed_count += 1
                logger.debug(f"Successfully saved model {i + 1}")
                
            except Exception as save_error:
                logger.warning(f"Save failed for model {i + 1}, attempting duplicate handling: {save_error}")
                
                try:
                    # Handle duplicate models with same defining fields
                    resolved_model = model.delete_models_with_same_defining_fields()

                    if resolved_model != model:
                        # Existing model found, use its PK
                        model.pk = resolved_model.pk
                        logger.info(f"Using existing model with PK {resolved_model.pk}")

                    # Single save attempt with the resolved model
                    model.save()

                    processed_count += 1
                    logger.info(f"Successfully saved model {i + 1} after duplicate handling")
                    
                except Exception as duplicate_error:
                    error_count += 1
                    error_msg = f"Model {i + 1} save failed even after duplicate handling: {str(duplicate_error)}"
                    errors.append(error_msg)
                    logger.error(error_msg)
                    # Continue processing other models rather than failing completely
            
        except CalculatedModelError as calc_model_error:
            error_count += 1
            error_msg = f"Model {i + 1}: {str(calc_model_error)}"
            errors.append(error_msg)
            logger.error(error_msg)
            # Continue processing other models
            
        except Exception as unexpected_error:
            error_count += 1
            error_msg = f"Unexpected error processing model {i + 1}: {str(unexpected_error)}"
            errors.append(error_msg)
            logger.error(error_msg)
            # Continue processing other models
    
    # Log final results
    if error_count == 0:
        logger.info(f"Synchronous processing completed successfully: {processed_count}/{model_count} models processed")
    elif processed_count > 0:
        logger.warning(
            f"Synchronous processing completed with errors: {processed_count}/{model_count} models processed successfully, "
            f"{error_count} failed"
        )
        # Log first few errors for debugging
        if errors:
            error_sample = "; ".join(errors[:3])
            if len(errors) > 3:
                error_sample += f" (and {len(errors) - 3} more errors)"
            logger.warning(f"Sample errors: {error_sample}")
    else:
        # All models failed
        error_summary = "; ".join(errors[:5])
        if len(errors) > 5:
            error_summary += f" (and {len(errors) - 5} more errors)"
        
        raise CalculatedModelError(
            f"Synchronous processing failed for all {model_count} models. Errors: {error_summary}",
            total_models=model_count,
            processed_models=processed_count,
            failed_models=error_count,
            error_count=len(errors)
        )


class CalculatedModelMixinMeta(ModelBase):
    def __new__(cls, name, bases, attrs, **kwargs):
        if 'Meta' not in attrs:
            class Meta:
                pass

            attrs['Meta'] = Meta

        if len(attrs['defining_fields']) != 0:
            attrs['Meta'].constraints = [
                UniqueConstraint(fields=attrs['defining_fields'], name='defining_fields_' + name)
            ]

        return super().__new__(cls, name, bases, attrs, **kwargs)


class CalculatedModelMixin(LexModel, metaclass=CalculatedModelMixinMeta):
    """
    Mixin for models that support calculated field combinations and parallel processing.
    
    This mixin provides a powerful framework for creating and processing multiple model
    instances based on combinatorial field expansion and parallel processing capabilities.
    It's designed for scenarios where you need to generate many model variations and
    process them efficiently using Celery workers.
    
    Key Concepts:
    
    1. **defining_fields**: List of field names that create unique model combinations.
       Each combination of values from these fields results in a separate model instance.
       For example, if defining_fields = ['region', 'product'] and region has values
       ['US', 'EU'] and product has values ['A', 'B'], then 4 models will be created:
       (US, A), (US, B), (EU, A), (EU, B).
    
    2. **parallelizable_fields**: List of field names used to group models for parallel
       processing in Celery workers. Models with the same values for parallelizable
       fields are processed together in the same Celery task. This must be a subset
       of defining_fields.
    
    3. **Model Creation Workflow**:
       - Step 1: Generate all combinations based on defining_fields
       - Step 2: Handle duplicate detection and resolution
       - Step 3: Organize models into clusters using parallelizable_fields
       - Step 4: Dispatch to Celery workers or process synchronously
    
    Usage Examples:
    
    Basic usage with defining fields only:
    ```python
    class MyModel(CalculatedModelMixin):
        defining_fields = ['region', 'product']
        parallelizable_fields = []  # Synchronous processing
        
        region = models.CharField(max_length=50)
        product = models.CharField(max_length=50)
        
        def get_selected_key_list(self, key):
            if key == 'region':
                return ['US', 'EU', 'APAC']
            elif key == 'product':
                return ['A', 'B', 'C']
            return []
        
        def calculate(self):
            # Perform calculations for this model instance
            pass
    
    # Create all combinations (9 models: 3 regions × 3 products)
    MyModel.create()
    ```
    
    Advanced usage with parallel processing:
    ```python
    class AdvancedModel(CalculatedModelMixin):
        defining_fields = ['region', 'product', 'scenario']
        parallelizable_fields = ['region']  # Group by region for Celery
        
        def get_selected_key_list(self, key):
            # Return possible values for each field
            pass
        
        def calculate(self):
            # Complex calculation that benefits from parallel processing
            pass
    
    # Create with field overrides
    AdvancedModel.create(region=['US', 'EU'], scenario=['optimistic'])
    ```
    
    Attributes:
        input (bool): Whether this model accepts input data. Defaults to False.
        defining_fields (List[str]): Field names that create unique combinations.
        parallelizable_fields (List[str]): Field names for grouping parallel processing.
    
    Methods:
        create(*args, **kwargs): Class method to create and process all model combinations.
        get_selected_key_list(key): Instance method to return possible values for a field.
        calculate(*args): Instance method to perform calculations for this model.
        delete_models_with_same_defining_fields(): Handle duplicate model detection.
    
    Error Handling:
        The mixin provides comprehensive error handling with specific exception types:
        - ModelCombinationError: Issues during field combination generation
        - ModelClusteringError: Problems with parallelizable field clustering  
        - CeleryDispatchError: Celery task dispatch or processing failures
        - CalculatedModelError: General calculated model operation errors
    
    Performance Considerations:
        - Small combinations (< 100): Synchronous processing is efficient
        - Medium combinations (100-1000): Consider parallelization
        - Large combinations (> 1000): Definitely use parallelization
        - Ensure CELERY_ACTIVE=True in settings for parallel processing
    
    See Also:
        - calculated_model_usage_examples.py: Comprehensive usage examples
        - ModelCombinationGenerator: Handles field combination expansion
        - ModelClusterManager: Manages parallelizable field clustering
        - CeleryTaskDispatcher: Handles Celery task dispatch and failure management
    """
    
    # Class attributes with type hints and documentation
    input: bool = False
    """Whether this model accepts input data. Set to True for models that process external input."""
    
    defining_fields: List[str] = []
    """
    List of field names that create unique model combinations.
    
    Each combination of values from these fields results in a separate model instance.
    The values for each field are obtained from get_selected_key_list() or field overrides
    passed to create(). Must be a list of valid field names on the model.
    
    Example:
        defining_fields = ['region', 'product', 'scenario']
        # Creates combinations like: (US, A, optimistic), (US, A, pessimistic), etc.
    """
    
    parallelizable_fields: List[str] = []
    """
    List of field names used to group models for parallel processing.
    
    Models with the same values for these fields are processed together in the same
    Celery task. This enables efficient parallel processing by grouping related
    calculations. Must be a subset of defining_fields.
    
    Example:
        defining_fields = ['region', 'product', 'scenario']
        parallelizable_fields = ['region']
        # Groups models by region: all US models in one task, all EU models in another
    """

    class Meta:
        abstract = True

    def get_selected_key_list(self, key: str) -> List[Any]:
        """
        Return possible values for a defining field.
        
        This method is called during model combination generation to determine
        what values each defining field can take. It should return a list of
        all possible values for the specified field.
        
        Args:
            key (str): The name of the defining field to get values for
            
        Returns:
            List[Any]: List of possible values for the field. Can be strings,
                      numbers, or other serializable types.
        
        Raises:
            NotImplementedError: If not implemented by subclass
            
        Example:
            def get_selected_key_list(self, key):
                if key == 'region':
                    return ['US', 'EU', 'APAC']
                elif key == 'product':
                    return ['ProductA', 'ProductB', 'ProductC']
                elif key == 'scenario':
                    return ['optimistic', 'realistic', 'pessimistic']
                else:
                    return []
        
        Note:
            - Return empty list for unknown keys
            - Values should be consistent across calls
            - Consider caching if values are expensive to compute
        """
        raise NotImplementedError(
            f"Subclass {self.__class__.__name__} must implement get_selected_key_list() "
            f"to return possible values for defining field '{key}'"
        )

    @abstractmethod
    def calculate(self):
        pass

    def lex_func(self):
        return self.calculate_mixin if hasattr(self, "calculate_mixin") else self.calculate


    # def get_func(self):
    #     return self.

    @abstractmethod
    def calculate_mixin(self) -> None:
        """
        Perform calculations for this model instance.
        
        This method is called for each model combination during processing.
        It should contain the business logic for calculating and setting
        field values on this model instance.
        
        Args:
            *args: Variable arguments passed from the create() method call.
                  These can be used to pass additional context or parameters
                  to the calculation logic.
        
        Raises:
            NotImplementedError: If not implemented by subclass
            
        Example:
            def calculate(self, base_year=2024):
                # Perform calculations based on model field values
                if self.region == 'US':
                    multiplier = 1.2
                elif self.region == 'EU':
                    multiplier = 1.0
                else:
                    multiplier = 0.8
                
                self.calculated_value = self.base_value * multiplier
                # Don't call save() here - it's handled by the framework
        
        Note:
            - Don't call save() in this method - saving is handled automatically
            - Use self.field_name to access defining field values
            - Calculations should be deterministic for the same field values
            - Consider performance for large numbers of model combinations
        """
        raise NotImplementedError(
            f"Subclass {self.__class__.__name__} must implement calculate() "
            f"to perform calculations for model instances"
        )




    @classmethod
    def create(cls, *args, **kwargs):
        """
        Create and process all model combinations based on defining fields.
        
        This method orchestrates the complete model creation and processing workflow:
        1. Generates all model combinations based on defining_fields configuration
        2. Handles duplicate model detection and resolution
        3. Organizes models into clusters for efficient parallel processing
        4. Dispatches processing to Celery workers or processes synchronously
        
        The method maintains identical functionality to the original implementation
        while providing improved code organization and error handling.
        
        Args:
            *args: Arguments passed to the calculate() method of each model
            **kwargs: Field overrides for defining_fields expansion
            
        Raises:
            CalculatedModelError: If any step of the model creation process fails
            
        Example:
            # Create models with specific field overrides
            MyCalculatedModel.create(region=['US', 'EU'], product=['A', 'B'])
            
            # Create models using default field values from get_selected_key_list
            MyCalculatedModel.create()
        """
        creation_start_time = logger.info(f"Starting model creation for {cls.__name__}")




        try:
            # Log initial configuration
            logger.info(
                f"Model creation configuration for {cls.__name__}: "
                f"{len(cls.defining_fields)} defining fields, "
                f"{len(cls.parallelizable_fields)} parallelizable fields"
            )
            
            if cls.defining_fields:
                logger.debug(f"Defining fields: {cls.defining_fields}")
            if cls.parallelizable_fields:
                logger.debug(f"Parallelizable fields: {cls.parallelizable_fields}")
            if kwargs:
                logger.debug(f"Field overrides provided: {list(kwargs.keys())}")
            
            # Step 1: Generate all model combinations based on defining fields
            logger.debug(f"Step 1: Generating model combinations for {cls.__name__}")
            try:
                base_model = cls()
                model_combinations = cls._generate_model_combinations(base_model, kwargs)
                logger.info(f"Generated {len(model_combinations)} model combinations for {cls.__name__}")
                
            except Exception as combination_error:
                raise CalculatedModelError(
                    f"Step 1 failed - Model combination generation failed for {cls.__name__}: {str(combination_error)}",
                    model_class=cls.__name__,
                    step="model_combination_generation",
                    defining_fields=cls.defining_fields,
                    field_overrides=list(kwargs.keys()) if kwargs else []
                ) from combination_error
            
            # Step 2: Handle duplicate models and prepare for processing
            logger.debug(f"Step 2: Preparing models for processing for {cls.__name__}")
            try:
                prepared_models = cls._prepare_models_for_processing(model_combinations)
                logger.info(f"Prepared {len(prepared_models)} models after duplicate handling for {cls.__name__}")
                
            except Exception as preparation_error:
                raise CalculatedModelError(
                    f"Step 2 failed - Model preparation failed for {cls.__name__}: {str(preparation_error)}",
                    model_class=cls.__name__,
                    step="model_preparation",
                    model_combinations_count=len(model_combinations) if 'model_combinations' in locals() else 0
                ) from preparation_error
            
            # Step 3: Organize models into processing clusters
            logger.debug(f"Step 3: Creating processing clusters for {cls.__name__}")
            try:
                processing_clusters = cls._create_processing_clusters(prepared_models)
                logger.info(f"Created processing clusters for {cls.__name__}")
                
            except Exception as clustering_error:
                raise CalculatedModelError(
                    f"Step 3 failed - Processing cluster creation failed for {cls.__name__}: {str(clustering_error)}",
                    model_class=cls.__name__,
                    step="cluster_creation",
                    prepared_models_count=len(prepared_models) if 'prepared_models' in locals() else 0,
                    parallelizable_fields=cls.parallelizable_fields
                ) from clustering_error

            context = operation_context.get()
            # Step 4: Dispatch for processing (Celery or synchronous)
            logger.debug(f"Step 4: Dispatching model processing for {cls.__name__}")
            try:
                cls._dispatch_model_processing(processing_clusters, *args)
                logger.info(f"Model processing dispatch completed for {cls.__name__}")
                
            except Exception as dispatch_error:
                raise CalculatedModelError(
                    f"Step 4 failed - Model processing dispatch failed for {cls.__name__}: {str(dispatch_error)}",
                    model_class=cls.__name__,
                    step="processing_dispatch",
                    celery_active=getattr(settings, 'CELERY_ACTIVE', False)
                ) from dispatch_error
            
            # Success - log completion
            logger.info(f"Successfully completed model creation and processing for {cls.__name__}")
            
        except CalculatedModelError:
            # Re-raise CalculatedModelError as-is (already has detailed context)
            logger.error(f"Model creation failed for {cls.__name__}")
            raise
        except Exception as unexpected_error:
            # Catch any other unexpected errors
            logger.error(f"Unexpected error during model creation for {cls.__name__}: {unexpected_error}")
            raise CalculatedModelError(
                f"Unexpected error during model creation for {cls.__name__}: {str(unexpected_error)}",
                model_class=cls.__name__
            ) from unexpected_error
    
    @classmethod
    def _generate_model_combinations(
        cls, 
        base_model: 'CalculatedModelMixin', 
        field_overrides: Dict[str, Any]
    ) -> List['CalculatedModelMixin']:
        """
        Generate all model combinations based on defining fields.
        
        Uses the ModelCombinationGenerator to create all possible combinations
        of models by expanding each defining field with its possible values.
        Field overrides from kwargs take precedence over default field values.
        
        Args:
            base_model: The base model instance to expand
            field_overrides: Dictionary of field values from kwargs
            
        Returns:
            List of model instances with all field combinations applied
            
        Raises:
            ModelCombinationError: If model combination generation fails
        """
        if not cls.defining_fields:
            logger.debug(f"No defining fields configured for {cls.__name__}, returning single base model")
            return [base_model]
        
        try:
            logger.debug(
                f"Generating model combinations for {cls.__name__} with {len(cls.defining_fields)} defining fields: "
                f"{cls.defining_fields}"
            )
            
            if field_overrides:
                override_fields = list(field_overrides.keys())
                logger.debug(f"Using field overrides for: {override_fields}")
            
            model_combinations = ModelCombinationGenerator.generate_model_combinations(
                base_model, cls.defining_fields, field_overrides
            )
            
            logger.info(
                f"Successfully generated {len(model_combinations)} model combinations for {cls.__name__} "
                f"from {len(cls.defining_fields)} defining fields"
            )
            return model_combinations
            
        except ModelCombinationError:
            # Re-raise ModelCombinationError as-is (already has detailed context)
            raise
        except Exception as e:
            raise ModelCombinationError(
                f"Unexpected error during model combination generation for {cls.__name__}: {str(e)}",
                model_class=cls.__name__,
                defining_fields=cls.defining_fields,
                field_overrides=list(field_overrides.keys()) if field_overrides else []
            ) from e
    
    @classmethod
    def _prepare_models_for_processing(
        cls, 
        model_combinations: List['CalculatedModelMixin']
    ) -> List['CalculatedModelMixin']:
        """
        Prepare models for processing by handling duplicates and validation.
        
        This method processes each model to handle duplicate detection using
        the delete_models_with_same_defining_fields method. This ensures that
        models with identical defining field values are properly managed.
        
        Args:
            model_combinations: List of model combinations to prepare
            
        Returns:
            List of prepared models ready for processing
            
        Raises:
            CalculatedModelError: If model preparation fails
        """
        if not model_combinations:
            logger.warning(f"No model combinations provided for {cls.__name__} preparation")
            return []
        
        if not isinstance(model_combinations, (list, tuple)):
            raise CalculatedModelError(
                f"Model combinations must be a list or tuple, got {type(model_combinations).__name__}",
                model_class=cls.__name__,
                combinations_type=type(model_combinations).__name__
            )
        
        logger.debug(f"Preparing {len(model_combinations)} model combinations for {cls.__name__}")
        
        prepared_models = []
        preparation_errors = []
        
        for i, model in enumerate(model_combinations):
            try:
                if model is None:
                    logger.warning(f"Model {i + 1}/{len(model_combinations)} is None, skipping")
                    continue
                
                if not isinstance(model, cls):
                    logger.warning(
                        f"Model {i + 1} is not an instance of {cls.__name__} "
                        f"(got {type(model).__name__}), skipping"
                    )
                    continue
                
                logger.debug(f"Preparing model {i + 1}/{len(model_combinations)}")
                
                # Handle duplicate models with same defining fields
                prepared_model = model.delete_models_with_same_defining_fields()
                prepared_models.append(prepared_model)
                
                logger.debug(f"Successfully prepared model {i + 1}")
                
            except CalculatedModelError as calc_error:
                preparation_errors.append(f"Model {i + 1}: {str(calc_error)}")
                logger.error(f"Failed to prepare model {i + 1}/{len(model_combinations)}: {calc_error}")
                
            except Exception as e:
                preparation_errors.append(f"Model {i + 1}: {str(e)}")
                logger.error(f"Unexpected error preparing model {i + 1}/{len(model_combinations)}: {e}")
        
        # Check if we have any prepared models
        if not prepared_models and model_combinations:
            error_summary = "; ".join(preparation_errors[:5])  # Limit to first 5 errors
            if len(preparation_errors) > 5:
                error_summary += f" (and {len(preparation_errors) - 5} more errors)"
            
            raise CalculatedModelError(
                f"Failed to prepare any models for {cls.__name__}. "
                f"Errors: {error_summary}",
                model_class=cls.__name__,
                total_combinations=len(model_combinations),
                preparation_errors=len(preparation_errors)
            )
        
        # Log preparation summary
        if preparation_errors:
            logger.warning(
                f"Model preparation completed with {len(preparation_errors)} errors: "
                f"{len(prepared_models)}/{len(model_combinations)} models prepared successfully"
            )
        else:
            logger.info(
                f"Successfully prepared all {len(prepared_models)} model combinations for {cls.__name__}"
            )
        
        return prepared_models
    
    @classmethod
    def _create_processing_clusters(
        cls, 
        prepared_models: List['CalculatedModelMixin']
    ) -> Dict[Any, Any]:
        """
        Create processing clusters based on parallelizable fields.
        
        Uses the ModelClusterManager to organize models into hierarchical
        clusters based on parallelizable_fields configuration. This enables
        efficient parallel processing by grouping models that can be processed
        independently.
        
        Args:
            prepared_models: List of prepared models to cluster
            
        Returns:
            Nested dictionary representing the cluster hierarchy
            
        Raises:
            ModelClusteringError: If clustering fails
        """
        if not prepared_models:
            logger.warning(f"No prepared models provided for {cls.__name__} clustering")
            return {}
        
        try:
            logger.debug(
                f"Creating processing clusters for {len(prepared_models)} models of type {cls.__name__}"
            )
            
            if cls.parallelizable_fields:
                logger.debug(
                    f"Using {len(cls.parallelizable_fields)} parallelizable fields for clustering: "
                    f"{cls.parallelizable_fields}"
                )
            else:
                logger.debug("No parallelizable fields configured, creating single cluster")
            
            processing_clusters = ModelClusterManager.create_clusters(
                prepared_models, cls.parallelizable_fields
            )
            
            # Log cluster information for debugging
            if cls.parallelizable_fields:
                try:
                    cluster_groups = ModelClusterManager.flatten_clusters_to_groups(processing_clusters)
                    cluster_count = len(cluster_groups)
                    
                    # Calculate cluster size statistics
                    if cluster_groups:
                        cluster_sizes = [len(group) for group in cluster_groups]
                        min_size = min(cluster_sizes)
                        max_size = max(cluster_sizes)
                        avg_size = sum(cluster_sizes) / len(cluster_sizes)
                        
                        logger.info(
                            f"Created {cluster_count} processing clusters for {cls.__name__} "
                            f"based on {len(cls.parallelizable_fields)} parallelizable fields. "
                            f"Cluster sizes: min={min_size}, max={max_size}, avg={avg_size:.1f}"
                        )
                    else:
                        logger.warning(f"Clustering resulted in no groups for {cls.__name__}")
                        
                except Exception as stats_error:
                    logger.warning(f"Could not calculate cluster statistics: {stats_error}")
                    logger.info(f"Created processing clusters for {cls.__name__}")
            else:
                logger.info(f"Created single cluster for {len(prepared_models)} models of type {cls.__name__}")
            
            return processing_clusters
            
        except ModelClusteringError:
            # Re-raise ModelClusteringError as-is (already has detailed context)
            raise
        except Exception as e:
            raise ModelClusteringError(
                f"Unexpected error during cluster creation for {cls.__name__}: {str(e)}",
                parallelizable_fields=cls.parallelizable_fields,
                model_count=len(prepared_models)
            ) from e


    @classmethod
    def _dispatch_model_processing(
        cls, 
        processing_clusters: Dict[Any, Any], 
        *args
    ) -> None:
        """
        Dispatch model processing to Celery workers or process synchronously.
        
        Determines whether to use Celery for parallel processing or fall back
        to synchronous processing based on the CELERY_ACTIVE setting. Uses
        the CeleryTaskDispatcher for Celery-based processing or direct
        synchronous processing as a fallback.
        
        Args:
            processing_clusters: Nested dictionary of model clusters
            *args: Arguments to pass to model calculate() methods
            
        Raises:
            CalculatedModelError: If model processing dispatch fails
        """
        if not processing_clusters:
            logger.warning(f"No processing clusters provided for {cls.__name__}, nothing to dispatch")
            return
        
        try:
            # Determine processing mode based on Celery configuration
            celery_active = os.getenv('CELERY_ACTIVE', None) == 'true' and hasattr(cls.calculate, 'delay')
            
            if celery_active:
                logger.info(f"Celery is active, dispatching {cls.__name__} models to parallel processing")

                try:
                    processing_groups = ModelClusterManager.flatten_clusters_to_groups(processing_clusters)

                    if processing_groups:
                        total_models = sum(len(group) for group in processing_groups)
                        logger.info(
                            f"Dispatching {len(processing_groups)} groups containing {total_models} "
                            f"models of type {cls.__name__} to Celery"
                        )

                        context = operation_context.get()
                        from lex.core.tasks.celery_dispatcher import CeleryTaskDispatcher
                        # from lex.lex_app.celery_tasks import synchronous_on_commit
                        # synchronous_on_commit(
                        #     CeleryTaskDispatcher.dispatch_calculation_groups,
                        #     processing_groups, args, context=context
                        # )

                        print(f"Transaction depth: {get_transaction_depth()}")

                        CeleryTaskDispatcher.dispatch_calculation_groups(processing_groups, *args, context=context)
                        logger.info(f"Celery dispatch completed for {cls.__name__}")
                        
                    else:
                        logger.warning(f"No processing groups created for {cls.__name__}, skipping Celery dispatch")
                        
                except ModelClusteringError as clustering_error:
                    raise CalculatedModelError(
                        f"Failed to flatten clusters for Celery dispatch: {str(clustering_error)}",
                        model_class=cls.__name__,
                        processing_mode="celery"
                    ) from clustering_error
                    
                except CeleryDispatchError as dispatch_error:
                    raise CalculatedModelError(
                        f"Celery dispatch failed: {str(dispatch_error)}",
                        model_class=cls.__name__,
                        processing_mode="celery"
                    ) from dispatch_error
                    
            else:
                logger.info(f"Celery not active, using synchronous processing for {cls.__name__}")
                
                try:
                    # Flatten all models from clusters for synchronous processing
                    processing_groups = ModelClusterManager.flatten_clusters_to_groups(processing_clusters)
                    all_models = []
                    
                    for group in processing_groups:
                        all_models.extend(group)
                    
                    if all_models:
                        logger.info(f"Processing {len(all_models)} models of type {cls.__name__} synchronously")
                        calc_and_save_sync(all_models, *args)
                        logger.info(f"Synchronous processing completed for {len(all_models)} models of type {cls.__name__}")
                    else:
                        logger.warning(f"No models to process for {cls.__name__}")
                        
                except ModelClusteringError as clustering_error:
                    raise CalculatedModelError(
                        f"Failed to flatten clusters for synchronous processing: {str(clustering_error)}",
                        model_class=cls.__name__,
                        processing_mode="synchronous"
                    ) from clustering_error
                    
                except Exception as sync_error:
                    raise CalculatedModelError(
                        f"Synchronous processing failed: {str(sync_error)}",
                        model_class=cls.__name__,
                        processing_mode="synchronous",
                        model_count=len(all_models) if 'all_models' in locals() else 0
                    ) from sync_error
                    
        except CalculatedModelError:
            # Re-raise CalculatedModelError as-is
            raise
        except Exception as e:
            raise CalculatedModelError(
                f"Unexpected error during model processing dispatch for {cls.__name__}: {str(e)}",
                model_class=cls.__name__
            ) from e

    def delete_models_with_same_defining_fields(self):
        """Handle duplicate models with same defining field values."""
        if not self.defining_fields:
            logger.debug(f"No defining fields configured for {self.__class__.__name__}, returning current model")
            return self

        try:
            # Build filter dictionary using proper field resolution
            filter_keys = {}
            defining_field_values = {}

            for field_name in self.defining_fields:
                try:
                    # Use _meta to resolve field properly
                    field = self._meta.get_field(field_name)

                    # Handle ForeignKey fields
                    if hasattr(field, 'remote_field') and field.remote_field:
                        # For ForeignKey, use the _id field name for filtering
                        filter_field_name = f"{field_name}_id"
                        related_obj = getattr(self, field_name, None)
                        if related_obj is not None:
                            field_value = related_obj.pk
                            defining_field_values[field_name] = str(related_obj)
                        else:
                            # Fallback to direct _id field access
                            field_value = getattr(self, filter_field_name, None)
                            defining_field_values[field_name] = field_value
                        filter_keys[filter_field_name] = field_value
                    else:
                        # Regular field
                        field_value = getattr(self, field_name)
                        filter_keys[field_name] = field_value
                        defining_field_values[field_name] = field_value

                except Exception as field_error:
                    available_fields = [f.name for f in self._meta.get_fields()]
                    raise CalculatedModelError(
                        f"Model {self.__class__.__name__} does not have defining field '{field_name}'",
                        field_name=field_name,
                        model_class=self.__class__.__name__,
                        available_fields=available_fields
                    ) from field_error

            logger.debug(f"Checking for duplicates with filter: {filter_keys}")

            # Query for existing models
            try:
                filtered_objects = type(self).objects.filter(**filter_keys)
                object_count = filtered_objects.count()

                if object_count == 1:
                    existing_model = filtered_objects.first()
                    logger.debug(f"Found existing model with ID {existing_model.pk}")
                    return existing_model
                elif object_count == 0:
                    # Reset primary key for fresh insert
                    if self.pk is not None:
                        self.pk = None
                        if hasattr(self, 'id'):
                            self.id = None
                    logger.debug("No existing models found, using current model")
                    return self
                else:
                    # Multiple models found - data integrity issue
                    existing_ids = list(filtered_objects.values_list('pk', flat=True))
                    field_details = [f"{k}={v}" for k, v in defining_field_values.items()]
                    defining_fields_str = ", ".join(field_details)

                    raise CalculatedModelError(
                        f"Found {object_count} models with identical defining field values. "
                        f"Fields: [{defining_fields_str}]. IDs: {existing_ids}.",
                        model_class=self.__class__.__name__,
                        duplicate_count=object_count,
                        existing_ids=existing_ids
                    )

            except Exception as query_error:
                if isinstance(query_error, CalculatedModelError):
                    raise
                raise CalculatedModelError(
                    f"Database query failed: {str(query_error)}",
                    model_class=self.__class__.__name__,
                    filter_keys=filter_keys
                ) from query_error

        except CalculatedModelError:
            raise
        except Exception as e:
            raise CalculatedModelError(
                f"Unexpected error: {str(e)}",
                model_class=self.__class__.__name__
            ) from e
