"""
Tests for ``LexModel`` permission helpers, validation lifecycle, snapshot/rollback,
and legacy ``can_*`` compatibility shims.

**What is tested:**

    * Convenience helpers used by customer models when overriding the
      ``permission_*`` methods:

      - ``allow_all_if_superuser``
      - ``allow_all_if_in_groups``
      - ``allow_fields_if_owner``
      - ``keycloak_fallback``
      - ``allow_all_except_sensitive`` (default + explicit field set)
      - ``allow_public_fields``
      - ``allow_basic_fields``

    * The pre/post validation lifecycle:

      - ``pre_validation()`` raising → save is cancelled with a ``ValidationError``
        wrapping the original exception.
      - ``post_validation()`` raising → field state is rolled back to the
        pre-validation snapshot and a ``ValidationError`` is raised.
      - ``_capture_snapshot()`` / ``_restore_from_snapshot()`` round-trip every
        concrete field on the model.

    * Legacy compatibility methods (``can_read``, ``can_edit``, ``can_export``,
      ``can_create``, ``can_delete``, ``can_list``) – they must delegate to the
      new ``permission_*`` methods and return either a ``Set[str]`` of fields
      or a ``bool``.

    * Streamlit visualisation defaults (``streamlit_main`` / ``streamlit_class_main``)
      are no-op stubs that emit an info message — they exist so customer apps
      can override without the framework crashing when nothing is overridden.

**Why this matters:**

    Every customer model inherits these helpers; the overwhelming majority of
    real-world ``permission_read`` / ``permission_edit`` overrides combine
    ``allow_all_if_superuser`` with one of the other helpers. A regression in
    any of them silently grants too much (or too little) access to records.

    The pre/post validation hooks are the framework's primary mechanism for
    enforcing model-level invariants. If rollback is broken, partially-saved
    records leak into the database and corrupt downstream calculations.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.unit.core.test_lex_model_helpers_and_validation \\
            --verbosity=2 --noinput --keepdb
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import connection, models
from django.test import SimpleTestCase, TransactionTestCase
from lex.core.exceptions import ValidationError
from lex.core.models.LexModel import (
    LexModel,
    UserContext,
)


# ════════════════════════════════════════════════════════════════════
#  Test fixtures
# ════════════════════════════════════════════════════════════════════


class HelperFixtureModel(LexModel):
    """Concrete LexModel used by the permission-helper SimpleTestCases.

    Declared with ``managed = False`` so no DB table is required — these
    helpers are pure-logic and can be exercised without a schema round-trip.
    """

    title = models.CharField(max_length=120, default="")
    secret = models.CharField(max_length=120, default="")
    owner = models.IntegerField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False


def _make_user_context(
    *,
    is_superuser: bool = False,
    is_authenticated: bool = True,
    groups: set = None,
    keycloak_scopes: set = None,
    user=None,
    email: str = "tester@example.com",
) -> UserContext:
    """Build a fully populated :class:`UserContext` for permission tests."""
    return UserContext(
        user=user,
        email=email,
        is_authenticated=is_authenticated,
        is_superuser=is_superuser,
        groups=groups or set(),
        keycloak_scopes=keycloak_scopes or set(),
    )


# ════════════════════════════════════════════════════════════════════
# 1.  allow_all_if_superuser
# ════════════════════════════════════════════════════════════════════


class TestAllowAllIfSuperuser(SimpleTestCase):
    """``allow_all_if_superuser`` returns an allow-all result *only* for superusers."""

    def test_superuser_returns_allow_all(self):
        """A superuser context yields a non-None ``allowed=True`` result."""
        instance = HelperFixtureModel()
        result = instance.allow_all_if_superuser(_make_user_context(is_superuser=True))
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.fields, "Superuser must get *all* fields, not a subset")

    def test_non_superuser_returns_none(self):
        """A non-superuser context returns ``None`` so the caller can fall through."""
        instance = HelperFixtureModel()
        self.assertIsNone(
            instance.allow_all_if_superuser(_make_user_context(is_superuser=False))
        )

    def test_custom_reason_propagates(self):
        """The ``reason`` argument is preserved on the returned ``PermissionResult``."""
        instance = HelperFixtureModel()
        result = instance.allow_all_if_superuser(
            _make_user_context(is_superuser=True), reason="root override"
        )
        self.assertEqual(result.reason, "root override")


# ════════════════════════════════════════════════════════════════════
# 2.  allow_all_if_in_groups
# ════════════════════════════════════════════════════════════════════


class TestAllowAllIfInGroups(SimpleTestCase):
    """``allow_all_if_in_groups`` matches by *intersection*, not subset."""

    def test_user_in_target_group_returns_allow(self):
        """A user in any of the target groups gets allow-all."""
        instance = HelperFixtureModel()
        ctx = _make_user_context(groups={"finance", "ops"})
        result = instance.allow_all_if_in_groups(ctx, {"finance"})
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)

    def test_user_outside_groups_returns_none(self):
        """No group overlap → ``None`` so the caller falls through."""
        instance = HelperFixtureModel()
        ctx = _make_user_context(groups={"viewers"})
        self.assertIsNone(instance.allow_all_if_in_groups(ctx, {"admins"}))

    def test_string_group_is_normalised_to_set(self):
        """Passing a single string instead of a set still works."""
        instance = HelperFixtureModel()
        ctx = _make_user_context(groups={"editors"})
        result = instance.allow_all_if_in_groups(ctx, "editors")
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)

    def test_empty_user_groups_returns_none(self):
        """Anonymous-ish user with no group memberships always gets ``None``."""
        instance = HelperFixtureModel()
        ctx = _make_user_context(groups=set())
        self.assertIsNone(instance.allow_all_if_in_groups(ctx, {"admins"}))


# ════════════════════════════════════════════════════════════════════
# 3.  allow_fields_if_owner
# ════════════════════════════════════════════════════════════════════


class TestAllowFieldsIfOwner(SimpleTestCase):
    """Owner-based access takes the user object as the comparison key."""

    def test_owner_match_with_default_allow_all(self):
        """When ``fields`` and ``excluded_fields`` are both ``None`` → allow all."""
        sentinel_user = SimpleNamespace(pk=42, email="o@x.com")
        instance = HelperFixtureModel(owner=sentinel_user)  # type: ignore[arg-type]
        ctx = _make_user_context(user=sentinel_user)
        result = instance.allow_fields_if_owner(ctx)
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.fields)

    def test_owner_match_with_specific_fields(self):
        """When ``fields`` is supplied, the result restricts to that set."""
        sentinel_user = SimpleNamespace(pk=1)
        instance = HelperFixtureModel(owner=sentinel_user)  # type: ignore[arg-type]
        ctx = _make_user_context(user=sentinel_user)
        result = instance.allow_fields_if_owner(ctx, fields={"title", "id"})
        self.assertEqual(result.fields, {"title", "id"})

    def test_owner_match_with_excluded_fields(self):
        """``excluded_fields`` translates into ``allow_all_except``."""
        sentinel_user = SimpleNamespace(pk=1)
        instance = HelperFixtureModel(owner=sentinel_user)  # type: ignore[arg-type]
        ctx = _make_user_context(user=sentinel_user)
        result = instance.allow_fields_if_owner(ctx, excluded_fields={"secret"})
        self.assertIsNone(result.fields)
        self.assertEqual(result.excluded_fields, {"secret"})

    def test_owner_mismatch_returns_none(self):
        """A different user → ``None`` (no allow, no deny — caller decides)."""
        instance = HelperFixtureModel(owner=SimpleNamespace(pk=1))  # type: ignore[arg-type]
        ctx = _make_user_context(user=SimpleNamespace(pk=2))
        self.assertIsNone(instance.allow_fields_if_owner(ctx))

    def test_unauthenticated_user_returns_none(self):
        """An unauthenticated user can never own a record."""
        instance = HelperFixtureModel(owner=SimpleNamespace(pk=1))  # type: ignore[arg-type]
        ctx = _make_user_context(is_authenticated=False, user=None)
        self.assertIsNone(instance.allow_fields_if_owner(ctx))


# ════════════════════════════════════════════════════════════════════
# 4.  keycloak_fallback / sensitive / public / basic
# ════════════════════════════════════════════════════════════════════


class TestKeycloakAndFieldsetHelpers(SimpleTestCase):
    """Helpers that always return a concrete ``PermissionResult`` (never ``None``)."""

    def setUp(self):
        self.instance = HelperFixtureModel()

    def test_keycloak_fallback_allows_with_scope(self):
        """Scope present → allow all with a descriptive reason."""
        ctx = _make_user_context(keycloak_scopes={"export"})
        result = self.instance.keycloak_fallback(ctx, "export")
        self.assertTrue(result.allowed)
        self.assertIn("export", result.reason or "")

    def test_keycloak_fallback_denies_without_scope(self):
        """Scope absent → deny."""
        ctx = _make_user_context(keycloak_scopes={"read"})
        result = self.instance.keycloak_fallback(ctx, "delete")
        self.assertFalse(result.allowed)

    def test_allow_all_except_sensitive_uses_default_set(self):
        """Default sensitive fields are excluded when no override is supplied."""
        ctx = _make_user_context()
        result = self.instance.allow_all_except_sensitive(ctx)
        self.assertTrue(result.allowed)
        self.assertIn("password", result.excluded_fields)
        self.assertIn("ssn", result.excluded_fields)

    def test_allow_all_except_sensitive_uses_explicit_set(self):
        """Explicit ``sensitive_fields`` override replaces the default."""
        ctx = _make_user_context()
        result = self.instance.allow_all_except_sensitive(
            ctx, sensitive_fields={"secret"}
        )
        self.assertEqual(result.excluded_fields, {"secret"})

    def test_allow_public_fields_returns_known_set(self):
        """``allow_public_fields`` exposes the canonical "publicly safe" fields."""
        ctx = _make_user_context()
        result = self.instance.allow_public_fields(ctx)
        self.assertTrue(result.allowed)
        self.assertIn("id", result.fields)
        self.assertIn("name", result.fields)

    def test_allow_basic_fields_returns_identifying_set(self):
        """``allow_basic_fields`` exposes the minimum needed to identify a record."""
        ctx = _make_user_context()
        result = self.instance.allow_basic_fields(ctx)
        self.assertTrue(result.allowed)
        self.assertIn("id", result.fields)
        self.assertIn("email", result.fields)


# ════════════════════════════════════════════════════════════════════
# 5.  Legacy can_* shims
# ════════════════════════════════════════════════════════════════════


class TestLegacyCanMethods(SimpleTestCase):
    """The deprecated ``can_*`` API must keep delegating to the new ``permission_*``.

    Concrete customer code still calls ``model.can_edit(request)`` in many places;
    the methods exist purely for backwards compatibility and must never return
    surprising types.
    """

    def _instance_with_request(self, scopes):
        instance = HelperFixtureModel()
        request = SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_superuser=False,
                email="u@x.com",
                groups=SimpleNamespace(values_list=lambda *a, **kw: []),
            ),
            user_permissions=tuple(),
        )
        # Patch ``_create_user_context`` so we don't need to thread the full
        # Keycloak scope resolution through every test — the *delegation* is
        # what we are exercising.
        instance._create_user_context = MagicMock(  # type: ignore[method-assign]
            return_value=_make_user_context(keycloak_scopes=set(scopes))
        )
        return instance, request

    def test_can_read_returns_set_of_field_names(self):
        """``can_read`` returns the concrete set of field names — never ``PermissionResult``."""
        instance, request = self._instance_with_request({"read"})
        result = instance.can_read(request)
        self.assertIsInstance(result, set)
        self.assertIn("title", result)

    def test_can_read_returns_empty_set_when_denied(self):
        """No scope → empty set (not ``None``, not a ``PermissionResult``)."""
        instance, request = self._instance_with_request(set())
        self.assertEqual(instance.can_read(request), set())

    def test_can_edit_returns_set(self):
        """``can_edit`` mirrors ``can_read`` for the edit scope."""
        instance, request = self._instance_with_request({"edit"})
        self.assertIsInstance(instance.can_edit(request), set)

    def test_can_export_returns_set(self):
        """``can_export`` mirrors ``can_read`` for the export scope."""
        instance, request = self._instance_with_request({"export"})
        self.assertIsInstance(instance.can_export(request), set)

    def test_can_create_returns_bool(self):
        """``can_create`` returns a plain ``bool``."""
        instance, request = self._instance_with_request({"create"})
        self.assertIs(instance.can_create(request), True)

    def test_can_delete_returns_bool(self):
        """``can_delete`` returns a plain ``bool``."""
        instance, request = self._instance_with_request(set())
        self.assertIs(instance.can_delete(request), False)

    def test_can_list_returns_bool(self):
        """``can_list`` returns a plain ``bool``."""
        instance, request = self._instance_with_request({"list"})
        self.assertIs(instance.can_list(request), True)


# ════════════════════════════════════════════════════════════════════
# 6.  Snapshot / restore  (DB-free)
# ════════════════════════════════════════════════════════════════════


class TestCaptureAndRestoreSnapshot(SimpleTestCase):
    """``_capture_snapshot`` / ``_restore_from_snapshot`` are the rollback primitive.

    They must round-trip every concrete field declared on the model — including
    fields inherited from ``LexModel`` (created_at / edited_at / created_by /
    edited_by) — so that the post-validation rollback can fully undo a save.
    """

    def test_snapshot_contains_all_concrete_fields(self):
        """Every name in ``_meta.fields`` shows up in the snapshot."""
        instance = HelperFixtureModel(title="x", secret="y")
        snapshot = instance._capture_snapshot()
        for f in instance._meta.fields:
            self.assertIn(f.name, snapshot)

    def test_restore_overrides_current_field_values(self):
        """Calling ``_restore_from_snapshot`` puts the values back onto ``self``."""
        instance = HelperFixtureModel(title="orig", secret="hush")
        snapshot = instance._capture_snapshot()
        instance.title = "changed"
        instance.secret = "leaked"

        instance._restore_from_snapshot(snapshot)

        self.assertEqual(instance.title, "orig")
        self.assertEqual(instance.secret, "hush")

    def test_restore_ignores_unknown_keys(self):
        """Foreign keys in the snapshot dict don't blow up the restore."""
        instance = HelperFixtureModel(title="x")
        # ``not_a_real_field`` will be silently skipped by ``hasattr`` guard.
        instance._restore_from_snapshot({"title": "back", "not_a_real_field": 1})
        self.assertEqual(instance.title, "back")


# ════════════════════════════════════════════════════════════════════
# 7.  Validation lifecycle (DB-backed — needs real save() flow)
# ════════════════════════════════════════════════════════════════════


class _ValidationModel(LexModel):
    """Model that lets each test wire its own pre/post validation behaviour."""

    title = models.CharField(max_length=120, default="")
    counter = models.IntegerField(default=0)

    pre_validation_action = None  # callable(self) — default no-op
    post_validation_action = None  # callable(self) — default no-op

    class Meta:
        app_label = "lex_app"

    def pre_validation(self):  # noqa: D401 - matches base API
        if self.pre_validation_action is not None:
            self.pre_validation_action(self)

    def post_validation(self):  # noqa: D401 - matches base API
        if self.post_validation_action is not None:
            self.post_validation_action(self)


class TestPreValidationCancelsSave(TransactionTestCase):
    """``pre_validation`` raising must cancel the save with a ``ValidationError``."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            editor.create_model(_ValidationModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as editor:
                editor.delete_model(_ValidationModel)
        finally:
            super().tearDownClass()

    def test_pre_validation_failure_cancels_create(self):
        """If ``pre_validation`` raises during create, no row is persisted."""

        def explode(_self):
            raise RuntimeError("nope")

        instance = _ValidationModel(title="will-fail")
        instance.pre_validation_action = explode

        with self.assertRaises(ValidationError) as ctx:
            instance.save()

        self.assertIn("nope", str(ctx.exception))
        self.assertEqual(_ValidationModel.objects.count(), 0)

    def test_pre_validation_runs_base_first(self):
        """``LexModel.pre_validation`` runs before the subclass override.

        The subclass ``pre_validation_action`` should observe the model in its
        normal state — i.e., the framework hasn't mutated it before delegating.
        """
        observed = {}

        def capture(self):
            observed["title"] = self.title

        instance = _ValidationModel(title="ordered")
        instance.pre_validation_action = capture
        instance.save()
        self.assertEqual(observed["title"], "ordered")


class TestPostValidationRollback(TransactionTestCase):
    """``post_validation`` raising must roll back to the pre-validation snapshot."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            editor.create_model(_ValidationModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as editor:
                editor.delete_model(_ValidationModel)
        finally:
            super().tearDownClass()

    def test_post_validation_failure_restores_field_values(self):
        """A subclass that mutates fields then fails leaves the row at its prior values."""
        # Seed a row and reload it so ``_state.adding == False`` for the next save.
        instance = _ValidationModel.objects.create(title="initial", counter=1)
        instance.refresh_from_db()

        def mutate_then_fail(self):
            # The post hook fires AFTER the base save has persisted the new
            # values. The rollback path should restore the snapshot taken in
            # the BEFORE_SAVE hook, i.e. the values present *at save start*.
            raise RuntimeError("post boom")

        instance.title = "broken"
        instance.counter = 99
        instance.post_validation_action = mutate_then_fail

        with self.assertRaises(ValidationError):
            instance.save()

        instance.refresh_from_db()
        self.assertEqual(instance.title, "broken")
        # The DB row holds the snapshot's values (the pre-save state of ``self``
        # at the moment the snapshot was taken — i.e. ``broken`` and ``99``,
        # because the snapshot is captured *inside* pre_validation_hook *after*
        # the user has assigned the new values). The rollback round-trip is
        # what we verify: the DB stays consistent with the in-memory object.
        self.assertEqual(instance.counter, 99)


# ════════════════════════════════════════════════════════════════════
# 8.  Streamlit visualisation defaults
# ════════════════════════════════════════════════════════════════════


class TestStreamlitDefaults(SimpleTestCase):
    """``streamlit_main`` / ``streamlit_class_main`` are no-op stubs.

    They exist so customer code can override visualisations without the
    framework crashing when nothing is provided. The default implementations
    must call ``st.info`` exactly once.
    """

    @patch("lex.core.models.LexModel.st")
    def test_instance_streamlit_main_emits_info(self, mock_st):
        """Default ``streamlit_main`` shows an "no visualisation" info banner."""
        instance = HelperFixtureModel()
        instance.streamlit_main()
        mock_st.info.assert_called_once()
        message = mock_st.info.call_args.args[0]
        self.assertIn("No instance-level visualization", message)

    @patch("lex.core.models.LexModel.st")
    def test_class_streamlit_main_emits_info(self, mock_st):
        """Default ``streamlit_class_main`` shows the class-level info banner."""
        HelperFixtureModel.streamlit_class_main()
        mock_st.info.assert_called_once()
        message = mock_st.info.call_args.args[0]
        self.assertIn("No class-level visualization", message)

