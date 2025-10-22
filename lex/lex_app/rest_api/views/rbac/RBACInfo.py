# from rest_framework.permissions import IsAuthenticated
# from django.http import JsonResponse
# from rest_framework.views import APIView
# from rest_framework_api_key.permissions import HasAPIKey
# from lex.lex_app.auth_helpers import get_user_info, get_tokens_and_permissions
# from django.apps import apps
# from lex.lex_app import settings
# import requests
#
#
# class RBACInfo(APIView):
#     http_method_names = ['get']
#     permission_classes = [HasAPIKey | IsAuthenticated]
#
#     def get(self, request, *args, **kwargs):
#         from lex.lex_app.ProcessAdminSettings import processAdminSite
#
#         # # Replace with the actual base URL of your server
#         # base_url = 'https://melihsunbul.excellence-cloud.dev'
#         #
#         # # Replace with the actual keycloak_internal_client_id you want to use
#         # keycloak_internal_client_id = '<keycloak_internal_client_id>'
#         #
#         # # Construct the full URL
#         # url = f"{base_url}/api/clients/{keycloak_internal_client_id}/roles"
#         #
#         #
#         # headers = {
#         #     'Authorization': f'Api-Key <Api-Key>',
#         # }
#         #
#         # # Send the GET request
#         # response = requests.get(url, headers=headers)
#         #
#         # # Check if the request was successful
#         # if response.status_code == 200:
#         #     # Parse and print the response (assuming it's in JSON format)
#         #     roles = response.json()
#         #     print(roles)
#         # else:
#         #     print(f"Failed to retrieve roles. Status code: {response.status_code}")
#         #     print(response.text)
#
#         role_definitions = {
#             "admin": [{"action": "*", "resource": "*"}],
#             "standard": [{"action": ["edit", "show", "export", "read", "list"], "resource": "*"}],
#             "view-only": [{"action": ["show", "read", "list"], "resource": "*"}],
#         }
#
#         user_roles = ["admin"]
#         user_permissions = []
#         project_models = [model for model in
#                               set(apps.get_app_config(settings.repo_name).models.values())]
#
#         for model in project_models:
#             try:
#                 user_permissions.append(model.evaluate_rbac(request.user))
#             except Exception as e:
#                 pass
#         # return JsonResponse(get_user_info(request))
#         return JsonResponse({"user": get_user_info(request)["user"], "role_definitions": role_definitions, "user_roles": user_roles, "user_permissions": user_permissions})