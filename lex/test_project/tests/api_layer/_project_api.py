"""A small project API used as ``lex_config.API_URLCONF`` by clusters 10o–10q.

Underscore-prefixed so lex's model discovery skips it, as it skips a real
project's ``_api`` package.
"""
from __future__ import annotations

from django.db import DatabaseError
from django.urls import path
from rest_framework import serializers
from rest_framework.response import Response

from lex.api.project import (
    LexActionError,
    LexAPIView,
    LexWebhookView,
    strict_serializer,
    verify_hmac_sha256,
)

from .models import ApiProjectItem

WEBHOOK_SECRET = "cluster-10o-secret"


class WhoAmIView(LexAPIView):
    """Requires sign-in; echoes the caller."""

    def get(self, request):
        return Response({"username": request.user.username})


class PingView(LexAPIView):
    """Public; tells whether a signed-in caller was recognised."""

    public = True

    def get(self, request):
        return Response({"authenticated": bool(request.user and request.user.is_authenticated)})


class BootstrapView(LexAPIView):
    """Public session bootstrap that sets the CSRF cookie."""

    public = True
    ensure_csrf = True

    def get(self, request):
        return Response({"ok": True})


class ListedOnlyView(LexAPIView):
    """Public only because PUBLIC_PATHS lists it (``public`` stays False)."""

    permission_classes = []

    def get(self, request):
        return Response({"listed": True})


class CreateThenFailView(LexAPIView):
    """Writes a row, then fails with a business error: the write must roll back."""

    def post(self, request):
        ApiProjectItem.objects.create(name=request.data.get("name", "temporary"))
        raise LexActionError("Not today.", code="NOT_TODAY", fields={"name": "Rejected."}, status_code=409)


class ItemDetailView(LexAPIView):
    """Looks a row up through read permission."""

    def get(self, request, pk):
        item = self.get_object_or_404(ApiProjectItem, pk=pk)
        return Response({"id": item.pk, "name": item.name})


class DatabaseFailureView(LexAPIView):
    def get(self, request):
        raise DatabaseError("cluster10: trigger said no")


class CrashView(LexAPIView):
    def get(self, request):
        raise RuntimeError("cluster10: unexpected")


@strict_serializer
class RenameInput(serializers.Serializer):
    name = serializers.CharField()


class RenameView(LexAPIView):
    def post(self, request):
        data = RenameInput(data=request.data)
        data.is_valid(raise_exception=True)
        return Response({"name": data.validated_data["name"]})


class SignedWebhookView(LexWebhookView):
    service_user = "cluster10_webhook"

    def post(self, request):
        if not verify_hmac_sha256(self.raw_body, request.headers.get("X-Signature"), WEBHOOK_SECRET, prefix="sha256="):
            raise LexActionError("Bad signature.", code="BAD_SIGNATURE", status_code=401)
        return Response({"actor": request.user.username, "bytes": len(self.raw_body)})


urlpatterns = [
    path("whoami", WhoAmIView.as_view()),
    path("ping", PingView.as_view()),
    path("bootstrap", BootstrapView.as_view()),
    path("listed", ListedOnlyView.as_view()),
    path("create-then-fail", CreateThenFailView.as_view()),
    path("items/<int:pk>", ItemDetailView.as_view()),
    path("db-failure", DatabaseFailureView.as_view()),
    path("crash", CrashView.as_view()),
    path("rename", RenameView.as_view()),
    path("hooks/signed", SignedWebhookView.as_view()),
]
