import asyncio
import os
from typing import List, Type, Optional

import nest_asyncio
from asgiref.sync import sync_to_async
from simple_history import register
from django.db import models


class ModelRegistration:
    """
    Handles registration of Django models with admin sites and history tracking.
    
    This class manages the registration of different types of models including:
    - HTML Report models
    - Process models  
    - Standard models with history tracking
    - CalculationModel instances with aborted calculation handling
    """
    
    @classmethod
    def register_models(cls, models: List[Type[models.Model]], untracked_models: Optional[List[str]] = None) -> None:
        """
        Register a list of Django models with appropriate admin sites and history tracking.
        
        Args:
            models: List of Django model classes to register
            untracked_models: Optional list of model names (lowercase) that should not have history tracking.
                            Defaults to empty list if None.
        
        Raises:
            ImportError: If required model classes cannot be imported
            AttributeError: If model registration fails due to missing attributes
        """
        from lex.lex_app.ProcessAdminSettings import processAdminSite, adminSite
        from lex.lex_app.lex_models.Process import Process
        from lex.lex_app.lex_models.html_report import HTMLReport
        from lex.lex_app.lex_models.CalculationModel import CalculationModel
        from django.contrib.auth.models import User

        # Initialize untracked_models to empty list if None provided
        if untracked_models is None:
            untracked_models = []

        # Configure User model display name
        def get_username(self):
            return f"{self.first_name} {self.last_name}"

        User.add_to_class("__str__", get_username)
        processAdminSite.register([User])

        # Process each model based on its type
        for model in models:
            try:
                if issubclass(model, HTMLReport):
                    cls._register_html_report(model)
                elif issubclass(model, Process):
                    cls._register_process_model(model)
                elif not issubclass(model, type) and not model._meta.abstract:
                    cls._register_standard_model(model, untracked_models)
                    
                    # Handle CalculationModel reset logic if applicable
                    if issubclass(model, CalculationModel):
                        cls._handle_calculation_model_reset(model)
            except Exception as e:
                raise RuntimeError(f"Failed to register model {model.__name__}: {str(e)}") from e

    @classmethod
    def _register_html_report(cls, model: Type[models.Model]) -> None:
        """
        Register an HTMLReport model with the process admin site.
        
        Args:
            model: HTMLReport model class to register
        """
        from lex.lex_app.ProcessAdminSettings import processAdminSite
        
        model_name = model.__name__.lower()
        processAdminSite.registerHTMLReport(model_name, model)
        processAdminSite.register([model])

    @classmethod
    def _register_process_model(cls, model: Type[models.Model]) -> None:
        """
        Register a Process model with the process admin site.
        
        Args:
            model: Process model class to register
        """
        from lex.lex_app.ProcessAdminSettings import processAdminSite
        
        model_name = model.__name__.lower()
        processAdminSite.registerProcess(model_name, model)
        processAdminSite.register([model])

    @classmethod
    def _register_standard_model(cls, model: Type[models.Model], untracked_models: List[str]) -> None:
        """
        Register a standard model with both admin sites and optional history tracking.
        
        Args:
            model: Standard model class to register
            untracked_models: List of model names that should not have history tracking
        """
        from lex.lex_app.ProcessAdminSettings import processAdminSite, adminSite
        
        model_name = model.__name__.lower()
        
        # Register with process admin site
        processAdminSite.register([model])
        
        # Add history tracking if model is not in untracked list
        if model_name not in untracked_models:
            register(model)
            processAdminSite.register([model.history.model])
        else:
            print(model_name)  # Maintain existing behavior for debugging
            
        # Register with standard admin site
        adminSite.register([model])

    @classmethod
    def _handle_calculation_model_reset(cls, model: Type[models.Model]) -> None:
        """
        Handle resetting of CalculationModel instances with aborted calculations.
        
        This method resets any CalculationModel instances that were left in IN_PROGRESS state
        when the application starts, marking them as ABORTED if Celery is not active.
        
        Args:
            model: CalculationModel class to handle reset for
        """
        from lex.lex_app.lex_models.CalculationModel import CalculationModel
        
        if not os.getenv("CALLED_FROM_START_COMMAND"):
            return
            
        @sync_to_async
        def reset_instances_with_aborted_calculations():
            """Reset calculation instances that were left in progress."""
            # if not os.getenv("CELERY_ACTIVE"):
            aborted_calc_instances = model.objects.filter(is_calculated=CalculationModel.IN_PROGRESS)
            aborted_calc_instances.update(is_calculated=CalculationModel.ABORTED)

        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(reset_instances_with_aborted_calculations())

    @classmethod
    def register_model_structure(cls, structure: dict):
        from lex.lex_app.ProcessAdminSettings import processAdminSite
        if structure: processAdminSite.register_model_structure(structure)

    @classmethod
    def register_model_styling(cls, styling: dict):
        from lex.lex_app.ProcessAdminSettings import processAdminSite
        if styling: processAdminSite.register_model_styling(styling)

    @classmethod
    def register_widget_structure(cls, structure):
        from lex.lex_app.ProcessAdminSettings import processAdminSite
        if structure: processAdminSite.register_widget_structure(structure)