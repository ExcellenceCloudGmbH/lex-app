import os
import posixpath
from pathlib import Path

from django.http import HttpResponse
from django.utils._os import safe_join
from django.views.static import serve as static_serve

from lex.lex_app import settings


def serve_react(request, path, document_root=None):
    path = posixpath.normpath(path).lstrip("/")

    if path == "config.js":
        config_path = safe_join(document_root, path)
        with open(config_path, 'r') as file:
            content = file.read()

        # Replace placeholders with actual environment variable values
        replacements = {
            'undefined': {  # Only replace 'undefined' entries
                'REACT_APP_KEYCLOAK_REALM': os.getenv('KEYCLOAK_REALM'),
                'REACT_APP_KEYCLOAK_URL': os.getenv('KEYCLOAK_URL'),
                'REACT_APP_KEYCLOAK_CLIENT_ID': os.getenv('KEYCLOAK_CLIENT_ID'),
                'REACT_APP_STORAGE_TYPE': os.getenv('STORAGE_TYPE', "LEGACY"),
                'REACT_APP_DOMAIN_BASE': os.getenv("REACT_APP_DOMAIN_BASE", "localhost"),
                'REACT_APP_PROJECT_DISPLAY_NAME': os.getenv('PROJECT_DISPLAY_NAME', settings.repo_name),
                'REACT_APP_GRAFANA_DASHBOARD_URL': os.getenv("REACT_APP_GRAFANA_DASHBOARD_URL", "localhost"),
            }
        }

        for key, value in replacements['undefined'].items():
            content = content.replace(f"window.{key} = undefined", f"window.{key} = \"{value}\"")

        response = HttpResponse(content, content_type='application/javascript')
        # Set no-cache headers
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response

    fullpath = Path(safe_join(document_root, path))
    if fullpath.is_file():
        response = static_serve(request, path, document_root)
        # Set no-cache headers for static files
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response
    else:
        response = static_serve(request, "index.html", document_root)
        # Set no-cache headers for index.html
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response