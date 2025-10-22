# from: https://stackoverflow.com/a/52700398
from rest_framework import response, status


class DestroyOneWithPayloadMixin:
    """
    The default destroy methods of Django do not return anything.
    However, we want to send the deleted instance with the response.
    """

    def destroy(self, *args, **kwargs):

        instance = self.get_object()
        if not instance.can_delete({}):
            return response.Response({
                    "message": f"You are not authorized to this record in {instance.__class__.__name__}"
                },status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(instance)
        super().destroy(*args, **kwargs)
        return response.Response(serializer.data, status=status.HTTP_200_OK)