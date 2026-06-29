import inspect
import io
import json
import logging
import os
import threading
from pathlib import Path
from unittest import TestCase

import dateutil.parser
from lex.audit_logging.mixins.AuditLogMixin import _safe_get_content_type
from django.apps import apps
from django.core.cache import cache
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import models
from lex.api.utils import OperationContext
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.audit_logging.utils.config import is_audit_logging_enabled
from lex.lex_app import settings

logger = logging.getLogger(__name__)


class ProcessAdminTestCase(TestCase):

    def _save_seed_instance(self, instance, calculation_id, audit_log):
        """Persist a seed object, running any triggered calculation on the
        dedicated calculation thread pool — mirroring the live ``One.update``
        path — instead of inline on the initial-data loader thread.

        A seeded ``CalculationModel`` whose ``is_calculated`` is
        ``IN_PROGRESS`` turns ``save()`` into a calculation trigger. The live
        request path (``lex.api.views.model_entries.One``) never runs that
        calculation on the request/ASGI thread: it persists ``IN_PROGRESS``
        with the hook deferred, then submits ``calculate_hook`` to
        ``_calculation_executor``. We do the same here so initial-data
        calculations execute on the ``lex-calc`` pool, off the bootstrap
        thread.

        Unlike the request path (which returns HTTP 202 and lets the pool
        finish in the background), the loader processes actions **in order**
        and a later action may reference an earlier calculation's result, so
        we block on the future before returning — the calculation still runs
        on the pool, but the ordering guarantee of the initial-data upload is
        preserved.

        Deferring is required, not cosmetic: submitting the hook from *inside*
        ``save()``'s ``transaction.atomic()`` would deadlock (the pool thread
        opens its own connection and waits on the row lock the loader thread
        holds) — see ``CalculationModel._run_in_calculation_executor``.
        """
        from lex.core.models.CalculationModel import (
            CalculationModel,
            _calculation_executor,
        )

        is_calculation_trigger = (
            isinstance(instance, CalculationModel)
            and getattr(instance, "is_calculated", None) == CalculationModel.IN_PROGRESS
            and not getattr(instance, "_calculation_hook_in_progress", False)
        )

        with OperationContext({}, calculation_id, audit_log=audit_log):
            with model_logging_context(instance):
                if not is_calculation_trigger:
                    instance.save()
                    return

                # Phase 1: persist IN_PROGRESS with the hook deferred so the
                # calculation runs OUTSIDE this save's atomic block.
                instance._defer_calculate_hook = True
                instance.save()

                # Phase 2: run calculate_hook on the calculation pool, then
                # wait for it (ordered seeding). copy_context() captures this
                # OperationContext so the calculation_id reaches the pool
                # thread; the pool callable installs its own ModelContext.
                from contextvars import copy_context

                from django.db import close_old_connections
                from lex.audit_logging.utils.ModelContext import (
                    ModelContext,
                    _model_context,
                )

                ctx = copy_context()

                def _invoke_calculate_hook():
                    _model_context.set(
                        {"model_context": ModelContext([instance])}
                    )
                    instance._defer_calculate_hook = False
                    try:
                        instance.calculate_hook()
                    except Exception:
                        # Outside save(), so we own the terminal-failure audit
                        # flush that LexModel.save would otherwise perform.
                        try:
                            instance._finalize_pending_terminal_audit()
                        except Exception:
                            logger.exception(
                                "Failed to finalize terminal failure audit for "
                                "%s after initial-data calculation raised",
                                instance,
                            )
                        raise

                def _background_calculate():
                    try:
                        ctx.run(_invoke_calculate_hook)
                    finally:
                        close_old_connections()

                future = _calculation_executor.submit(_background_calculate)
                future.result()

    def replace_tagged_parameters(self, object_parameters):
        for key in object_parameters:
            value: str = object_parameters[key]
            if isinstance(value, str):
                parsed_value = value
                if value.startswith("tag:"):
                    parsed_value = self.tagged_objects[value.replace("tag:", "")]
                elif value.startswith("datetime:"):
                    parsed_value = dateutil.parser.parse(value.replace("datetime:", ""))
                object_parameters[key] = parsed_value

        return object_parameters

    test_path = None

    def setUpCloudStorage(self, generic_app_models, audit_logger=None) -> None:
        from datetime import datetime
        self.t0 = datetime.now()
        self.tagged_objects = {}
        test_data = self.get_test_data()
        
        # Check if audit logging is enabled
        audit_enabled = (audit_logger is not None and is_audit_logging_enabled())
        
        for object in test_data:
            klass = generic_app_models[object['class']]
            action = object['action']
            tag = object['tag'] if 'tag' in object else 'instance'
            audit_log = None
            
            try:
                if action == 'create':
                    object['parameters'] = self.replace_tagged_parameters(object['parameters'])
                    
                    # Generate calculation_id and log audit entry before creation
                    calculation_id = audit_logger.generate_calculation_id() if audit_enabled else None
                    if audit_enabled:
                        audit_log = audit_logger.log_object_creation(klass, object['parameters'], tag, calculation_id)

                    self.tagged_objects[tag] = klass(**object['parameters'])
                    for parameter in object['parameters'].keys():
                        if (isinstance(self.tagged_objects[tag]._meta.get_field(parameter), (models.FileField))):
                            upload_to = self.tagged_objects[tag]._meta.get_field(parameter).upload_to
                            if upload_to and not upload_to.endswith('/'):
                                upload_to += "/"
                            path = f"{upload_to}{os.path.basename(self.tagged_objects[tag].__dict__[parameter])}"
                            file_name = os.path.basename(self.tagged_objects[tag].__dict__[parameter])
                            f = open(f"{os.getcwd()}/{self.tagged_objects[tag].__dict__[parameter]}", "rb")
                            file_content = f.read()
                            new_file_name = default_storage.save(path, content=File(io.BytesIO(file_content),
                                                                                    name=f"{file_name}"))
                            self.tagged_objects[tag].__dict__[parameter] = new_file_name

                    cache.set(threading.get_ident(), str(object['class']) + "_" + action)

                    self._save_seed_instance(
                        self.tagged_objects[tag], calculation_id, audit_log
                    )

                    instance = self.tagged_objects[tag]
                    if audit_enabled:
                        if CalculationLog.objects.filter(calculationId=calculation_id).count() == 0:
                            audit_log.calculation_id = None
                        audit_log.content_type = _safe_get_content_type(instance.__class__)
                        audit_log.object_id = instance.pk
                        payload = audit_log.payload
                        payload['id'] = instance.pk
                        audit_log.payload = payload
                        audit_log.save()
                    # Mark audit log as successful if audit logging is enabled
                    if audit_enabled and audit_log:
                        audit_logger.mark_operation_success(audit_log)
                        
                elif action == 'update':
                    object['filter_parameters'] = self.replace_tagged_parameters(object['filter_parameters'])
                    self.tagged_objects[tag] = klass.objects.filter(**object['filter_parameters']).first()
                    if self.tagged_objects[tag] is not None:
                        # Generate calculation_id and log audit entry before update
                        calculation_id = audit_logger.generate_calculation_id() if audit_enabled else None
                        if audit_enabled:
                            audit_log = audit_logger.log_object_update(klass, self.tagged_objects[tag], object['parameters'], tag, calculation_id)

                        for key in object['parameters']:
                            if isinstance(self.tagged_objects[tag]._meta.get_field(key), (models.FileField)):
                                upload_to = self.tagged_objects[tag]._meta.get_field(key).upload_to
                                if upload_to and not upload_to.endswith('/'):
                                    upload_to += "/"
                                path = f"{upload_to}{os.path.basename(object['parameters'][key])}"
                                file_name = os.path.basename(object['parameters'][key])
                                f = open(f"{os.getcwd()}/{object['parameters'][key]}", "rb")
                                file_content = f.read()
                                new_file_name = default_storage.save(path, content=File(io.BytesIO(file_content),
                                                                                        name=f"{file_name}"))
                                setattr(self.tagged_objects[tag], key, new_file_name)
                            else:
                                setattr(self.tagged_objects[tag], key, object['parameters'][key])

                        cache.set(threading.get_ident(),
                                  str(object['class']) + "_" + action + "_" + str(self.tagged_objects[tag].pk))

                        self._save_seed_instance(
                            self.tagged_objects[tag], calculation_id, audit_log
                        )
                        instance = self.tagged_objects[tag]
                        if audit_enabled:
                            if CalculationLog.objects.filter(calculationId=calculation_id).count() == 0:
                                audit_log.calculation_id = None
                            audit_log.content_type = _safe_get_content_type(instance.__class__)
                            audit_log.object_id = instance.pk
                            audit_log.save()
                        # Mark audit log as successful if audit logging is enabled
                        if audit_enabled and audit_log:
                            audit_logger.mark_operation_success(audit_log)

                elif action == 'delete':
                    # Log audit entry before deletion if audit logging is enabled
                    instances = klass.objects.filter(**object['filter_parameters'])

                    for instance in instances:
                        if audit_enabled:
                            audit_log = audit_logger.log_object_deletion(instance, object['filter_parameters'], tag)
                            audit_log.content_type = _safe_get_content_type(klass)
                            audit_log.object_id = instance.pk
                            audit_log.save()

                        instance.delete()

                        # Mark audit log as successful if audit logging is enabled
                        if audit_enabled and audit_log:
                            audit_logger.mark_operation_success(audit_log)

                    # If no instances matched, still do the (no-op) delete for consistency
                    if not instances.exists():
                        klass.objects.filter(**object['filter_parameters']).delete()

            except Exception as e:
                # Mark audit log as failed if audit logging is enabled and an error occurred
                if audit_enabled and audit_log:
                    import traceback
                    error_msg = f"Error during {action} operation on {klass.__name__} (tag: {tag}) in setUpCloudStorage: {str(e)}\n{traceback.format_exc()}"
                    try:
                        audit_logger.mark_operation_failure(audit_log, error_msg)
                    except Exception as audit_error:
                        # If audit logging itself fails, log it but don't break the main process
                        print(f"Warning: Failed to mark audit log as failed during setUpCloudStorage: {audit_error}")
                # Re-raise the exception to maintain existing error handling behavior
                raise


    def setUp(self, audit_logger=None) -> None:
        from datetime import datetime

        generic_app_models = {f"{model.__name__}": model for model in
                              set(apps.get_app_config(settings.repo_name).models.values())}

        self.t0 = datetime.now()
        self.tagged_objects = {}
        test_data = self.get_test_data()
        
        # Check if audit logging is enabled
        audit_enabled = (audit_logger is not None and is_audit_logging_enabled())
        
        for object in test_data:
            klass = generic_app_models[object['class']]
            action = object['action']
            tag = object['tag'] if 'tag' in object else 'instance'
            audit_log = None
            
            try:
                if action == 'create':
                    object['parameters'] = self.replace_tagged_parameters(object['parameters'])
                    
                    # Generate calculation_id and log audit entry before creation
                    calculation_id = audit_logger.generate_calculation_id() if audit_enabled else None
                    if audit_enabled:
                        audit_log = audit_logger.log_object_creation(klass, object['parameters'], tag, calculation_id)

                    self.tagged_objects[tag] = klass(**object['parameters'])
                    cache.set(threading.get_ident(), str(object['class']) + "_" + action)

                    self._save_seed_instance(
                        self.tagged_objects[tag], calculation_id, audit_log
                    )

                    instance = self.tagged_objects[tag]

                    if audit_enabled:
                        if CalculationLog.objects.filter(calculationId=calculation_id).count() == 0:
                            audit_log.calculation_id = None
                        audit_log.content_type = _safe_get_content_type(instance.__class__)
                        audit_log.object_id = instance.pk
                        payload = audit_log.payload
                        payload['id'] = instance.pk
                        audit_log.payload = payload
                        audit_log.save()
                    # Mark audit log as successful if audit logging is enabled
                    if audit_enabled and audit_log:
                        audit_logger.mark_operation_success(audit_log)
                        
                elif action == 'update':
                    object['filter_parameters'] = self.replace_tagged_parameters(object['filter_parameters'])
                    self.tagged_objects[tag] = klass.objects.filter(**object['filter_parameters']).first()
                    if self.tagged_objects[tag] is not None:
                        # Generate calculation_id and log audit entry before update
                        calculation_id = audit_logger.generate_calculation_id() if audit_enabled else None
                        if audit_enabled:
                            audit_log = audit_logger.log_object_update(klass, self.tagged_objects[tag], object['parameters'], tag, calculation_id)

                        for key in object['parameters']:
                            setattr(self.tagged_objects[tag], key, object['parameters'][key])

                        cache.set(threading.get_ident(),
                                  str(object['class']) + "_" + action + "_" + str(self.tagged_objects[tag].pk))

                        self._save_seed_instance(
                            self.tagged_objects[tag], calculation_id, audit_log
                        )

                        instance = self.tagged_objects[tag]
                        if audit_enabled:
                            if CalculationLog.objects.filter(calculationId=calculation_id).count() == 0:
                                audit_log.calculation_id = None
                            audit_log.content_type = _safe_get_content_type(instance.__class__)
                            audit_log.object_id = instance.pk
                            audit_log.save()
                        # Mark audit log as successful if audit logging is enabled
                        if audit_enabled and audit_log:
                            audit_logger.mark_operation_success(audit_log)
                            
                elif action == 'delete':
                    # Log audit entry before deletion if audit logging is enabled

                    instances = klass.objects.filter(**object['filter_parameters'])

                    for instance in instances:

                        if audit_enabled:
                            audit_log = audit_logger.log_object_deletion(instance, object['filter_parameters'], tag)

                        if audit_enabled:
                            audit_log.content_type = _safe_get_content_type(klass)
                            audit_log.object_id = instance.pk
                            audit_log.save()

                        if audit_enabled and audit_log:
                            audit_logger.mark_operation_success(audit_log)
                        instance.delete()
                        
            except Exception as e:
                # Mark audit log as failed if audit logging is enabled and an error occurred
                if audit_enabled and audit_log:
                    import traceback
                    error_msg = f"Error during {action} operation on {klass.__name__} (tag: {tag}) in setUp: {str(e)}\n{traceback.format_exc()}"
                    try:
                        audit_logger.mark_operation_failure(audit_log, error_msg)
                    except Exception as audit_error:
                        # If audit logging itself fails, log it but don't break the main process
                        print(f"Warning: Failed to mark audit log as failed during setUp: {audit_error}")
                # Re-raise the exception to maintain existing error handling behavior
                raise

    def tearDown(self) -> None:
        pass
        # logs = pd.DataFrame.from_records(CalculationLog.objects.filter(message_type__in=["Test: Success", "Test: Error"], timestamp__gt=self.t0).values())
        # if len(logs) > 0:
        #     traces = logs['method'].str.replace("'", "").str.replace("\[", "").str.replace("\]", "").str[:-1].str[1:].str.split("\), \(", expand=True)
        #     logs = pd.concat([logs, traces], axis=1)
        #     logs.drop(columns=['id', 'method'], inplace=True)
        #     path = "generic_app/ExcelLogs/TestLogs/" + f"""TestLogs_{datetime.now().strftime("%Y-%m-%d-%H_%M_%S")}.xlsx"""
        #     logs.to_excel(path)
        #     raise Exception
        #
        # super().tearDown()

    def get_test_data(self):
        if self.test_path is None:
            file = inspect.getfile(self.__class__)
            path = Path(file).parent
            clean_test_path = str(path) + os.sep + "test_data.json"
        else:
            clean_test_path = self.test_path.replace('/', os.sep)
            clean_test_path = os.getenv("PROJECT_ROOT") + os.sep + clean_test_path
        test_data = self.get_test_data_from_path(clean_test_path)
        return test_data


    def get_test_data_from_path(self, path):
        with open(str(path), 'r') as f:
            test_data = json.loads(f.read())
            for index, object in enumerate(test_data):
                if "subprocess" in object:
                    subprocess_path = object['subprocess'].replace('/', os.sep)
                    subprocess_path = os.getenv("PROJECT_ROOT") + os.sep + subprocess_path
                    sublist = self.get_test_data_from_path(subprocess_path)
                    test_data[index] = sublist
        flat_list = []
        for sublist in test_data:
            if type(sublist) == list:
                flat_list.extend(sublist)
            else:
                flat_list.append(sublist)
        return flat_list


    def get_classes(self, generic_app_models):
        test_data = self.get_test_data()
        return set([generic_app_models[object['class']] for object in test_data])

    def check_if_all_models_are_empty(self, generic_app_models):
        for klass in self.get_classes(generic_app_models):
            if klass.objects.all().count() > 0:
                return False
        return True

    def get_list_of_non_empty_models(self, generic_app_models):
        count_of_objects_in_non_empty_models = {}
        for klass in self.get_classes(generic_app_models):
            c = klass.objects.all().count()
            if c > 0:
                count_of_objects_in_non_empty_models[str(klass)]=c
        return count_of_objects_in_non_empty_models