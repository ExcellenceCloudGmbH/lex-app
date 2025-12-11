import copy

import copy
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.process_admin.models.model_collection import ModelCollection


class ModelStructureObtainView(APIView):
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    # These must be set when wiring up the view:
    model_collection = None
    get_model_structure_func = None
    get_container_func = None

    def delete_restricted_nodes_from_model_structure(self, tree, request):
        """
        Recursively remove nodes the user cannot list. Uses the new permission system.
        """
        for key in list(tree.keys()):
            node = tree[key]
            # If it's a leaf node representing a model, check list permission
            if node.get("type") == "Model":
                try:
                    model_container = self.get_container_func(key)
                    # Instantiate the model to call its permission check method
                    model_instance = model_container.model_class()

                    # Check permission using new system if available
                    can_list = False
                    if hasattr(model_instance, 'permission_list'):
                        from lex.core.models.base import UserContext
                        user_context = UserContext.from_request(request, model_instance)
                        can_list = model_instance.permission_list(user_context)
                    elif hasattr(model_instance, "can_list"):
                        can_list = model_instance.can_list(request)
                    else:
                        # Default to allow if no permission method
                        can_list = True

                    if not can_list:
                        del tree[key]
                        continue

                except Exception as e:
                    # If we can't get a container or check permissions, remove it for safety
                    del tree[key]
                    continue

            # Recurse into children
            if "children" in node and isinstance(node["children"], dict):
                self.delete_restricted_nodes_from_model_structure(
                    node["children"], request
                )

    def get(self, request, *args, **kwargs):
        # 1) Copy the raw tree
        structure = copy.deepcopy(self.get_model_structure_func())

        # 2) Prune nodes the user is not authorized to see
        self.delete_restricted_nodes_from_model_structure(structure, request)

        # 3) Annotate with serializers (this logic remains the same)
        def annotate(subtree):
            for node_id, node in subtree.items():
                try:
                    container = self.get_container_func(node_id)
                except Exception:
                    container = None
                if container and hasattr(container, "serializers_map"):
                    node["available_serializers"] = list(
                        container.serializers_map.keys()
                    )
                children = node.get("children")
                if isinstance(children, dict):
                    annotate(children)

        annotate(structure)

        return Response(structure)


class ModelStylingObtainView(APIView):
    http_method_names = ["get"]
    model_collection: ModelCollection = None
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        user_dependent_model_styling = self.model_collection.model_styling.copy()
        for key in user_dependent_model_styling.keys():
            # FIXME remove try-catch
            try:
                container = self.model_collection.get_container(key).model_class

                if hasattr(
                    container, "modification_restriction"
                ):  # FIXME change these ugly calls of hasattr
                    # FIXME: this is only set if there is an entry in @user_dependent_model_styling for the model
                    #   if this is not the case (which mostly holds), then the restrictions are not transfered to the
                    #   frontend --> fix this via own route for modification_restriction (which is better anyway)
                    user_dependent_model_styling[key][
                        "can_read_in_general"
                    ] = container.modification_restriction.can_read_in_general(
                        user, violations=None
                    )
                    user_dependent_model_styling[key][
                        "can_modify_in_general"
                    ] = container.modification_restriction.can_modify_in_general(
                        user, violations=None
                    )
                    user_dependent_model_styling[key][
                        "can_create_in_general"
                    ] = container.modification_restriction.can_create_in_general(
                        user, violations=None
                    )
            except KeyError:
                # happens if key not in container
                pass

        return Response(user_dependent_model_styling)


class Overview(APIView):
    http_method_names = ["get"]
    HTML_reports = None

    def get(self, request, *args, **kwargs):
        user = request.user
        class_name = kwargs["report_name"]
        html_report_class = self.HTML_reports[class_name]
        html = html_report_class().get_html(user)
        return Response(html)


class ProcessStructure(APIView):
    http_method_names = ["get"]
    processes = None

    def get(self, request, *args, **kwargs):
        class_name = kwargs["process_name"]
        process_class = self.processes[class_name]
        process_structure = process_class().get_structure()
        return Response(process_structure)


# import copy
#
# import copy
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework.views import APIView
# from rest_framework_api_key.permissions import HasAPIKey
#
# from lex.process_admin.models.model_collection import ModelCollection
# from lex.api.views.authentication.KeycloakManager import KeycloakManager
#
#
# class ModelStructureObtainView(APIView):
#     http_method_names = ["get"]
#     permission_classes = [HasAPIKey | IsAuthenticated]
#
#     # These must be set when wiring up the view:
#     model_collection = None
#     get_model_structure_func = None
#     get_container_func = None
#
#     def get_access_rights_for_user(self, access_token):
#         """
#         Generates a detailed list of a user's permissions for all models, records, and fields.
#
#         Args:
#             access_token (str): The user's Keycloak access token.
#
#         Returns:
#             list: A list of permission dictionaries formatted for the frontend.
#         """
#         if not access_token:
#             return []
#
#         kc_manager = KeycloakManager()
#         uma_permissions = kc_manager.get_uma_permissions(access_token)
#
#         access_rights = []
#
#         # A dictionary to group permissions by resource and record
#         # Format: { 'resource_name': { 'record_id': {'scopes'}, 'model_level': {'scopes'} } }
#         grouped_perms = {}
#
#         for perm in uma_permissions:
#             resource_name = perm.get('rsname')
#             if not resource_name:
#                 continue
#
#             record_id = perm.get('resource_set_id')
#             scopes = set(perm.get('scopes', []))
#
#             if resource_name not in grouped_perms:
#                 grouped_perms[resource_name] = {}
#
#             if record_id:
#                 if record_id not in grouped_perms[resource_name]:
#                     grouped_perms[resource_name][record_id] = set()
#                 grouped_perms[resource_name][record_id].update(scopes)
#             else:
#                 if 'model_level' not in grouped_perms[resource_name]:
#                     grouped_perms[resource_name]['model_level'] = set()
#                 grouped_perms[resource_name]['model_level'].update(scopes)
#
#         # Process the grouped permissions into the final access_rights list
#         for resource, records in grouped_perms.items():
#             # Handle record-level permissions
#             for record_id, scopes in records.items():
#                 if record_id == 'model_level':
#                     continue
#                 for action in scopes:
#                     access_rights.append({
#                         "action": action,
#                         "resource": resource,
#                         "record": {"id": record_id}
#                     })
#
#             # Handle model-level permissions
#             if 'model_level' in records:
#                 for action in records['model_level']:
#                     access_rights.append({
#                         "action": action,
#                         "resource": resource
#                     })
#
#         return access_rights
#
#     def delete_restricted_nodes_from_model_structure(self, tree, request):
#         """
#         Recursively remove nodes the user cannot read based on Keycloak permissions.
#         """
#         for key in list(tree.keys()):
#             node = tree[key]
#             # If it's a leaf node representing a model, check read permission
#             if "children" not in node:
#                 try:
#                     # Get an instance of the model to check permissions against
#                     model_instance = self.get_container_func(key)
#
#                     # Temporarily attach the request to the context for the permission check
#                     # This is necessary for LexModel's permission methods to access the session
#                     from lex.api.utils.context import context_id
#                     context_id.set({"request_obj": request})
#                     temp = model_instance.model_class()
#
#                     if not temp.can_read():
#                         del tree[key]
#                         del temp
#                         continue
#                     del temp
#                 except Exception:
#                     # If we can't get a container or check permissions, remove it for safety
#                     del tree[key]
#                     continue
#
#             # Recurse into children
#             if "children" in node and isinstance(node["children"], dict):
#                 self.delete_restricted_nodes_from_model_structure(node["children"], request)
#
#     def get(self, request, *args, **kwargs):
#         # 1) Copy the raw tree
#         structure = copy.deepcopy(self.get_model_structure_func())
#
#
#         # 2) Prune nodes the user is not authorized to see
#         self.delete_restricted_nodes_from_model_structure(structure, request)
#
#         # 3) Annotate with serializers (unchanged)
#         def annotate(subtree):
#             for node_id, node in subtree.items():
#                 try:
#                     container = self.get_container_func(node_id)
#                 except Exception:
#                     container = None
#                 if container and hasattr(container, "serializers_map"):
#                     node["available_serializers"] = list(container.serializers_map.keys())
#                 children = node.get("children")
#                 if isinstance(children, dict):
#                     annotate(children)
#
#         annotate(structure)
#
#         return Response(structure)
#
#
