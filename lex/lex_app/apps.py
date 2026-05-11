import asyncio
import logging
import os
import sys
import threading
import traceback

import nest_asyncio
from asgiref.sync import sync_to_async
from celery import shared_task
from django.apps import apps
from django.contrib.admin.apps import AdminConfig


from lex.authentication.utils.lex_authentication import LexAuthentication
from lex.core.config import LexProjectConfig
from lex.lex_app.settings import repo_name, CELERY_ACTIVE
from lex.utilities.config.generic_app_config import GenericAppConfig
from lex.audit_logging.utils.config import is_audit_logging_enabled, get_audit_logging_config

logger = logging.getLogger(__name__)


class CustomAdminConfig(AdminConfig):
    """
    Custom admin configuration that prevents django.contrib.auth.admin from being loaded.
    This avoids the AlreadyRegistered exception for the Group model.
    """
    default_site = 'django.contrib.admin.sites.AdminSite'
    
    def ready(self):
        # Don't call super().ready() to prevent autodiscovery
        # This prevents django.contrib.auth.admin from being loaded
        pass


def _create_audit_logger():
    """
    Create an audit logger instance if audit logging is enabled.
    
    Returns:
        InitialDataAuditLogger instance if enabled, None otherwise
    """
    try:
        if not is_audit_logging_enabled():
            print("Audit logging is disabled for initial data upload")
            return None
        
        from lex.audit_logging.utils.InitialDataAuditLogger import InitialDataAuditLogger
        logger = InitialDataAuditLogger()
        print(f"Successfully initialized audit logger")
        return logger
    except ImportError as e:
        print(f"Warning: Failed to import audit logger module: {e}")
        print("Initial data upload will continue without audit logging")
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"Warning: Failed to initialize audit logger: {e}")
        print("Initial data upload will continue without audit logging")
        traceback.print_exc()
        return None


def _create_audit_logger_for_task(audit_logging_enabled=None):
    """
    Create an audit logger instance for Celery task context.
    
    Args:
        audit_logging_enabled: Optional override for audit logging enablement

    Returns:
        InitialDataAuditLogger instance if enabled, None otherwise
    """
    try:
        # Use provided parameter or check configuration
        if audit_logging_enabled is False:
            print("Audit logging explicitly disabled for task context")
            return None
        elif audit_logging_enabled is True or is_audit_logging_enabled():
            from lex.audit_logging.utils.InitialDataAuditLogger import InitialDataAuditLogger
            logger = InitialDataAuditLogger()
            return logger
        else:
            print("Audit logging is disabled for task context")
            return None
    except ImportError as e:
        print(f"Warning: Failed to import audit logger module in task context: {e}")
        print("Initial data upload will continue without audit logging")
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"Warning: Failed to initialize audit logger in task context: {e}")
        print("Initial data upload will continue without audit logging")
        traceback.print_exc()
        return None





def should_load_data(auth_settings):
    """
    Check whether the initial data should be loaded.
    """
    return hasattr(auth_settings, 'initial_data_load') and auth_settings.initial_data_load


class LexAppConfig(GenericAppConfig):
    name = 'lex_app'
    
    
    def ready(self):
        super().ready()
        if not repo_name.startswith('lex') and self.name == 'lex_app':
            super().start(
                repo=repo_name,
                is_lex=False
            )
            generic_app_models = {f"{model.__name__}": model for model in
                                  set(list(apps.get_app_config(repo_name).models.values())
                                      + list(apps.get_app_config(repo_name).models.values())) if model.__name__.count("Historical") != 1}
            nest_asyncio.apply()


            asyncio.run(self.async_ready(generic_app_models))

    def register_models(self):
        """
        Override parent's register_models to filter out lex core models
        when registering repo models, but include Historical models for repo models.
        """
        from django.contrib import admin
        from django.contrib.auth import get_user_model
        from lex.process_admin.utils.model_registration import ModelRegistration
        from lex.lex_app.streamlit.Streamlit import Streamlit

        # Models to explicitly exclude (shouldn't show in frontend)
        # REMOVED Profile from exclusion to allow history tracking demo
        excluded_model_names = {'Profile', 'HistoricalProfile'}

        # Filter models appropriately
        models_to_register = []
        for model in self.discovered_models.values():
            # Skip explicitly excluded models
            if model.__name__ in excluded_model_names:
                continue

            # Skip if already registered in admin
            if admin.site.is_registered(model):
                continue

            # Skip if it's a lex core model (already registered)
            if hasattr(self, '_lex_core_models') and model in self._lex_core_models:
                continue

            # Skip Django built-in models
            model_meta = getattr(model, "_meta", None)
            if model_meta is not None and model_meta.app_label in ['auth', 'contenttypes', 'sessions', 'admin']:
                continue

            models_to_register.append(model)

        if models_to_register:
            logger.info(f"Registering {len(models_to_register)} models from {repo_name}")
            logger.debug(f"Models: {[m.__name__ for m in models_to_register]}")
            ModelRegistration.register_models(
                models_to_register,
                self.untracked_models,
                self.model_structure_builder.history_tracking_enabled,
            )
        else:
            logger.debug(f"No new models to register from {repo_name}")

        # Register model structure and styling if available
        # This provides the organized structure in the frontend
        if (
            self.model_structure_builder.model_structure
            or self.model_structure_builder.model_structure_is_explicitly_defined
        ):
            logger.debug(f"Registering model structure for {repo_name}")
            ModelRegistration.register_model_structure(
                self.model_structure_builder.model_structure,
                auto_include_missing_models=not self.model_structure_builder.model_structure_is_explicitly_defined,
            )
        if self.model_structure_builder.model_styling:
            ModelRegistration.register_model_styling(self.model_structure_builder.model_styling)
        if self.model_structure_builder.widget_structure:
            ModelRegistration.register_widget_structure(self.model_structure_builder.widget_structure)
        ModelRegistration.register_models(
            [Streamlit],
            self.untracked_models,
            self.model_structure_builder.history_tracking_enabled,
        )
        ModelRegistration.register_models(
            [get_user_model()],
            self.untracked_models,
            self.model_structure_builder.history_tracking_enabled,
        )

    def is_running_in_celery(self):
        # from celery import current_task
        # if current_task and current_task.request:
        #     return True
        return os.getenv('IS_RUNNING_IN_CELERY', 'false') == 'true'

    async def async_ready(self, generic_app_models):
        """
        Check conditions and decide whether to load data asynchronously.
        """
        from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
        from lex.core.config import LexProjectConfig
        test = ProcessAdminTestCase()


        project_config = LexProjectConfig.load()
        # TODO: FIX THIS
        if ( not running_in_uvicorn()
                or self.is_running_in_celery()
                or not project_config._loaded):
            return

        # Log audit logging configuration
        try:
            config = get_audit_logging_config()
            config_summary = config.get_configuration_summary()
            print(f"Audit logging configuration - Enabled: {config.audit_logging_enabled}")
            print(f"Configuration details: {config_summary}")

        except ValueError as e:
            print(f"Error: Invalid audit logging configuration: {e}")
            print("Initial data upload will continue with audit logging disabled")
            traceback.print_exc()
        except Exception as e:
            print(f"Warning: Failed to load audit logging configuration: {e}")
            print("Using fallback configuration")
            traceback.print_exc()

        # Validate initial data path before proceeding
        if not self.validate_initial_data_path(project_config.initial_data):
            return

        if await are_all_models_empty(test, generic_app_models):
            # Prepare audit logging parameters for task execution
            audit_enabled = is_audit_logging_enabled()

            if os.getenv("DEPLOYMENT_ENVIRONMENT") and os.getenv("ARCHITECTURE") == "MQ/Worker":
                from lex.lex_app.celery_tasks import load_data, WaitForTasks

                def _load_initial_data_in_context():
                    with WaitForTasks():
                        load_data(
                            test,
                            generic_app_models,
                            audit_enabled,
                            project_config.initial_data,
                        )

                await sync_to_async(_load_initial_data_in_context, thread_sensitive=False)()
            else:
                # Pass audit logging parameters to thread
                from lex.lex_app.celery_tasks import load_data
                x = threading.Thread(target=load_data, args=(test, generic_app_models, audit_enabled, project_config.initial_data))
                x.start()
        else:
            test.test_path = project_config.initial_data
            non_empty_models = await sync_to_async(test.get_list_of_non_empty_models)(generic_app_models)
            print(f"Loading Initial Data not triggered due to existence of objects of Model: {non_empty_models}")
            print("Not all referenced Models are empty")


    def validate_initial_data_path(self, initial_data_path):
        """
        Validates that the initial data file exists, is accessible, and contains valid JSON structure.
        Mimics logic from ProcessAdminTestCase.get_test_data() and adds content validation.
        """
        import json
        
        if not initial_data_path:
            print("Initial data path not configured.")
            return False

        try:
            # Construct the absolute path matches ProcessAdminTestCase logic
            clean_path = initial_data_path.replace('/', os.sep)
            project_root = os.getenv("PROJECT_ROOT")
            
            if not project_root:
                # Fallback if PROJECT_ROOT is not set, though ProcessAdminTestCase relies on it
                print("Warning: PROJECT_ROOT environment variable not set. Defaulting to os.getcwd().")
                project_root = os.getcwd()
                
            full_path = str(project_root) + os.sep + clean_path
            
            # Check existence
            if not os.path.exists(full_path):
                print(f"ERROR: Initial data file not found at: {full_path}")
                print(f"Configured path was: {initial_data_path}")
                print(f"PROJECT_ROOT was: {project_root}")
                print("Skipping initial data load to prevent crash.")
                return False
                
            # Basic read check
            if not os.access(full_path, os.R_OK):
                print(f"ERROR: No read permission for initial data file: {full_path}")
                return False

            # Check if it's a valid file (not directory)
            if not os.path.isfile(full_path):
                print(f"ERROR: Initial data path is not a file: {full_path}") 
                return False
                
            # Validate Content
            try:
                with open(full_path, 'r') as f:
                    data = json.load(f)
                    
                if not isinstance(data, list):
                    print(f"ERROR: Initial data file must contain a JSON list. Found: {type(data)}")
                    return False
                    
                for idx, item in enumerate(data):
                    if not isinstance(item, dict):
                         print(f"ERROR: Item at index {idx} is not a dictionary.")
                         return False
                    
                    # Check for required attributes that ProcessAdminTestCase expects
                    if 'subprocess' in item:
                        continue # Subprocesses are handled recursively in actual code, we skip shallow validation here
                        
                    if 'class' not in item:
                        print(f"ERROR: Item at index {idx} missing required attribute 'class'.")
                        print(f"Item content: {item}")
                        return False
                        
                    # action is also used in setUpCloudStorage, but let's at least check class
                    if 'action' not in item:
                        print(f"Warning: Item at index {idx} missing attribute 'action'. This might cause issues.")

            except json.JSONDecodeError as e:
                print(f"ERROR: Initial data file contains invalid JSON: {e}")
                return False
            except Exception as e:
                 print(f"ERROR: Failed to validate initial data file content: {e}")
                 traceback.print_exc()
                 return False

            return True
            
        except Exception as e:
            print(f"ERROR: unexpected error validating initial data path: {e}")
            traceback.print_exc()
            return False


async def are_all_models_empty(test,  generic_app_models):
    """
    Check if all models are empty.
    """
    project_config = LexProjectConfig.load()
    test.test_path = project_config.initial_data
    return await sync_to_async(test.check_if_all_models_are_empty)(generic_app_models)


def running_in_uvicorn():
    """
    Check if the application is running in Uvicorn context.
    """
    return sys.argv[-1:] == ["lex_app.asgi:application"] and os.getenv("CALLED_FROM_START_COMMAND")
