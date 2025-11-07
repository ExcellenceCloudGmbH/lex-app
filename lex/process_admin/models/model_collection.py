from typing import Dict, Any, Set, Type, Union
from django.db.models import Model

from lex.process_admin.models.model_container import ModelContainer
from lex.process_admin.models.utils import enrich_model_structure_with_readable_names_and_types


def _create_model_containers(models_to_admins: Dict[Type[Model], Any]) -> Dict[str, ModelContainer]:
    """
    Create ModelContainer instances from model classes and their admin configurations.
    
    Args:
        models_to_admins: Dictionary mapping model classes to their admin configurations
        
    Returns:
        Dictionary mapping container IDs to ModelContainer instances
        
    Raises:
        ValueError: If an abstract model is registered (except HTMLReport subclasses)
    """
    ids2containers = dict()

    for model_class, process_admin in models_to_admins.items():
        from lex.core.models.html_report import HTMLReport
        if not issubclass(model_class, HTMLReport):
            if model_class._meta.abstract:
                raise ValueError(
                    f'The model {model_class._meta.model_name} is abstract, but only concrete models can be registered'
                )
        model_container = ModelContainer(model_class, process_admin)
        ids2containers[model_container.id] = model_container

    return ids2containers


class ModelCollection:
    """
    Collection of model containers that manages model structure and organization.
    
    The ModelCollection serves as a central registry for all models in the application,
    organizing them into a hierarchical structure for display and navigation purposes.
    It maintains the relationship between model classes, their admin configurations,
    and their presentation structure.
    
    Key concepts:
    - Model Structure: Hierarchical organization of models for UI display
    - Model Containers: Wrapper objects that combine model classes with their admin configs
    - Model Styling: Display configuration for customizing model presentation
    
    Attributes:
        ids2containers: Dictionary mapping model IDs to their containers
        model_structure: Hierarchical structure defining model organization
        model_styling: Configuration for customizing model display
        model_structure_with_readable_names: Enriched structure with display names
    """
    def __init__(self, models_to_admins: Dict[Type[Model], Any], model_structure: Dict[str, Any], model_styling: Dict[str, Any]) -> None:
        """
        Initialize the ModelCollection with models, structure, and styling configuration.
        
        Args:
            models_to_admins: Dictionary mapping model classes to their admin configurations
            model_structure: Hierarchical structure defining how models are organized for display.
                           If None, creates a default flat structure under 'Models'
            model_styling: Configuration dictionary for customizing model display properties
        """
        self.ids2containers = _create_model_containers(models_to_admins)
        self.model_structure = model_structure or {'Models': {c.id: None for c in self.all_containers}}
        self.model_styling = model_styling

        self.model_structure_with_readable_names = {
            node: enrich_model_structure_with_readable_names_and_types(node, sub_tree, self) 
            for node, sub_tree in self.model_structure.items()
        }

    @property
    def all_containers(self) -> Set[ModelContainer]:
        """
        Get all model containers in this collection.
        
        Returns:
            Set of all ModelContainer instances
        """
        return set(self.ids2containers.values())

    @property
    def all_model_ids(self) -> Set[str]:
        """
        Get all model IDs in this collection.
        
        Returns:
            Set of all model ID strings
        """
        return {c.id for c in self.all_containers}

    def get_container(self, id_or_model_class: Union[str, Type[Model]]) -> ModelContainer:
        """
        Get a model container by ID or model class.
        
        Args:
            id_or_model_class: Either a model ID string or a model class
            
        Returns:
            The corresponding ModelContainer instance
            
        Raises:
            KeyError: If the container is not found
            
        Note:
            This method maintains backward compatibility with the existing interface.
            For better error handling, use get_container_by_id() or get_container_by_model_class().
        """
        return self.ids2containers[id_or_model_class]

    def get_container_by_id(self, container_id: str) -> ModelContainer:
        """
        Get a model container by its ID with enhanced error handling.
        
        Args:
            container_id: The model ID string to look up
            
        Returns:
            The corresponding ModelContainer instance
            
        Raises:
            ValueError: If container_id is not a string or is empty
            KeyError: If no container exists with the given ID
        """
        if not isinstance(container_id, str):
            raise ValueError(f"Container ID must be a string, got {type(container_id).__name__}")
        
        if not container_id.strip():
            raise ValueError("Container ID cannot be empty")
        
        try:
            return self.ids2containers[container_id]
        except KeyError:
            available_ids = sorted(self.all_model_ids)
            raise KeyError(
                f"No container found with ID '{container_id}'. "
                f"Available IDs: {available_ids}"
            )

    def get_container_by_model_class(self, model_class: Type[Model]) -> ModelContainer:
        """
        Get a model container by its model class with enhanced error handling.
        
        Args:
            model_class: The Django model class to look up
            
        Returns:
            The corresponding ModelContainer instance
            
        Raises:
            ValueError: If model_class is not a valid Django model class
            KeyError: If no container exists for the given model class
        """
        if not isinstance(model_class, type):
            raise ValueError(f"Expected a model class, got {type(model_class).__name__}")
        
        # Check if it's a Django model by looking for _meta attribute
        if not hasattr(model_class, '_meta'):
            raise ValueError(f"Expected a Django model class, got {model_class.__name__}")
        
        # Get the model ID from the model class
        model_id = model_class._meta.model_name
        
        try:
            return self.ids2containers[model_id]
        except KeyError:
            available_models = [container.model_class.__name__ for container in self.all_containers]
            raise KeyError(
                f"No container found for model class '{model_class.__name__}'. "
                f"Available model classes: {sorted(available_models)}"
            )
