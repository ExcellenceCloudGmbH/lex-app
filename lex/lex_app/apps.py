import asyncio
import os
import sys
import threading
import traceback

import nest_asyncio
from asgiref.sync import sync_to_async
from celery import shared_task
from django.apps import apps

from lex.lex_app.model_utils.LexAuthentication import LexAuthentication
from lex.lex_app.settings import repo_name, CELERY_ACTIVE
from lex.lex_app.utils import GenericAppConfig
from lex.lex_app.logging.config import is_audit_logging_enabled, get_audit_logging_config


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
        
        from lex.lex_app.logging.InitialDataAuditLogger import InitialDataAuditLogger
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


def _create_audit_logger_for_task(audit_logging_enabled=None, calculation_id=None):
    """
    Create an audit logger instance for Celery task context.
    
    Args:
        audit_logging_enabled: Optional override for audit logging enablement
        calculation_id: Optional calculation ID for audit logging continuity
        
    Returns:
        InitialDataAuditLogger instance if enabled, None otherwise
    """
    try:
        # Use provided parameter or check configuration
        if audit_logging_enabled is False:
            print("Audit logging explicitly disabled for task context")
            return None
        elif audit_logging_enabled is True or is_audit_logging_enabled():
            from lex.lex_app.logging.InitialDataAuditLogger import InitialDataAuditLogger
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
        if repo_name != "lex":
            super().start(
                repo=repo_name
            )
            generic_app_models = {f"{model.__name__}": model for model in
                                  set(list(apps.get_app_config(repo_name).models.values())
                                      + list(apps.get_app_config(repo_name).models.values())) if model.__name__.count("Historical") != 1}
            nest_asyncio.apply()


            asyncio.run(self.async_ready(generic_app_models))

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
        _authentication_settings = LexAuthentication()
        print(_authentication_settings.initial_data_load)

        test = ProcessAdminTestCase()

        if (not running_in_uvicorn()
                or self.is_running_in_celery()
                or not _authentication_settings
                or not hasattr(_authentication_settings, 'initial_data_load')
                or not _authentication_settings.initial_data_load):
            return

        # Log audit logging configuration
        try:
            config = get_audit_logging_config()
            config_summary = config.get_configuration_summary()
            print(f"Audit logging configuration - Enabled: {config.audit_logging_enabled}, Batch size: {config.batch_size}")
            print(f"Configuration details: {config_summary}")
            
            # Validate configuration and warn about potential issues
            if config.batch_size > 1000:
                print(f"Warning: Large batch size ({config.batch_size}) may impact performance")
            if config.batch_size < 10:
                print(f"Warning: Small batch size ({config.batch_size}) may reduce efficiency")
                
        except ValueError as e:
            print(f"Error: Invalid audit logging configuration: {e}")
            print("Initial data upload will continue with audit logging disabled")
            traceback.print_exc()
        except Exception as e:
            print(f"Warning: Failed to load audit logging configuration: {e}")
            print("Using fallback configuration")
            traceback.print_exc()

        if await are_all_models_empty(test, _authentication_settings, generic_app_models):
            # Prepare audit logging parameters for task execution
            audit_enabled = is_audit_logging_enabled()
            calculation_id = None
            
            # if audit_enabled:
                # # Generate calculation ID for continuity between async_ready and task execution
                # try:
                #     from lex.lex_app.logging.InitialDataAuditLogger import InitialDataAuditLogger
                #     temp_logger = InitialDataAuditLogger()
                #     print(f"Generated calculation ID for task execution: {calculation_id}")
                # except Exception as e:
                #     print(f"Warning: Failed to generate calculation ID for task execution: {e}")
                #     print("Task will generate its own calculation ID")
                #     traceback.print_exc()
                #     calculation_id = None

            # TODO
            if False or (os.getenv("DEPLOYMENT_ENVIRONMENT")
                    and os.getenv("ARCHITECTURE") == "MQ/Worker"):
                # Pass audit logging parameters to Celery task
                from lex.lex_app.celery_tasks import load_data, RunInCelery
                with RunInCelery():
                    load_data(test, generic_app_models, audit_enabled, _authentication_settings.initial_data_load)
            else:
                # Pass audit logging parameters to thread
                from lex.lex_app.celery_tasks import load_data
                x = threading.Thread(target=load_data, args=(test, generic_app_models, audit_enabled, _authentication_settings.initial_data_load))
                x.start()
        else:
            test.test_path = _authentication_settings.initial_data_load
            non_empty_models = await sync_to_async(test.get_list_of_non_empty_models)(generic_app_models)
            print(f"Loading Initial Data not triggered due to existence of objects of Model: {non_empty_models}")
            print("Not all referenced Models are empty")


async def are_all_models_empty(test, _authentication_settings, generic_app_models):
    """
    Check if all models are empty.
    """
    test.test_path = _authentication_settings.initial_data_load
    return await sync_to_async(test.check_if_all_models_are_empty)(generic_app_models)


def running_in_uvicorn():
    """
    Check if the application is running in Uvicorn context.
    """
    return sys.argv[-1:] == ["lex_app.asgi:application"] and os.getenv("CALLED_FROM_START_COMMAND")