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
from urllib.parse import unquote, urlparse


DB_NAME = connection.settings_dict["NAME"]


class DeleteUnusedFiles(APIView):
    http_method_names = ["post"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    def print_failure(self, retry_number, ex):
        print(f"{retry_number}: {ex}")
        if retry_number == 15:
            raise ex

    def normalize_sp_path(self, path: str) -> str:
        """
        Normalize SharePoint paths so both DB references and SharePoint API results
        are compared in the same decoded, server-relative form.
        """
        if not path:
            return path

        path = str(path)

        # If a full URL somehow comes in, strip host and keep only the path
        if path.startswith("http://") or path.startswith("https://"):
            path = urlparse(path).path

        # SharePoint path APIs want decoded server-relative paths
        path = unquote(path)

        # Force server-relative form
        if not path.startswith("/"):
            path = "/" + path

        return path

    def cleanup_unused_files(self, dry_run: bool = True):
        """
        Deletes all unused media files or lists them if dry_run is True.
        """

        shrp_ctx = SharePointContext()

        folder_path = (
            f"Shared Documents/"
            f"{os.getenv('DEPLOYMENT_ENVIRONMENT', 'LOCAL')}-"
            f"{os.getenv('K8S_NAMESPACE', 'ENV')}/"
            f"{os.getenv('KEYCLOAK_INTERNAL_CLIENT_ID', 'Local')}/"
            f"{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', f'{platform.node()}/{DB_NAME}')}/uploads"
        )

        folder = shrp_ctx.ctx.web.get_folder_by_server_relative_url(folder_path).execute_query()
        files = folder.get_files(recursive=True).execute_query()

        # Keep actual File objects instead of only string paths
        files_by_path = {}
        for sp_file in files:
            normalized = self.normalize_sp_path(sp_file.serverRelativeUrl)
            files_by_path[normalized] = sp_file

        referenced_files = self.get_referenced_files()

        unused_paths = set(files_by_path.keys()) - referenced_files

        if dry_run:
            return {
                "status": "success",
                "dry_run": True,
                "unused_files_count": len(unused_paths),
                "unused_files": sorted(unused_paths),
                "referenced_files_count": len(referenced_files),
                "referenced_files": sorted(referenced_files),
            }

        deleted_files = []
        failed_files = []

        for path in unused_paths:
            sp_file = files_by_path[path]
            try:
                # Recycle the already-resolved File object directly
                sp_file.recycle()
                shrp_ctx.ctx.execute_query_retry(
                    max_retry=15,
                    timeout_secs=5,
                    failure_callback=self.print_failure,
                )
                deleted_files.append(path)
            except Exception as e:
                failed_files.append({"file": path, "error": str(e)})

        return {
            "status": "success",
            "dry_run": False,
            "deleted_files": deleted_files,
            "failed_files": failed_files,
        }

    def get_referenced_files(self) -> Set[str]:
        """
        Collect all files referenced in the database.
        Returns a set of normalized server-relative SharePoint paths.
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
                            referenced_files.add(self.normalize_sp_path(file_path))

        return referenced_files

    def post(self, request, *args, **kwargs):
        dry_run = request.data.get("dry_run", True)
        if not isinstance(dry_run, bool):
            return JsonResponse(
                {"status": "error", "message": "'dry_run' must be a boolean."},
                status=400,
            )

        result = self.cleanup_unused_files(dry_run=dry_run)
        return JsonResponse(result)