import inspect
import io
import json
import os
import threading
from pathlib import Path
from unittest import TestCase

import dateutil.parser
from django.apps import apps
from django.core.cache import cache
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import models

from lex.api.utils import OperationContext
from lex.audit_logging.utils.config import is_audit_logging_enabled
from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.models.calculation_log import CalculationLog
from lex.audit_logging.utils.model_context import model_logging_context

from lex.lex_app import settings
from lex.lex_app.celery_tasks import lex_shared_task


class ProcessAdminTestCase(TestCase):

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
                    
                    # Log audit entry before creation if audit logging is enabled
                    if audit_enabled:
                        audit_log = audit_logger.log_object_creation(klass, object['parameters'], tag)
                    
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
                    self.tagged_objects[tag].save()

                    calculation_id = audit_log.calculation_id if audit_enabled else None

                    instance = self.tagged_objects[tag]
                    if audit_enabled and CalculationLog.objects.filter(calculationId=calculation_id).count() < 0:
                        audit_log.calculation_id = None
                        audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
                        audit_log.save()
                    # Mark audit log as successful if audit logging is enabled
                    if audit_enabled and audit_log:
                        audit_logger.mark_operation_success(audit_log)
                        
                elif action == 'update':
                    object['filter_parameters'] = self.replace_tagged_parameters(object['filter_parameters'])
                    self.tagged_objects[tag] = klass.objects.filter(**object['filter_parameters']).first()
                    if self.tagged_objects[tag] is not None:
                        # Log audit entry before update if audit logging is enabled
                        if audit_enabled:
                            audit_log = audit_logger.log_object_update(klass, self.tagged_objects[tag], object['parameters'], tag)
                        
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

                        calculation_id = audit_log.calculation_id if audit_enabled else None
                        with OperationContext({}, calculation_id):
                            self.tagged_objects[tag].save()
                        instance = self.tagged_objects[tag]
                        # Mark audit log as successful if audit logging is enabled
                        if audit_enabled and CalculationLog.objects.filter(calculationId=calculation_id).count() < 0:
                            audit_log.calculation_id = None
                            audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
                            audit_log.save()
                        if audit_enabled and audit_log:
                            audit_logger.mark_operation_success(audit_log)
                            
                elif action == 'delete':
                    # Log audit entry before deletion if audit logging is enabled
                    if audit_enabled:
                        audit_log = audit_logger.log_object_deletion(klass, object['filter_parameters'], tag)
                    
                    klass.objects.filter(**object['filter_parameters']).delete()
                    
                    # Mark audit log as successful if audit logging is enabled
                    if audit_enabled and audit_log:
                        audit_logger.mark_operation_success(audit_log)
                        
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
                    
                    # Log audit entry before creation if audit logging is enabled
                    calculation_id = audit_logger.generate_calculation_id()
                    if audit_enabled:
                        audit_log = audit_logger.log_object_creation(klass, object['parameters'], tag, None)
                    
                    self.tagged_objects[tag] = klass(**object['parameters'])
                    cache.set(threading.get_ident(), str(object['class']) + "_" + action)

                    if "Investor" in klass.__name__ and "Track" in klass.__name__:
                        print("Break here")

                    with OperationContext({}, calculation_id, audit_log=audit_log):
                        with model_logging_context(self.tagged_objects[tag]):
                            self.tagged_objects[tag].save()

                    instance = self.tagged_objects[tag]
                    # Mark audit log as successful if audit logging is enabled


                    if audit_enabled:
                        if CalculationLog.objects.filter(calculationId=calculation_id).count() < 0:
                            audit_log.calculation_id = None
                        audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
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
                        # Log audit entry before update if audit logging is enabled
                        calculation_id = audit_logger.generate_calculation_id()
                        if audit_enabled:
                            audit_log = audit_logger.log_object_update(klass, self.tagged_objects[tag], object['parameters'], tag, None)
                        
                        for key in object['parameters']:
                            setattr(self.tagged_objects[tag], key, object['parameters'][key])

                        cache.set(threading.get_ident(),
                                  str(object['class']) + "_" + action + "_" + str(self.tagged_objects[tag].pk))

                        with OperationContext({}, calculation_id, audit_log=audit_log):
                            with model_logging_context(self.tagged_objects[tag]):
                                self.tagged_objects[tag].save()

                        instance = self.tagged_objects[tag]
                        if audit_enabled:
                            if CalculationLog.objects.filter(calculationId=calculation_id).count() < 0:
                                audit_log.calculation_id = None
                            audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
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
                            audit_log.content_type = ContentType.objects.get_for_model(klass)
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