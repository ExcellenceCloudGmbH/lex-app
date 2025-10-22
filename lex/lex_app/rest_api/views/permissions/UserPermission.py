import inspect

from rest_framework.permissions import BasePermission

READ_METHODS = {'GET'}
CREATE_METHODS = {'POST'}
MODIFY_METHODS = {'PUT', 'PATCH'}
DELETE_METHOD = 'DELETE'


def get_permission_denied_message(access_type, requested_unit, violations):
    details = ''
    if violations:
        details = f' Violations: {", ".join(violations)}'
    return f'You do not have general {access_type}-access to the requested {requested_unit}.{details}'


class UserPermission(BasePermission):
    """
    This permission class ensures, that only certain users can perform specific operations on the data.
    Important hint: one might think that through the error messages, information is revealed to an unauthorized
    user such as the existence of a certain model. But: the error message can only be seen by someone that
    is authenticated and in one of the authorized groups("azure_groups"). Therefore, providing there error messages
    is ok, and might be very helpful for some user unsuccessfully trying to perform certain operations.
    """

    message = None

    # TODO: this class can easily extended to also consider permissions set via Django admin

    def has_permission(self, request, view):
        model_container = view.kwargs['model_container']
        user = request.user
        modification_restriction = model_container.get_modification_restriction()

        if request.method in READ_METHODS:
            violations = []
            if modification_restriction.can_read_in_general(user, violations):
                return True
            self.message = get_permission_denied_message('read', 'model', violations)
            return False

        if request.method in MODIFY_METHODS:
            violations = []
            if modification_restriction.can_modify_in_general(user, violations):
                return True
            self.message = get_permission_denied_message('modify', 'model', violations)
            return False

        if request.method in CREATE_METHODS:
            violations = []
            if modification_restriction.can_create_in_general(user, violations):
                return True
            self.message = get_permission_denied_message('create', 'model', violations)
            return False

        if request.method == DELETE_METHOD:
            violations = []
            if modification_restriction.can_delete_in_general(user, violations):
                return True
            self.message = get_permission_denied_message('delete', 'model', violations)
            return False

        raise ValueError(f'unknow http method {request.method}')

    def has_object_permission(self, request, view, obj):
        model_container = view.kwargs['model_container']
        user = request.user
        modification_restriction = model_container.get_modification_restriction()

        if request.method in READ_METHODS:
            violations = []
            if modification_restriction.can_be_read(obj, user, violations):
                return True
            self.message = get_permission_denied_message('read', 'instance', violations)
            return False

        if request.method in MODIFY_METHODS:
            violations = []
            if 'request_data' in inspect.signature(modification_restriction.can_be_modified).parameters:
                if modification_restriction.can_be_modified(obj, user, violations, request.data):
                    return True
            else:
                if modification_restriction.can_be_modified(obj, user, violations):
                    return True
            self.message = get_permission_denied_message(obj, user, violations)
            return False

        if request.method in CREATE_METHODS:
            return True

        if request.method == DELETE_METHOD:
            violations = []
            if 'request_data' in inspect.signature(modification_restriction.can_be_deleted).parameters:
                if modification_restriction.can_be_deleted(obj, user, violations, request.data):
                    return True
            else:
                if modification_restriction.can_be_deleted(obj, user, violations):
                    return True
            self.message = get_permission_denied_message('delete', 'instance', violations)
            return False

        raise ValueError(f'unknow http method {request.method}')
