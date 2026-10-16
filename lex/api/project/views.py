"""Base classes for a project's own REST endpoints.

A project mounts its endpoints with ``lex_config.API_URLCONF`` under
``lex_config.API_PREFIX`` and builds them on these classes, so they get lex's
authentication, audit context, transaction handling, error format and read
permissions without re-implementing them::

    from lex.api.project import LexAPIView, LexActionError

    class SubmitView(LexAPIView):
        def post(self, request, uuid):
            instance = self.get_object_or_404(ProcessInstance, uuid=uuid)
            ...
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from contextlib import nullcontext
from typing import Any, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from lex.api.utils.Context import OperationContext


def visible_queryset(model_or_queryset, request):
    """The rows of a model (or queryset) the user behind ``request`` may read.

    Applies the same read rule as lex's generic list endpoint (Keycloak scopes
    or the model's ``permission_read``).
    """
    from lex.api.views.model_entries.filter_backends import apply_read_restriction

    if isinstance(model_or_queryset, type):
        queryset = model_or_queryset._default_manager.all()
    else:
        queryset = model_or_queryset
    return apply_read_restriction(queryset, request)


def strict_serializer(serializer_class):
    """Make a DRF serializer reject keys it does not declare (400).

    Usable as a class decorator. Rejecting unknown input keeps a client from
    sending fields the server sets itself, such as an actor or a timestamp.
    """

    original = serializer_class.to_internal_value

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({key: ["Unknown field."] for key in unknown})
        return original(self, data)

    serializer_class.to_internal_value = to_internal_value
    serializer_class.lex_strict_input = True
    return serializer_class


class LexAPIView(APIView):
    """A project endpoint with lex's guarantees.

    * ``public = False`` – sign-in required; ``True`` makes the path answer
      without sign-in (lex's login check skips it) while still recognising a
      signed-in caller.
    * ``ensure_csrf = False`` – ``True`` sets the CSRF cookie on the response,
      for a session bootstrap endpoint.
    * ``atomic = True`` – the handler runs in one database transaction.
    * every request runs inside lex's ``OperationContext`` (audit logging).
    * errors use lex's format; database errors go through
      ``lex_config.DB_ERROR_MAPPER``.
    * :meth:`get_object_or_404` applies read permission: a record the user may
      not read is "not found".
    """

    public: bool = False
    ensure_csrf: bool = False
    atomic: bool = True

    lex_structured_errors = True
    lex_maps_db_errors = True

    def get_permissions(self):
        if self.public:
            return [AllowAny()]
        if self.permission_classes == APIView.permission_classes:
            return [IsAuthenticated()]
        return super().get_permissions()

    @classmethod
    def as_view(cls, **initkwargs):
        view = super().as_view(**initkwargs)
        if cls.ensure_csrf:
            view = ensure_csrf_cookie(view)
        view.lex_public = cls.public
        return view

    @property
    def user_context(self):
        from lex.core.models.LexModel import UserContext

        return UserContext.from_request_base(self.request)

    def get_object_or_404(self, model_or_queryset, **lookup):
        queryset = visible_queryset(model_or_queryset, self.request)
        try:
            return queryset.get(**lookup)
        except (queryset.model.DoesNotExist, ValueError, TypeError) as exc:
            raise Http404 from exc

    def dispatch(self, request, *args, **kwargs):
        calculation_id = f"api_{uuid.uuid4().hex}"
        atomic = transaction.atomic() if self.atomic else nullcontext()
        with OperationContext(request, calculation_id):
            with atomic:
                response = super().dispatch(request, *args, **kwargs)
                # DRF turns exceptions into error responses inside dispatch; an
                # error answer must not commit what the handler wrote so far.
                if self.atomic and getattr(response, "exception", False):
                    transaction.set_rollback(True)
                return response


def verify_hmac_sha256(body: bytes, signature: Optional[str], secret: str, *, prefix: str = "") -> bool:
    """Constant-time check of a hex HMAC-SHA256 signature over ``body``.

    ``prefix`` is stripped from the header value first (e.g. ``"sha256="``).
    """
    if not signature or not secret:
        return False
    if prefix and signature.startswith(prefix):
        signature = signature[len(prefix):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@method_decorator(csrf_exempt, name="dispatch")
class LexWebhookView(LexAPIView):
    """A server-to-server callback: public, no session or token, CSRF-exempt.

    The caller proves itself with a signature over the raw body (see
    :func:`verify_hmac_sha256`); ``self.raw_body`` holds the bytes exactly as
    received. Set ``service_user`` to the username the webhook acts as: it
    becomes ``request.user`` (audit actor, database context).
    """

    public = True
    authentication_classes: list = []
    service_user: Optional[str] = None

    def initial(self, request, *args: Any, **kwargs: Any):
        self.raw_body = request._request.body
        if self.service_user:
            user = get_user_model()._default_manager.filter(username=self.service_user).first()
            if user is None:
                from django.core.exceptions import ImproperlyConfigured

                raise ImproperlyConfigured(
                    f"{type(self).__name__}.service_user {self.service_user!r} does not exist"
                )
            request._request.user = user
            request.user = user
        super().initial(request, *args, **kwargs)
