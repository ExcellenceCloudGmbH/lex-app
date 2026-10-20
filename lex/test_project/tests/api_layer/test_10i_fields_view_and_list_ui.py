"""
Sub-cluster 10i — `Fields` APIView dispatch + `create_list_ui_info` helper.

ROI batch extension to 10e (which covers `create_field_info` purely).
This file targets the **request-handling surface** of
`lex/api/views/model_info/Fields.py` (33.68% baseline, 75 stmts /
45 missed) — every line below `class Fields(APIView)` plus the small
`create_list_ui_info` helper feeding it.

Why it matters for customers
----------------------------
The `/api/<model>/fields/?serializer=…` endpoint is the **single
source of truth** the React form layer consults to decide:

* which DRF widget to render for each column (`type`),
* whether the input is editable / required / has a default,
* whether AG Grid may use the column for row-grouping or pivoting
  (`is_groupable`),
* whether the actions column should be hidden on the list view
  (`list_ui.hide_actions_column`),
* and which serializer alternates exist (the `?serializer=…`
  dispatch surface).

A regression in any of these branches silently mis-renders a form
or, worse, hides actions an admin needed to fix bad data. None of
the scenarios below need a database — `Fields.get` just walks
`serializer.fields` and `model._meta`, both of which we hand-roll
with `MagicMock` in well under a millisecond per test.

Scenarios 10.24 – 10.31.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db.models import (
    CASCADE,
    AutoField,
    BigAutoField,
    BigIntegerField,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    DurationField,
    EmailField,
    FileField,
    FloatField,
    ForeignKey,
    ImageField,
    IntegerField,
    ManyToManyField,
    OneToOneField,
    PositiveIntegerField,
    SlugField,
    SmallIntegerField,
    TextField,
    UUIDField,
)
from django.test import SimpleTestCase
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import APIException
from rest_framework.fields import empty

from lex.api.views.model_info.Fields import (
    DEFAULT_TYPE_NAME,
    DJANGO_FIELD2TYPE_NAME,
    DRF_FIELD2TYPE_NAME,
    Fields,
    create_field_info,
    create_list_ui_info,
    normalise_choices,
    resolve_type_name,
)

import pytest

pytestmark = pytest.mark.api_layer


# --------------------------------------------------------------------- #
# Helpers — synthesise the few attributes `Fields.get` actually touches.
# --------------------------------------------------------------------- #


def _fake_model(model_name="widget", pk_name="id"):
    """Mimic `model._meta.model_name` + `model._meta.pk.name` access."""
    pk = SimpleNamespace(name=pk_name)
    return SimpleNamespace(
        _meta=SimpleNamespace(model_name=model_name, pk=pk, get_field=None)
    )


def _fake_drf_field(
    *,
    source=None,
    label=None,
    default=empty,
    read_only=False,
    required=False,
    queryset_model_name=None,
):
    """A non-typed DRF field stand-in (defaults to `CharField`-like)."""
    field = MagicMock(spec=drf_serializers.CharField)
    field.source = source
    field.label = label
    field.default = default
    field.read_only = read_only
    field.required = required
    if queryset_model_name is not None:
        # Pretend it's a PrimaryKeyRelatedField with a queryset.model._meta.
        field = MagicMock(spec=drf_serializers.PrimaryKeyRelatedField)
        field.source = source
        field.label = label
        field.default = default
        field.read_only = read_only
        field.required = required
        field.queryset = MagicMock()
        field.queryset.model = SimpleNamespace(
            _meta=SimpleNamespace(model_name=queryset_model_name)
        )
    return field


def _fake_request(serializer_name=None):
    request = MagicMock()
    if serializer_name is None:
        request.query_params = {}
    else:
        request.query_params = {"serializer": serializer_name}
    return request


def _fake_container(serializers_map, *, with_getter=True):
    """A `model_container` mock supporting both dispatch styles.

    The view code prefers `container.get_serializers_map()` when present
    and falls back to `container.serializers_map`. Both branches must be
    pinned (10.27 covers the fallback).
    """
    if with_getter:
        container = MagicMock()
        container.get_serializers_map.return_value = serializers_map
        return container
    container = SimpleNamespace(serializers_map=serializers_map)
    return container


# --------------------------------------------------------------------- #
# 10i.A — `create_list_ui_info` helper
# --------------------------------------------------------------------- #


class TestCluster10i_CreateListUIInfo(SimpleTestCase):
    """List-UI metadata dispatch — `get_list_ui_options` over `Meta`."""

    # 10.24 -----------------------------------------------------------
    def test_10_24_get_list_ui_options_callable_takes_priority(self) -> None:
        """`get_list_ui_options` callable wins over any `Meta` flag.

        Pin the documented escape hatch: serializers can return an
        arbitrary list-UI dict (toolbar buttons, custom badges, etc)
        by defining a classmethod, and that takes precedence over the
        legacy `Meta.hide_actions_column` boolean. Drift to a "Meta
        wins" semantics would silently strip every custom toolbar
        button the platform ships.
        """
        custom = {"hide_actions_column": True, "extra": "value"}

        class S:
            class Meta:
                hide_actions_column = False  # would be False without override

            @classmethod
            def get_list_ui_options(cls):
                return custom

        # Class form — no instance round-trip.
        self.assertEqual(create_list_ui_info(S), custom)
        # Instance form — same answer.
        self.assertEqual(create_list_ui_info(S()), custom)

    # 10.25 -----------------------------------------------------------
    def test_10_25_meta_fallback_with_hide_actions_column_true_or_false(self):
        """No `get_list_ui_options` → `Meta.hide_actions_column` reflected.

        Pin the dominant-and-only fallback path: the front-end's "hide
        the row-actions column" flag is read straight off the
        serializer's `Meta`. Default-False keeps the column visible
        when the attribute is missing — drift here would hide actions
        for every model that hasn't opted in explicitly.
        """
        class STrue:
            class Meta:
                hide_actions_column = True

        class SFalse:
            class Meta:
                hide_actions_column = False

        class SNoMeta:
            pass

        self.assertEqual(create_list_ui_info(STrue), {"hide_actions_column": True})
        self.assertEqual(create_list_ui_info(SFalse), {"hide_actions_column": False})
        # Missing Meta entirely → False (default).
        self.assertEqual(create_list_ui_info(SNoMeta), {"hide_actions_column": False})


# --------------------------------------------------------------------- #
# 10i.B — `Fields.get` request handler
# --------------------------------------------------------------------- #


class TestCluster10i_FieldsView(SimpleTestCase):
    """End-to-end branches inside `Fields.get` (no DB, no router)."""

    def _invoke(self, container, request):
        """Stand-in for DRF's view dispatch — call `.get` directly."""
        view = Fields()
        return view.get(request, model_container=container)

    # 10.26 -----------------------------------------------------------
    def test_10_26_unknown_serializer_name_raises_apiexception_with_available(self):
        """Unknown `?serializer=…` → APIException with model + available list.

        The error body the frontend surfaces must include both the
        offending name and the keys it could have used; otherwise the
        UI can only show a generic "something went wrong" toast and
        the developer has no diagnostic for the typo. Pin the exact
        shape so a refactor can't drop the `available` key.
        """
        # default serializer present but the request asks for "ghost".
        serializers_map = {"default": MagicMock()}
        container = _fake_container(serializers_map)
        container.model_class = _fake_model("widget")
        request = _fake_request(serializer_name="ghost")

        with self.assertRaises(APIException) as ctx:
            self._invoke(container, request)

        body = ctx.exception.detail
        self.assertIn("Unknown serializer 'ghost' for model 'widget'", str(body["error"]))
        self.assertIn("default", body["available"])

    # 10.27 -----------------------------------------------------------
    def test_10_27_serializers_map_attribute_fallback_when_no_getter(self):
        """No `get_serializers_map()` → falls back to `.serializers_map` attr.

        Pin the back-compat branch: legacy containers that didn't
        implement the getter still resolve via the attribute. A
        regression that hard-required the getter would 500 every
        legacy-container request without a clear error.
        """
        ser_inst = MagicMock()
        ser_inst.fields = {}  # no fields → empty fields_info
        ser_cls = MagicMock(return_value=ser_inst)
        container = _fake_container({"default": ser_cls}, with_getter=False)
        container.model_class = _fake_model("legacy")
        # No serializer query param → defaults to "default".
        request = _fake_request()

        response = self._invoke(container, request)

        self.assertEqual(response.data["fields"], [])
        self.assertEqual(response.data["id_field"], "id")
        # list_ui present even on empty-fields response.
        self.assertIn("list_ui", response.data)

    # 10.28 -----------------------------------------------------------
    def test_10_28_django_model_field_path_emits_groupable_true(self):
        """Field resolved via `model._meta.get_field` → `is_groupable=True`.

        DB-backed columns can be used as AG Grid row-group / pivot
        keys (the SSRM endpoint runs `qs.values(field).annotate(...)`
        against them). Drift here disables grouping on every column
        the user actually wants to group by — a quiet UX regression
        that only an investigator who knows AG Grid would catch.
        """
        # Build a DRF field whose `source` resolves to a real-looking
        # Django field. We hand the model a `get_field` mock returning
        # an `IntegerField` instance.
        int_field = IntegerField(name="qty", verbose_name="qty")
        int_field.editable = True
        int_field.null = False
        int_field.primary_key = False

        ser_inst = MagicMock()
        ser_inst.fields = {"qty": _fake_drf_field(source="qty")}
        ser_inst.Meta = SimpleNamespace()  # no overrides
        ser_cls = MagicMock(return_value=ser_inst)

        model = _fake_model("widget")
        model._meta.get_field = MagicMock(return_value=int_field)
        container = _fake_container({"default": ser_cls})
        container.model_class = model

        response = self._invoke(container, _fake_request())

        self.assertEqual(len(response.data["fields"]), 1)
        info = response.data["fields"][0]
        self.assertEqual(info["name"], "qty")
        self.assertEqual(info["type"], "int")
        self.assertTrue(
            info["is_groupable"],
            "Real DB columns must opt-in to AG Grid grouping — a "
            "regression here would hide the row-group toggle on "
            "every numeric / date column users group by today.",
        )

    # 10.29 -----------------------------------------------------------
    def test_10_29_drf_only_field_falls_back_with_is_groupable_false(self):
        """`get_field` raises → DRF-only field path → `is_groupable=False`.

        SerializerMethodField / computed properties have no underlying
        Django column, so the SSRM `_execute_group_level` path can't
        group on them. Pin `is_groupable=False` so the frontend
        disables `enableRowGroup` / `enablePivot` on those columns —
        otherwise the user gets an empty grid when they try to group.

        Also pins the type-mapping fallback + default sanitisation
        (DRF `empty` → None, type → None, scalar passes through).
        """
        # FloatField → "float" via DRF_FIELD2TYPE_NAME.
        f = MagicMock(spec=drf_serializers.FloatField)
        f.source = None  # → falls back to fname
        f.label = "Score"
        f.default = empty  # documented sentinel, must coerce to None
        f.read_only = True
        f.required = False

        ser_inst = MagicMock()
        ser_inst.fields = {"score": f}
        ser_inst.Meta = SimpleNamespace()
        ser_cls = MagicMock(return_value=ser_inst)

        model = _fake_model("widget")
        # get_field raises for every name — forces the DRF fallback.
        model._meta.get_field = MagicMock(side_effect=Exception("not a model field"))
        container = _fake_container({"default": ser_cls})
        container.model_class = model

        response = self._invoke(container, _fake_request())
        info = response.data["fields"][0]

        self.assertEqual(info["name"], "score")
        self.assertEqual(info["type"], "float")
        self.assertEqual(info["readable_name"], "Score")
        self.assertFalse(info["editable"], "read_only=True must surface as editable=False")
        self.assertIsNone(info["default_value"], "DRF `empty` sentinel must coerce to None")
        self.assertFalse(
            info["is_groupable"],
            "DRF-only fields must NOT be flagged groupable — would crash "
            "the SSRM `qs.values(<computed>).annotate(...)` query.",
        )

    # 10.30 -----------------------------------------------------------
    def test_10_30_excluded_internal_fields_dropped_and_overrides_applied(self):
        """`ID_FIELD_NAME` / `SHORT_DESCR_NAME` skipped + type override wins.

        Pin two contracts in one scenario:
        * Internal-only fields are stripped from the response — they
          would surface as duplicate or confusing columns in the form
          builder if exposed.
        * `Meta.lex_field_type_overrides` lets a serializer override
          the auto-derived type for a field (e.g. force "string" on a
          JSONField that the frontend renders as a code editor). A
          regression that ignored the overrides would silently swap
          back to the auto type.

        Also pins scalar default sanitisation (str passes through, a
        bare class object is rejected → None).
        """
        from lex.api.serializers import ID_FIELD_NAME, SHORT_DESCR_NAME

        keep = MagicMock(spec=drf_serializers.CharField)
        keep.source = None
        keep.label = "Notes"
        keep.default = "draft"  # scalar — must pass through unchanged
        keep.read_only = False
        keep.required = True

        # Class as default → must coerce to None (the `isinstance(raw_def, type)`
        # branch). Use `int` as a stand-in class.
        weird = MagicMock(spec=drf_serializers.CharField)
        weird.source = None
        weird.label = "Weird"
        weird.default = int
        weird.read_only = False
        weird.required = False

        ser_inst = MagicMock()
        ser_inst.fields = {
            ID_FIELD_NAME: MagicMock(),         # → dropped
            SHORT_DESCR_NAME: MagicMock(),      # → dropped
            "notes": keep,
            "weird": weird,
        }
        ser_inst.Meta = SimpleNamespace(
            lex_field_type_overrides={"notes": "markdown"}
        )
        ser_cls = MagicMock(return_value=ser_inst)

        model = _fake_model("widget")
        model._meta.get_field = MagicMock(side_effect=Exception("drf-only"))
        container = _fake_container({"default": ser_cls})
        container.model_class = model

        response = self._invoke(container, _fake_request())
        names = [f["name"] for f in response.data["fields"]]

        self.assertNotIn(ID_FIELD_NAME, names, "id field must be hidden")
        self.assertNotIn(SHORT_DESCR_NAME, names, "short_descr must be hidden")
        notes = next(f for f in response.data["fields"] if f["name"] == "notes")
        self.assertEqual(
            notes["type"], "markdown",
            "lex_field_type_overrides must beat the auto-derived type — "
            "regression here would silently swap the editor widget back.",
        )
        self.assertEqual(notes["default_value"], "draft")
        self.assertTrue(notes["required"])
        weird_info = next(f for f in response.data["fields"] if f["name"] == "weird")
        self.assertIsNone(
            weird_info["default_value"],
            "A bare class object as `default` must coerce to None — would "
            "otherwise serialize as `<class 'int'>` and break the form.",
        )

    # 10.31 -----------------------------------------------------------
    def test_10_31_drf_pk_related_field_attaches_target_model_name(self):
        """`PrimaryKeyRelatedField` (DRF-only) → `target` set to model name.

        Pin the per-FK widget contract: when a serializer surfaces a
        related field that is NOT mirrored on the Django model
        (typical for `extra_kwargs`-style writes), the response still
        carries `target` so the frontend can render the right
        autocomplete picker. Without it the dropdown shows a free-text
        input and lets users save garbage IDs.

        Also covers the defensive `try/except` around
        `drf_field.queryset.model._meta.model_name` — a queryset that
        raises must NOT crash the whole `/fields/` response, just
        silently drop the `target`.
        """
        # Happy path — queryset.model._meta.model_name → "category".
        good = _fake_drf_field(label="Category", queryset_model_name="category")
        # Broken path — accessing `.queryset.model._meta.model_name` raises.
        bad = MagicMock(spec=drf_serializers.PrimaryKeyRelatedField)
        bad.source = None
        bad.label = "Bad"
        bad.default = empty
        bad.read_only = False
        bad.required = True
        type(bad).queryset = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("queryset blew up"))
        )

        ser_inst = MagicMock()
        ser_inst.fields = {"category": good, "bad_fk": bad}
        ser_inst.Meta = SimpleNamespace()
        ser_cls = MagicMock(return_value=ser_inst)

        model = _fake_model("widget")
        model._meta.get_field = MagicMock(side_effect=Exception("drf-only"))
        container = _fake_container({"default": ser_cls})
        container.model_class = model

        response = self._invoke(container, _fake_request())
        good_info = next(f for f in response.data["fields"] if f["name"] == "category")
        bad_info = next(f for f in response.data["fields"] if f["name"] == "bad_fk")

        self.assertEqual(good_info["type"], "foreign_key")
        self.assertEqual(
            good_info["target"], "category",
            "FK target must surface on DRF-only PrimaryKeyRelatedField — "
            "without it the frontend renders a free-text input instead of "
            "the autocomplete picker.",
        )
        self.assertEqual(bad_info["type"], "foreign_key")
        self.assertNotIn(
            "target", bad_info,
            "Defensive try/except: a raising queryset must drop `target` "
            "silently, not blow up the whole `/fields/` response.",
        )


# --------------------------------------------------------------------- #
# Type-map sanity (cheap regression gate on the dictionaries Fields
# .get keys off — drift in either map silently mis-renders widgets).
# --------------------------------------------------------------------- #


class TestCluster10i_TypeMaps(SimpleTestCase):
    """Belt-and-braces: pin the entries `Fields.get` actually consults."""

    def test_10_31b_django_field_to_type_name_covers_required_columns(self):
        """Pin the well-known mappings the form layer hard-codes against."""
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[ForeignKey], "foreign_key")
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[IntegerField], "int")
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[FloatField], "float")
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[BooleanField], "boolean")
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[DateField], "date")

    def test_10_31c_drf_field_to_type_name_covers_serializer_only_columns(self):
        """Pin DRF-only mappings used by the fallback branch (line 152-156)."""
        self.assertEqual(DRF_FIELD2TYPE_NAME[drf_serializers.IntegerField], "int")
        self.assertEqual(DRF_FIELD2TYPE_NAME[drf_serializers.DecimalField], "float")
        self.assertEqual(DRF_FIELD2TYPE_NAME[drf_serializers.CharField], "string")
        self.assertEqual(DRF_FIELD2TYPE_NAME[drf_serializers.PrimaryKeyRelatedField], "foreign_key")
        self.assertEqual(DRF_FIELD2TYPE_NAME[drf_serializers.JSONField], "json")
        self.assertEqual(DEFAULT_TYPE_NAME, "string")


# --------------------------------------------------------------------- #
# Type resolution — BUG-F-004 (numeric half) and the exact-match trap
# that caused it. Scenarios 10.72 – 10.76.
# --------------------------------------------------------------------- #


class TestCluster10i_TypeResolution(SimpleTestCase):
    """`resolve_type_name` — the lookup the whole form/filter layer keys off."""

    # 10.72 -----------------------------------------------------------
    def test_10_72_decimal_field_reports_float_not_string(self):
        """A Django `DecimalField` must report `float` (BUG-F-004, numeric half).

        `DecimalField` was absent from `DJANGO_FIELD2TYPE_NAME`, so every
        money / quantity column fell through to `DEFAULT_TYPE_NAME` and the
        grid handed it the TEXT filter — substring matching on a number,
        with no gt / lt / inRange. The frontend was ready the whole time:
        its `getFilterForField` already maps `float` to
        `agNumberColumnFilter`. Note the asymmetry this closes —
        `DRF_FIELD2TYPE_NAME` mapped `DecimalField` correctly, so a
        serializer-declared decimal worked while a model one did not.
        """
        self.assertEqual(DJANGO_FIELD2TYPE_NAME[DecimalField], "float")
        self.assertEqual(resolve_type_name(DecimalField), "float")

        nav = DecimalField(
            name="nav", verbose_name="nav", max_digits=12, decimal_places=2
        )
        nav.editable, nav.null, nav.primary_key = True, False, False
        self.assertEqual(
            create_field_info(nav)["type"],
            "float",
            "A DecimalField reaching the FE as 'string' is what put the "
            "text filter on every numeric column.",
        )

    # 10.73 -----------------------------------------------------------
    def test_10_73_subclass_walk_keeps_the_most_derived_match(self):
        """Specificity guard: `DateTimeField` must not collapse to `date`.

        The lookup walks `__mro__`, and Django's own hierarchy holds two
        traps: `DateTimeField` subclasses `DateField`, and `ImageField`
        subclasses `FileField`. A subclass check in the wrong order would
        report a datetime column as a date — a picker that silently drops
        the time — so pin both directions rather than trusting the walk.
        """
        self.assertEqual(resolve_type_name(DateTimeField), "date_time")
        self.assertEqual(resolve_type_name(DateField), "date")
        self.assertEqual(resolve_type_name(ImageField), "image_file")
        self.assertEqual(resolve_type_name(FileField), "file")

    # 10.74 -----------------------------------------------------------
    def test_10_74_unregistered_subclasses_resolve_to_their_base_type(self):
        """An unmapped subclass inherits its base's type instead of `string`.

        This is the half that prevents the NEXT BUG-F-004: Django ships
        numeric subclasses that were never registered, and projects define
        their own. Every one of them used to report `string` and get the
        text filter. `BigAutoField` is the one that mattered most in
        practice — it is the default primary key on modern Django, so
        every model's `id` column was affected.
        """
        for field_class, expected in (
            (BigAutoField, "int"),
            (BigIntegerField, "int"),
            (PositiveIntegerField, "int"),
            (SmallIntegerField, "int"),
        ):
            with self.subTest(field=field_class.__name__):
                self.assertEqual(resolve_type_name(field_class), expected)

        class ProjectDecimalField(DecimalField):
            """Stand-in for a customer's own field subclass."""

        self.assertEqual(resolve_type_name(ProjectDecimalField), "float")

    # 10.75 -----------------------------------------------------------
    def test_10_75_one_to_one_resolves_to_foreign_key_with_a_target(self):
        """`OneToOneField` gets the FK type AND the `target` that renders it.

        Type resolution and `additional_info` are two lookups on the same
        field and they have to agree. Once `OneToOneField` resolves to
        `foreign_key` through its base, an exact `type(field) == ForeignKey`
        check on the target branch hands the frontend an FK column with no
        model to resolve against — strictly worse than the `string` it
        reported before, because the FK renderer has nothing to read.
        """
        self.assertEqual(resolve_type_name(OneToOneField), "foreign_key")

        o2o = OneToOneField(
            "probe.Target", on_delete=CASCADE, name="owner", verbose_name="owner"
        )
        o2o.editable, o2o.null, o2o.primary_key = True, True, False
        # A real class, not a namespace: `OneToOneField.get_default` runs
        # `isinstance(default, self.remote_field.model)`, which needs a type.
        class _Target:
            _meta = SimpleNamespace(model_name="target", pk=SimpleNamespace(name="id"))

        o2o.remote_field = SimpleNamespace(model=_Target, limit_choices_to={})

        info = create_field_info(o2o)
        self.assertEqual(info["type"], "foreign_key")
        self.assertEqual(
            info["target"],
            "target",
            "An FK-typed column without a target renders nothing.",
        )

    # 10.76 -----------------------------------------------------------
    def test_10_76_unrelated_types_still_fall_through_to_string(self):
        """The walk must not over-capture: unmapped roots stay `string`.

        `ManyToManyField` is the one to watch. It is NOT a `ForeignKey`
        subclass, so neither the type map nor the target branch may claim
        it — if either did, a m2m column would render as a single FK.
        """
        self.assertFalse(
            issubclass(ManyToManyField, ForeignKey),
            "If Django ever changes this, the FK target branch needs "
            "revisiting — it is an isinstance check.",
        )
        for field_class in (
            CharField,
            TextField,
            EmailField,
            SlugField,
            UUIDField,
            DurationField,
            ManyToManyField,
        ):
            with self.subTest(field=field_class.__name__):
                self.assertEqual(resolve_type_name(field_class), DEFAULT_TYPE_NAME)

# --------------------------------------------------------------------- #
# Choice columns and form-required -- BUG-F-006 and BUG-F-008, the two
# `model_info` contract gaps. Scenarios 10.77 - 10.81.
# --------------------------------------------------------------------- #


class TestCluster10i_ChoicesAndRequired(SimpleTestCase):
    """The two keys the create/edit form cannot render a column without."""

    # 10.77 -----------------------------------------------------------
    def test_10_77_choices_reach_the_frontend_in_its_own_shape(self):
        """A `choices` column must ship `[{id, name}]` (BUG-F-006).

        `create_field_info` emitted no `choices` key at all, so
        `FieldInputForCreateUpdate` fell through to a free-text input and
        users could persist any string into a restricted column.

        The shape is not negotiable and not DRF's: the frontend maps
        `({ id, name }) => ...`, so emitting DRF's conventional
        `{value, display_name}` would destructure to a list of
        `{id: undefined, name: undefined}` -- a picker with the right
        number of blank options, which reads as working.
        """
        field = CharField(
            name="strategy",
            verbose_name="strategy",
            max_length=8,
            choices=[("equity", "Equity"), ("bond", "Bond")],
        )
        field.editable, field.primary_key = True, False

        info = create_field_info(field)
        self.assertEqual(
            info["choices"],
            [{"id": "equity", "name": "Equity"}, {"id": "bond", "name": "Bond"}],
        )

    # 10.78 -----------------------------------------------------------
    def test_10_78_no_choices_means_no_key_at_all(self):
        """A plain column must not carry an empty `choices` list.

        The frontend gate is `Array.isArray(choices) && choices.length > 0`,
        so `[]` would be harmless today -- but absent is the honest signal,
        and it keeps a `[]` from ever being read as "a picker with nothing
        in it".
        """
        field = CharField(name="name", verbose_name="name", max_length=32)
        field.editable, field.primary_key = True, False
        self.assertNotIn("choices", create_field_info(field))

    # 10.79 -----------------------------------------------------------
    def test_10_79_grouped_choices_flatten_and_labels_stringify(self):
        """Django optgroups flatten; lazy labels are forced to `str`.

        Django allows `[("Group", [(value, label), ...])]`. Passed through
        untouched, the group NAME becomes an option id and its member list
        becomes the label. Translated labels are lazy proxies, which do not
        survive JSON serialisation, so they are resolved here rather than
        at the response boundary.
        """
        grouped = [
            ("Listed", [("eq", "Equity"), ("etf", "ETF")]),
            ("Unlisted", [("pe", "Private Equity")]),
        ]
        self.assertEqual(
            normalise_choices(grouped),
            [
                {"id": "eq", "name": "Equity"},
                {"id": "etf", "name": "ETF"},
                {"id": "pe", "name": "Private Equity"},
            ],
        )

        # DRF's ChoiceField hands a mapping, not pairs.
        self.assertEqual(
            normalise_choices({"a": "Alpha", "b": "Beta"}),
            [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}],
        )

        # Absent / empty collapse to None so no key is emitted.
        for empty_input in (None, [], {}):
            with self.subTest(value=empty_input):
                self.assertIsNone(normalise_choices(empty_input))

    # 10.80 -----------------------------------------------------------
    def test_10_80_non_blank_char_column_reports_required(self):
        """A `blank=False` CharField must report `required=True` (BUG-F-008).

        This is the scenario the bug was filed on and the one that stayed
        broken through an earlier attempt at it. The check read
        `not (field.null or default is not None)`, and Django synthesises a
        `""` default for every CharField -- so the second term was always
        true, the `null` term never got a look in, and EVERY char column
        reported optional. No client validator attached, and empty-required
        saves went to the backend to be 400'd, violating the no-request
        contract.
        """
        field = CharField(name="name", verbose_name="name", max_length=32)
        field.editable, field.primary_key = True, False

        info = create_field_info(field)
        self.assertEqual(
            info["default_value"],
            "",
            "Pinning Django's synthesised empty-string default, because it "
            "is what made the old expression always-optional.",
        )
        self.assertTrue(
            info["required"],
            "blank=False, null=False, no default -- the form must block an "
            "empty save rather than let the backend 400 it.",
        )

    # 10.81 -----------------------------------------------------------
    def test_10_81_blank_nullable_or_defaulted_columns_are_optional(self):
        """Any of blank / null / has_default makes a column optional.

        Mirrors DRF's own `ModelSerializer` rule, so the client validator
        and the serializer that will judge the request agree. Getting this
        backwards is worse than the original bug: a spuriously required
        column blocks a save the backend would have accepted.
        """
        cases = (
            ("blank", CharField(name="a", verbose_name="a", max_length=8, blank=True)),
            ("null", CharField(name="b", verbose_name="b", max_length=8, null=True)),
            ("default", CharField(name="c", verbose_name="c", max_length=8, default="x")),
            ("int null", IntegerField(name="d", verbose_name="d", null=True)),
        )
        for label, field in cases:
            with self.subTest(case=label):
                field.editable, field.primary_key = True, False
                self.assertFalse(create_field_info(field)["required"])

        # Control: none of the three apply, so it stays required.
        strict = IntegerField(name="e", verbose_name="e")
        strict.editable, strict.primary_key = True, False
        self.assertTrue(create_field_info(strict)["required"])

if __name__ == "__main__":  # pragma: no cover
    unittest.main()

