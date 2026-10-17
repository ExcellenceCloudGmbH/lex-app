import traceback
from contextlib import nullcontext

from django.db import transaction
from django.db.models.signals import post_save
from lex.api.errors import error_detail, raise_or_return, surface
from lex.api.security import create_allowed, edit_denial, is_readable, strict_input_denial
from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import DestroyOneWithPayloadMixin
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
from lex.core.models.LexModel import should_use_atomic_model_operations
from lex.core.security_flags import strict
from rest_framework.exceptions import APIException, NotFound
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin
from rest_framework.response import Response


class CreateOrUpdate(ModelEntryProviderMixin, DestroyOneWithPayloadMixin, RetrieveUpdateDestroyAPIView, CreateAPIView):
    def update(self, request, *args, **kwargs):

        from lex.core.calculated_updates import update_handler
        model_container = self.kwargs['model_container']
        instance = model_container.model_class.objects.filter(pk=self.kwargs["pk"]).first()
        # The same checks as the single-record endpoint (LEX_SEC_NOT_FOUND / LEX_SEC_EDIT):
        # run_step writes rows too, so it may not bypass them.
        if instance is not None and strict("NOT_FOUND") and not is_readable(request, instance):
            raise NotFound()
        if instance is not None:
            denial = edit_denial(request, instance, request.data, serializer=self.get_serializer(instance))
            if denial is not None:
                return raise_or_return(denial)
        elif strict("EDIT") and not create_allowed(request, model_container.model_class):
            detail = f"You may not create a {model_container.model_class.__name__}."
            return raise_or_return(
                Response({"code": "CREATE_NOT_ALLOWED", "detail": detail, "error": detail}, status=400)
            )
        # Unknown fields are refused here as on the single-record endpoint (lex_strict_input).
        strict_denial = strict_input_denial(model_container.model_class, self.get_serializer(instance), request.data)
        if strict_denial is not None:
            return raise_or_return(strict_denial)
        try:
            if "next_step" in request.data:
                post_save.disconnect(update_handler)
            atomic_target = instance or model_container.model_class
            atomic_context = (
                transaction.atomic()
                if should_use_atomic_model_operations(atomic_target)
                else nullcontext()
            )
            with atomic_context:
                if instance:
                    response = UpdateModelMixin.update(self, request, *args, **kwargs)
                else:
                    response = CreateModelMixin.create(self, request, *args, **kwargs)
        except Exception as e:
            surface(e)
            raise APIException(error_detail({"error": f"{e} ", "traceback": traceback.format_exc()}))

        post_save.connect(update_handler)
        return response
