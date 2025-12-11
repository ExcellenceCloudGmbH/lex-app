import traceback
from datetime import datetime

from django.db import transaction
from django.db.models.signals import post_save
from rest_framework.exceptions import APIException
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin

from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import DestroyOneWithPayloadMixin
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin


class CreateOrUpdate(ModelEntryProviderMixin, DestroyOneWithPayloadMixin, RetrieveUpdateDestroyAPIView, CreateAPIView):
    def update(self, request, *args, **kwargs):

        from lex.core.calculated_updates import update_handler
        model_container = self.kwargs['model_container']
        instance = model_container.model_class.objects.filter(pk=self.kwargs["pk"]).first()
        try:
            if "next_step" in request.data:
                post_save.disconnect(update_handler)
            with transaction.atomic():
                if instance:
                    response = UpdateModelMixin.update(self, request, *args, **kwargs)
                else:
                    response = CreateModelMixin.create(self, request, *args, **kwargs)
        except Exception as e:
            print(e)
            raise APIException({"error": f"{e} ", "traceback": traceback.format_exc()})

        post_save.connect(update_handler)
        return response
