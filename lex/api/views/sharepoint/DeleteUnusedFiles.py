from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey
from django.http import JsonResponse
from django_sharepoint_storage.SharePointContext import SharePointContext
from django_sharepoint_storage.SharePointCloudStorageUtils import get_server_relative_path
import os
from pathlib import Path
from typing import Set
from lex.lex_app import settings
from django.db.models.fields.files import FileField
import platform
from django.db import connection


DB_NAME = connection.settings_dict['NAME']


class DeleteUnusedFiles(APIView):
    http_method_names = ['post']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def print_failure(self, retry_number, ex):
        shrp_ctx = SharePointContext()
        print(f"{retry_number}: {ex}")
        if retry_number == 5:
            shrp_ctx.ctx._queries.pop()
            raise ex

    def cleanup_unused_files(self, dry_run: bool = True):
        """
        Deletes all unused media files or lists them if dry_run is True.
        :param dry_run: If True, list files to be deleted without deleting them.
        :return: A dictionary with details of the operation.
        """

        # Collect all files in MEDIA_ROOT
        all_files = set()
        shrp_ctx = SharePointContext()
        folder_path = f"Shared Documents/{os.getenv('DEPLOYMENT_ENVIRONMENT', 'LOCAL')}-{os.getenv('K8S_NAMESPACE', 'ENV')}/{os.getenv('KEYCLOAK_INTERNAL_CLIENT_ID', 'Local')}/{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', f'{platform.node()}/{DB_NAME}')}/uploads"
        folder = shrp_ctx.ctx.web.get_folder_by_server_relative_url(folder_path).execute_query()
        files = folder.get_files(recursive=True).execute_query()

        for file in files:
            all_files.add(file.serverRelativeUrl)

        referenced_files = self.get_referenced_files()
        # Find unused files
        unused_files = all_files - referenced_files

        if dry_run:
            return {
                "status": "success",
                "dry_run": True,
                "unused_files_count": len(unused_files),
                "unused_files": [str(file) for file in unused_files],
                "referenced_files_count": len(referenced_files),
                "referenced_files": [str(file) for file in referenced_files],
            }
        else:
            deleted_files = []
            failed_files = []
            for file in unused_files:
                try:
                    file_obj = shrp_ctx.ctx.web.get_file_by_server_relative_path(file).execute_query_retry(max_retry=5, timeout_secs=5,
                                                                        failure_callback=self.print_failure)
                    file_obj.recycle().execute_query_retry(max_retry=5, timeout_secs=5,
                                                  failure_callback=self.print_failure)
                    deleted_files.append(str(file))
                except Exception as e:
                    failed_files.append({"file": str(file), "error": str(e)})

            return {
                "status": "success",
                "dry_run": False,
                "deleted_files": deleted_files,
                "failed_files": failed_files,
            }

    def get_referenced_files(self) -> Set[Path]:
        """
        Collect all files referenced in the database.
        Returns a set of Paths to referenced files.
        """
        referenced_files = set()
        from django.apps import apps

        models = set(apps.get_app_config(settings.repo_name).models.values())

        for model in models:
            for field in model._meta.fields:
                if isinstance(field, FileField):
                    for instance in model.objects.all().iterator():
                        file_field = getattr(instance, field.name)
                        if file_field and file_field.name:
                            file_path = get_server_relative_path(file_field.url)
                            referenced_files.add(file_path)

        return referenced_files

    def post(self, request, *args, **kwargs):
        dry_run = request.data.get('dry_run', True)
        if not isinstance(dry_run, bool):
            return JsonResponse({"status": "error", "message": "'dry_run' must be a boolean."}, status=400)

        result = self.cleanup_unused_files(dry_run=dry_run)
        return JsonResponse(result)