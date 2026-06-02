"""
Cluster 4f: Serializer-level permission machinery.

Targets ``lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin`` —
the write-side permission hook that sits in front of every PATCH/POST.
Baseline coverage before this sub-cluster: **9.33%**.

Intent (from the mixin docstring + docs/features/permissions/):

    The mixin injects field-level permission checks into DRF validation
    with four guarantees a customer/integration can reasonably expect:

      1. **camelCase → snake_case translation.** The frontend posts
         ``{"reportDate": ...}``; the backend model declares
         ``report_date``. The mixin must translate before looking
         fields up, so a valid PATCH isn't rejected as "unknown field".
      2. **Non-editable fields are ignored.** Primary keys and
         ``editable=False`` columns never participate in the permission
         check — even if the frontend sends them, the mixin skips them.
      3. **The decorator preserves identity.** ``add_permission_checks``
         wraps a serializer class but keeps ``__name__`` / ``__module__``
         so error messages, registrations, and repr still show the
         original class.
      4. **The metaclass auto-applies the mixin to LexModel
         serializers** but leaves non-LexModel serializers alone. This
         is how the whole codebase gets permission-aware serializers
         without every `class Meta: model = Foo` needing to opt in.

Why unit-level, not end-to-end: the PATCH path that *uses* this mixin
is already covered by 4b (4.4/4.5/4.6, xfailed against BUG-008/010).
Those tests assert the customer-visible *outcome* — they don't reach
into the mixin internals because the current bug short-circuits the
flow. These scenarios assert the **guarantees the mixin itself makes**,
so that when BUG-010 is fixed the underlying machinery is already
proven correct.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 4f.
"""

from __future__ import annotations

import unittest

from django.contrib.auth import get_user_model
from django.db import models
from django.test import SimpleTestCase, TestCase
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import (
    PermissionAwareSerializerMixin,
    PermissionAwareSerializerMetaclass,
    _camel_to_snake,
    add_permission_checks,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from .models import FieldLevelItem, ProtectedItem

import pytest

pytestmark = pytest.mark.permissions


# --------------------------------------------------------------------
# 4.19 — camelCase → snake_case translation
# --------------------------------------------------------------------
class TestCluster04f_CamelToSnake(SimpleTestCase):
    """Scenario 4.19: ``_camel_to_snake`` must match the frontend convention."""

    # -- 4.19 ----------------------------------------------------------
    def test_4_19_camel_to_snake_contract(self) -> None:
        """
        The conversion must be deterministic for every shape the
        frontend sends, and must be a no-op for snake_case strings so
        mixed payloads survive round-trip.
        """
        cases = [
            # (input, expected) — each is a real shape from the UI
            ("reportDate",        "report_date"),
            ("simpleName",        "simple_name"),
            ("already_snake",     "already_snake"),      # no-op
            ("lowercase",         "lowercase"),          # no-op
            ("twoWordsHere",      "two_words_here"),
            ("htmlURL",           "html_url"),           # trailing acronym
            ("URLPath",           "url_path"),           # leading acronym
            ("someHTTPResponse",  "some_http_response"), # acronym mid-word
            ("",                  ""),                   # empty is safe
        ]
        for raw, expected in cases:
            with self.subTest(input=raw):
                self.assertEqual(
                    _camel_to_snake(raw), expected,
                    msg=f"_camel_to_snake({raw!r}) → expected {expected!r}",
                )


# --------------------------------------------------------------------
# 4.20 — Non-editable fields are recognised
# --------------------------------------------------------------------
class FakeNonEditableModel(models.Model):
    """Synthetic model with a mix of editable and non-editable fields."""
    open_field = models.CharField(max_length=50)
    system_field = models.CharField(max_length=50, editable=False, default="")
    locked_int = models.IntegerField(editable=False, default=0)

    class Meta:
        app_label = "lex_app"
        managed = False


class FakeNonEditableSerializer(PermissionAwareSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = FakeNonEditableModel
        fields = "__all__"


class TestCluster04f_NonEditableFields(SimpleTestCase):
    """Scenario 4.20: system-managed + pk fields are excluded from the permission check set."""

    # -- 4.20 ----------------------------------------------------------
    def test_4_20_non_editable_fields_includes_pk_and_editable_false(self) -> None:
        """
        The set returned by ``_get_non_editable_fields`` must contain
        **at least** the primary key and every column declared
        ``editable=False``. If this contract drifts, the mixin will
        either start rejecting legitimate PATCHes (false 403s on
        ``id``) or skip fields that should be rejected.
        """
        s = FakeNonEditableSerializer()
        non_editable = s._get_non_editable_fields()

        self.assertIn(
            "id", non_editable,
            "Primary key must be treated as non-editable — PATCH with "
            "{'id': ...} must not cascade into a permission check",
        )
        self.assertIn(
            "system_field", non_editable,
            "`editable=False` char field must be non-editable",
        )
        self.assertIn(
            "locked_int", non_editable,
            "`editable=False` int field must be non-editable",
        )
        self.assertNotIn(
            "open_field", non_editable,
            "Regular editable field must NOT appear in the non-editable "
            "set — otherwise PATCH on open_field is silently skipped",
        )


# --------------------------------------------------------------------
# 4.21 — `add_permission_checks` decorator preserves identity
# --------------------------------------------------------------------
class UnwrappedSerializer(serializers.ModelSerializer):
    """A plain serializer with no mixin — the decorator's input."""
    class Meta:
        model = FakeNonEditableModel
        fields = "__all__"


class TestCluster04f_DecoratorContract(SimpleTestCase):
    """Scenario 4.21: ``add_permission_checks`` wraps a serializer transparently."""

    # -- 4.21 ----------------------------------------------------------
    def test_4_21_decorator_injects_mixin_and_preserves_identity(self) -> None:
        """
        Two guarantees:

        * The returned class **is** a ``PermissionAwareSerializerMixin``
          subclass (so DRF validation runs through the permission hook).
        * ``__name__`` and ``__module__`` match the original so log
          lines, error messages, and any framework machinery that keys
          off the class name keep working. If this drifts, user-facing
          error traces will suddenly show ``PermissionAwareVersion``
          everywhere.
        """
        wrapped = add_permission_checks(UnwrappedSerializer)

        self.assertTrue(
            issubclass(wrapped, PermissionAwareSerializerMixin),
            "add_permission_checks must return a PermissionAware subclass",
        )
        self.assertTrue(
            issubclass(wrapped, UnwrappedSerializer),
            "Wrapped class must still be a subclass of the original",
        )
        self.assertEqual(
            wrapped.__name__, UnwrappedSerializer.__name__,
            msg=(
                "__name__ must be preserved so logs/errors show the "
                f"user-recognisable class name; got {wrapped.__name__!r}"
            ),
        )
        self.assertEqual(
            wrapped.__module__, UnwrappedSerializer.__module__,
            msg="__module__ must be preserved for import/registration keys",
        )


# --------------------------------------------------------------------
# 4.22 — The metaclass auto-applies the mixin to LexModel serializers
# --------------------------------------------------------------------
class TestCluster04f_Metaclass(TestCase):
    """
    Scenario 4.22: ``PermissionAwareSerializerMetaclass`` injects the
    mixin iff the model has ``can_read`` + ``can_edit`` (the LexModel
    signature). Non-LexModel serializers must remain untouched.
    """

    # -- 4.22 ----------------------------------------------------------
    def test_4_22_metaclass_injects_for_lexmodel_only(self) -> None:
        from lex.core.models.LexModel import LexModel

        # A minimal LexModel subclass (unmanaged so we don't touch the DB).
        class MiniLexModel(LexModel):
            body = models.CharField(max_length=50)

            class Meta:
                app_label = "lex_app"
                managed = False

        # A non-LexModel serializer target.
        class PlainDjangoModel(models.Model):
            body = models.CharField(max_length=50)

            class Meta:
                app_label = "lex_app"
                managed = False

        # Build serializers via the metaclass — this is what the framework
        # does implicitly for every LexModelSerializer subclass.
        LexSer = PermissionAwareSerializerMetaclass(
            "LexModelSerializerImpl",
            (serializers.ModelSerializer,),
            {"Meta": type("Meta", (), {"model": MiniLexModel, "fields": "__all__"})},
        )
        PlainSer = PermissionAwareSerializerMetaclass(
            "PlainSerializerImpl",
            (serializers.ModelSerializer,),
            {"Meta": type("Meta", (), {"model": PlainDjangoModel, "fields": "__all__"})},
        )

        self.assertTrue(
            issubclass(LexSer, PermissionAwareSerializerMixin),
            "Metaclass must inject the mixin for LexModel-backed serializers",
        )
        self.assertFalse(
            issubclass(PlainSer, PermissionAwareSerializerMixin),
            "Plain Django models must NOT pick up the permission mixin — "
            "they have no `can_read`/`can_edit` and would explode at validation time",
        )


# --------------------------------------------------------------------
# 4.23 — 4.26: run_validation end-to-end
# --------------------------------------------------------------------
class _FieldLevelSerializer(PermissionAwareSerializerMixin, serializers.ModelSerializer):
    """Real serializer over ``FieldLevelItem`` so ``run_validation``
    has a real instance to bind to. ``FieldLevelItem.permission_edit``
    only allows ``public_name`` for non-superusers — that's the hook
    we lean on to prove the mixin denies writes to ``sensitive_salary``
    / ``pii_ssn`` unless they're unchanged."""

    class Meta:
        model = FieldLevelItem
        fields = "__all__"


class _ProtectedSerializer(PermissionAwareSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = ProtectedItem
        fields = "__all__"


class TestCluster04f_RunValidation(E2ETestCase):
    """
    Scenarios 4.23–4.26: drive ``PermissionAwareSerializerMixin.run_validation``
    end-to-end. These are the scenarios that actually exercise lines
    62–163 of the mixin — the change-detection branch, the per-field
    deny, the reserved-field bypass, and the create-path
    ``permission_create`` check.

    Before this extension those lines were entirely uncovered; the
    baseline of 9.33% came from import-time execution only.

    Uses ``E2ETestCase`` so ``FieldLevelItem`` / ``ProtectedItem``
    tables are materialized on the test DB.
    """

    e2e_models = [FieldLevelItem, ProtectedItem]

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="4f_admin", password="x", is_superuser=True,
        )
        # The E2ETestCase base class already creates ``self.user`` (a
        # regular authenticated user) in its setUp — reuse that as the
        # "regular" caller to keep the scenarios honest about what the
        # framework gives you by default.
        self.regular_user = self.user

    def _request(self, user):
        """Build a DRF-style request with the given user, mirroring
        what the real view passes into ``serializer.context``."""
        req = APIRequestFactory().patch("/")
        req.user = user
        # UserContext pulls this off the request; empty tuple exercises
        # the default anonymous-scopes branch.
        req.user_permissions = ()
        return req

    # -- 4.23 ----------------------------------------------------------
    def test_4_23_change_detection_skips_permission_check_on_unchanged_values(self) -> None:
        """
        Scenario 4.23: PATCH that sends a denied field **with the same
        value** it already has must pass validation.

        This is the ``new_python_value == old_python_value`` branch at
        line 124 of the mixin — without it, the frontend's "send the
        whole form back" pattern would false-403 every time a denied
        field round-trips unchanged.
        """
        item = FieldLevelItem.objects.create(
            public_name="before", sensitive_salary=100, pii_ssn="111-22-3333",
        )
        ser = _FieldLevelSerializer(
            instance=item,
            data={"public_name": "before", "sensitive_salary": 100, "pii_ssn": "111-22-3333"},
            partial=True,
            context={"request": self._request(self.regular_user)},
        )
        try:
            ser.run_validation(ser.initial_data)
        except PermissionDenied as e:  # pragma: no cover
            self.fail(
                f"Unchanged values on denied fields must skip the permission "
                f"check (change-detection branch). Got PermissionDenied: {e}"
            )

    # -- 4.24 ----------------------------------------------------------
    def test_4_24_changed_denied_field_raises_permission_denied(self) -> None:
        """
        Scenario 4.24: PATCH that actually **changes** a denied field
        must raise ``PermissionDenied`` with a field-specific message.
        This is the raise at line 134–137 — the core customer-facing
        contract of this mixin.
        """
        item = FieldLevelItem.objects.create(
            public_name="before", sensitive_salary=100, pii_ssn="111-22-3333",
        )
        ser = _FieldLevelSerializer(
            instance=item,
            data={"sensitive_salary": 999},  # changed + denied for non-superuser
            partial=True,
            context={"request": self._request(self.regular_user)},
        )
        with self.assertRaises(PermissionDenied) as ctx:
            ser.run_validation(ser.initial_data)
        self.assertIn(
            "sensitive_salary", str(ctx.exception),
            "Error message must name the offending field so the UI "
            "can surface a precise error to the customer",
        )

    # -- 4.25 ----------------------------------------------------------
    def test_4_25_reserved_field_names_bypass_permission_check(self) -> None:
        """
        Scenario 4.25: any key prefixed with ``lexReserved`` is framework
        plumbing (e.g. ``lexReservedMeta``) and must not trigger a
        permission check even if the user has no rights to it.

        Covers the early-``continue`` branch at line 105.
        """
        item = FieldLevelItem.objects.create(public_name="before")
        ser = _FieldLevelSerializer(
            instance=item,
            data={"lexReservedMeta": "anything", "public_name": "after"},
            partial=True,
            context={"request": self._request(self.regular_user)},
        )
        try:
            ser.run_validation(ser.initial_data)
        except PermissionDenied as e:  # pragma: no cover
            self.fail(f"lexReserved* keys must bypass the permission check; got {e}")

    # -- 4.26 ----------------------------------------------------------
    def test_4_26_create_path_permission_create_denies(self) -> None:
        """
        Scenario 4.26: POST (no instance) must consult
        ``permission_create`` — covers lines 139–161 of the mixin.

        ``ProtectedItem.permission_create`` only allows admins. A
        regular user hitting the create path must see ``PermissionDenied``;
        an admin must pass.
        """
        ser = _ProtectedSerializer(
            data={"name": "nope"},
            context={"request": self._request(self.regular_user)},
        )
        with self.assertRaises(PermissionDenied) as ctx:
            ser.run_validation(ser.initial_data)
        self.assertIn(
            "ProtectedItem", str(ctx.exception),
            "Create-denied message must name the model so the UI "
            "can explain which resource was blocked",
        )

        ser_admin = _ProtectedSerializer(
            data={"name": "ok"},
            context={"request": self._request(self.admin_user)},
        )
        try:
            ser_admin.run_validation(ser_admin.initial_data)
        except PermissionDenied as e:  # pragma: no cover
            self.fail(
                f"Admin must pass permission_create and run_validation; got {e}"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


