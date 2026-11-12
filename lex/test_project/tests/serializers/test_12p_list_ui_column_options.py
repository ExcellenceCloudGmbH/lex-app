"""Cluster 12p -- a project declares how its columns look, once, on the serializer.

Intent: LEX-1556. A customer reported that the grid's ID column is too wide.
The only answer the framework had was "resize it and save a view", which does
not survive a read-only System View, has to be repeated per model, and says
nothing to the many users who never read the identifier at all. The frontend
already recognises three owners of how a table looks -- the individual user
(their Settings panel), a saved view (the data shape), and **the developer**
-- but the developer's layer, ``list_ui``, could say exactly one thing
(``hide_actions_column``) and nothing at all about a column.

``Meta.lex_column_options`` is that missing layer:

    class FundSerializer(LexSerializer):
        class Meta:
            model = Fund
            fields = "__all__"
            lex_column_options = {"id": {"max_width": 90}}

WHAT MAKES IT WORTH PINNING. This metadata is read while answering
``model_info``, which the entire frontend blocks on before it can draw
anything. A project that mistypes an option must get that option ignored --
never a grid that will not load, and never an exception from a code path whose
only job is to describe a display preference. The scenarios below are mostly
about the garbage, because the happy path is the part that would be noticed
immediately and the refusal to explode is the part that would not.

The scenarios assert the CONTRACT (what a frontend may rely on reading), not
the normaliser's internals: a declaration either survives intact or is absent.

Cluster 12p -- scenarios 12.66-12.72. Type: U.
Covers: lex/api/serializers/list_ui_columns.py,
        lex/api/serializers/base_serializers.py (LexSerializer.get_list_ui_options).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12p_list_ui_column_options.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.api.serializers import LexSerializer
from lex.api.serializers.list_ui_columns import build_column_options

pytestmark = pytest.mark.serializers


class _Meta:
    """A serializer ``Meta`` carrying only what this module reads."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class ListUiColumnOptionsTests(SimpleTestCase):
    # 12.66 ------------------------------------------------------------
    def test_12_66_declared_options_reach_the_frontend_unchanged(self):
        """Every supported option survives exactly as the project wrote it.

        This is the whole feature: a project says "cap the id at 90px, pin the
        name left, never show the internal reference" and the frontend is told
        precisely that. A value quietly rewritten here would be debugged in
        the browser, which is the wrong place to discover it.
        """
        options = build_column_options(
            _Meta(
                lex_column_options={
                    "id": {"max_width": 90},
                    "name": {"width": 320, "min_width": 120, "pinned": "left"},
                    "internal_ref": {"hidden": True},
                }
            )
        )

        self.assertEqual(
            options,
            {
                "id": {"max_width": 90},
                "name": {"width": 320, "min_width": 120, "pinned": "left"},
                "internal_ref": {"hidden": True},
            },
        )

    # 12.67 ------------------------------------------------------------
    def test_12_67_a_serializer_that_declares_nothing_adds_nothing(self):
        """The payload only grows for projects that asked for it.

        Almost every serializer in every project declares no column options.
        They must not start paying for the feature in response size, and the
        frontend must not have to tell "declared nothing" apart from
        "declared an empty thing".
        """
        self.assertEqual(build_column_options(_Meta()), {})
        self.assertEqual(build_column_options(None), {})
        # A declaration of the wrong SHAPE entirely is still "nothing".
        self.assertEqual(build_column_options(_Meta(lex_column_options=["id"])), {})
        self.assertEqual(build_column_options(_Meta(lex_column_options="id")), {})

    # 12.68 ------------------------------------------------------------
    def test_12_68_an_unusable_option_is_dropped_and_its_neighbours_survive(self):
        """One bad option must not cost a project its good ones.

        The failure this prevents: a project adds a fourth column option,
        fat-fingers it, and loses the three that were working -- with the only
        symptom being a grid that looks like it did before the change.
        """
        options = build_column_options(
            _Meta(
                lex_column_options={
                    "id": {
                        "max_width": 90,
                        "width": "wide",       # not a number
                        "min_width": -5,       # not a width
                        "pinned": "middle",    # not a side
                        "colour": "red",       # not an option at all
                    },
                }
            )
        )

        self.assertEqual(options, {"id": {"max_width": 90}})

    # 12.69 ------------------------------------------------------------
    def test_12_69_a_boolean_is_never_read_as_a_width(self):
        """``{"width": True}`` is a typo, not a one-pixel column.

        ``bool`` is an ``int`` subclass in Python, so a plain ``isinstance``
        check accepts ``True`` and produces a column one pixel wide -- which
        looks to the user like the column vanished, and to the developer like
        the feature is broken.
        """
        options = build_column_options(
            _Meta(lex_column_options={"id": {"width": True, "hidden": True}})
        )

        self.assertEqual(options, {"id": {"hidden": True}})

    # 12.70 ------------------------------------------------------------
    def test_12_70_a_contradictory_width_pair_is_refused_rather_than_guessed(self):
        """A minimum above a maximum is a mistake with no correct reading.

        Honouring either one would be a guess about which the project meant,
        and the grid would silently size the column to the guess. Dropping
        both leaves the column sizing itself -- visibly unchanged, which is
        the symptom that sends someone to look at what they wrote.
        """
        options = build_column_options(
            _Meta(
                lex_column_options={
                    "id": {"min_width": 400, "max_width": 100, "pinned": "right"},
                }
            )
        )

        # The contradiction goes; the unrelated option stays.
        self.assertEqual(options, {"id": {"pinned": "right"}})

        # A consistent pair is kept.
        consistent = build_column_options(
            _Meta(lex_column_options={"id": {"min_width": 100, "max_width": 400}})
        )
        self.assertEqual(consistent, {"id": {"min_width": 100, "max_width": 400}})

    # 12.71 ------------------------------------------------------------
    def test_12_71_the_serializer_contract_carries_both_list_ui_answers(self):
        """``get_list_ui_options`` reports the actions flag AND the columns.

        ``list_ui`` is one payload with one reader. A serializer that answers
        only half of it forces the frontend to guess what the other half
        means, and the half most likely to be forgotten is the one added last.
        """

        class Declaring(LexSerializer):
            class Meta:
                hide_actions_column = True
                lex_column_options = {"id": {"max_width": 90}}

        class Plain(LexSerializer):
            class Meta:
                pass

        self.assertEqual(
            Declaring.get_list_ui_options(),
            {"hide_actions_column": True, "columns": {"id": {"max_width": 90}}},
        )
        # A serializer declaring neither still answers both keys, so the
        # frontend reads one shape regardless of what the project wrote.
        self.assertEqual(
            Plain.get_list_ui_options(),
            {"hide_actions_column": False, "columns": {}},
        )

    # 12.72 ------------------------------------------------------------
    def test_12_72_options_survive_the_wrapping_of_a_custom_serializer(self):
        """A project's OWN DRF serializer keeps its column options.

        A second execution path, and the one a project that cares about
        column widths is most likely to be on: a hand-written
        ``ModelSerializer`` registered as ``api_serializers["default"]`` is
        not used as written — the framework wraps it in a generated subclass
        to add the fields the frontend needs. Anything that path drops is
        lost for exactly the serializers that were customised on purpose,
        and the symptom (my declaration did nothing) is indistinguishable
        from the declaration never having been read at all.
        """
        from rest_framework import serializers as drf_serializers

        from lex.api.serializers.base_serializers import _wrap_custom_serializer
        from lex.test_project.tests.serializers.models import WideItem

        class CustomSerializer(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount"]
                lex_column_options = {"id": {"max_width": 90}}

        wrapped = _wrap_custom_serializer(CustomSerializer, WideItem)

        self.assertEqual(
            wrapped.get_list_ui_options()["columns"],
            {"id": {"max_width": 90}},
        )
