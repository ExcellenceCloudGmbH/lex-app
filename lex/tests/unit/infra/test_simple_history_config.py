"""
Tests for ``lex.lex_app.simple_history_config`` — model exclusion logic
for django-simple-history.

Covers:
- get_excluded_models: builtin + settings-based exclusions
- should_track_model: Historical prefix, excluded names, Django apps,
  third-party apps, abstract models, already-has-history
- get_model_exclusion_reason: returns human-readable reason strings
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase, override_settings
from lex.lex_app.simple_history_config import (
    DJANGO_BUILTIN_MODELS,
    THIRD_PARTY_EXCLUDED_MODELS,
    get_excluded_models,
    should_track_model,
    get_model_exclusion_reason,
)


def _make_model(name="TestModel", app_label="myapp", abstract=False, has_history=False):
    """Create a mock Django model class for testing."""
    model = MagicMock()
    model.__name__ = name
    model._meta.app_label = app_label
    model._meta.abstract = abstract
    if has_history:
        model.history = MagicMock()
    else:
        # Remove history attribute entirely
        del model.history
    return model


class GetExcludedModelsTest(SimpleTestCase):

    @override_settings(
        SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True,
        SIMPLE_HISTORY_EXCLUDED_MODELS=[],
    )
    def test_includes_builtins_when_enabled(self):
        excluded = get_excluded_models()
        self.assertTrue(DJANGO_BUILTIN_MODELS.issubset(excluded))
        self.assertTrue(THIRD_PARTY_EXCLUDED_MODELS.issubset(excluded))

    @override_settings(
        SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=False,
        SIMPLE_HISTORY_EXCLUDED_MODELS=[],
    )
    def test_excludes_builtins_when_disabled(self):
        excluded = get_excluded_models()
        # Should not contain the builtin sets
        for model in DJANGO_BUILTIN_MODELS:
            self.assertNotIn(model, excluded)

    @override_settings(
        SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True,
        SIMPLE_HISTORY_EXCLUDED_MODELS=["CustomModel", "AnotherModel"],
    )
    def test_includes_app_specific_exclusions(self):
        excluded = get_excluded_models()
        self.assertIn("custommodel", excluded)
        self.assertIn("anothermodel", excluded)

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_missing_excluded_models_setting(self):
        """No SIMPLE_HISTORY_EXCLUDED_MODELS setting → still works."""
        excluded = get_excluded_models()
        self.assertIsInstance(excluded, set)


class ShouldTrackModelTest(SimpleTestCase):

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_historical_prefix_excluded(self):
        model = _make_model(name="HistoricalInvoice")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_builtin_model_name_excluded(self):
        model = _make_model(name="Permission", app_label="myapp")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_django_auth_app_excluded(self):
        model = _make_model(name="CustomGroup", app_label="auth")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_django_contenttypes_app_excluded(self):
        model = _make_model(name="SomeModel", app_label="contenttypes")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_third_party_app_excluded(self):
        model = _make_model(name="OAuth", app_label="oauth2_authcodeflow")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_simple_history_app_excluded(self):
        model = _make_model(name="Tracker", app_label="simple_history")
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_abstract_model_excluded(self):
        model = _make_model(name="BaseModel", abstract=True)
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_model_with_history_excluded(self):
        model = _make_model(name="Invoice", has_history=True)
        self.assertFalse(should_track_model(model))

    @override_settings(SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN=True)
    def test_normal_model_should_be_tracked(self):
        model = _make_model(name="Invoice", app_label="billing")
        self.assertTrue(should_track_model(model))


class GetModelExclusionReasonTest(SimpleTestCase):

    def test_historical_prefix(self):
        model = _make_model(name="HistoricalFund")
        reason = get_model_exclusion_reason(model)
        self.assertIn("Historical model", reason)

    def test_builtin_model_name(self):
        model = _make_model(name="User")
        reason = get_model_exclusion_reason(model)
        self.assertIn("Django built-in model", reason)

    def test_third_party_model_name(self):
        model = _make_model(name="rest_framework")
        reason = get_model_exclusion_reason(model)
        self.assertIn("Third-party excluded", reason)

    def test_django_app(self):
        model = _make_model(name="Foo", app_label="auth")
        reason = get_model_exclusion_reason(model)
        self.assertIn("Django built-in app", reason)

    def test_excluded_third_party_app(self):
        model = _make_model(name="Bar", app_label="channels")
        reason = get_model_exclusion_reason(model)
        self.assertIn("Excluded third-party app", reason)

    def test_abstract(self):
        model = _make_model(name="AbstractBase", app_label="myapp", abstract=True)
        reason = get_model_exclusion_reason(model)
        self.assertIn("Abstract model", reason)

    def test_already_has_history(self):
        model = _make_model(name="Fund", app_label="myapp", has_history=True)
        reason = get_model_exclusion_reason(model)
        self.assertIn("Already has history", reason)

    def test_trackable_model_returns_none(self):
        model = _make_model(name="Invoice", app_label="billing")
        self.assertIsNone(get_model_exclusion_reason(model))
