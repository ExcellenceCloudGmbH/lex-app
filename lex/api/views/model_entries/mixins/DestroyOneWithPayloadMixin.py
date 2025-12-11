# from: https://stackoverflow.com/a/52700398
from rest_framework import response, status


class DestroyOneWithPayloadMixin:
    """
    The default destroy methods of Django do not return anything.
    However, we want to send the deleted instance with the response.
    """

    def destroy(self, *args, **kwargs):
        from rest_framework import response, status
        
        instance = self.get_object()
        request = self.request
        
        # Check delete permission using new system
        can_delete = False
        try:
            if hasattr(instance, 'permission_delete'):
                from lex.core.models.base import UserContext
                user_context = UserContext.from_request(request, instance)
                can_delete = instance.permission_delete(user_context)
            elif hasattr(instance, 'can_delete'):
                can_delete = instance.can_delete(request)
            else:
                # Default to allow if no permission method
                can_delete = True
        except Exception:
            # Allow by default on permission check error
            can_delete = True
            
        if not can_delete:
            return response.Response({
                "message": f"You are not authorized to delete this record in {instance.__class__.__name__}"
            }, status=status.HTTP_400_BAD_REQUEST)
            
        serializer = self.get_serializer(instance)
        super().destroy(*args, **kwargs)
        return response.Response(serializer.data, status=status.HTTP_200_OK)