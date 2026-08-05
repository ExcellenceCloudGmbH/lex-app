"""Named serializers — choosing which fields a record shows.

Intent: a dashboard shows three fields of a record, not all seven, and the
choice is made once on the model rather than restated everywhere the record
appears.

``api_serializers`` is that mechanism: a mapping of name to serializer class on
the model class, which ``?serializer=<name>`` selects between
(``ModelEntryProviderMixin.get_serializer_class``). It is what
``lex_calculation(serializer=...)`` passes through — the Streamlit embed hands
the name to ``lex_view``, which puts it in the query string, and the React data
provider forwards it to this endpoint. Everything between the dashboard author
and the field list is transport.

That chain is why these scenarios exist. It crosses three codebases — a
Streamlit helper, a React data provider, a DRF view — and only its two ends are
visible to anyone: an author writes a serializer name, and a page shows fields.
If the query parameter stopped selecting anything the page would not break, it
would quietly show the default field list forever, which is exactly the failure
nobody notices.

Cluster 10p — scenarios 10.85-10.88. Type: E2E.
Covers: lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py
        (``get_serializer_class``),
        lex/api/serializers/base_serializers.py (``get_serializer_map_for_model``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10p_named_serializers.py -v
"""

from __future__ import annotations

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, API_LAYER_SERIALIZER_CALC, ApiLayerSerializerCalc

pytestmark = pytest.mark.api_layer


class TestCluster10p_NamedSerializers(E2ETestCase):
    """Cluster 10p: ``?serializer=<name>`` and the model's ``api_serializers``."""

    e2e_models = ALL_MODELS

    def _record(self):
        return ApiLayerSerializerCalc.objects.create(
            name="Q1", field_1="one", field_2="two", internal_note="internal",
        )

    def test_10_85_the_default_serializer_returns_the_whole_record(self):
        """
        Scenario 10.85: without a name, nothing is hidden.
        Given: a record with four business fields
        When: it is read with no ``serializer`` parameter
        Then: every field is present.

        The baseline the next scenario is measured against. A dashboard that
        asks for a narrower view has to be narrower than *something*, and this
        pins what that something is: the framework's auto-generated serializer,
        registered under "default".
        """
        item = self._record()

        response = self.client.get(self.url_detail(API_LAYER_SERIALIZER_CALC, item.pk))

        self.assertEqual(response.status_code, 200, msg=response.content[:400])
        payload = response.json()
        for field in ("name", "field_1", "field_2", "internal_note"):
            self.assertIn(
                field, payload,
                msg=(
                    f"The default serializer must expose {field!r}; it returned "
                    f"{sorted(payload)}. A dashboard narrowing this view can "
                    "only be verified against a known-wide one."
                ),
            )

    def test_10_86_a_named_serializer_narrows_the_field_list(self):
        """
        Scenario 10.86: the name on the model decides what a dashboard shows.
        Given: ``api_serializers = {"compact": ...}`` exposing three fields
        When: the record is read with ``?serializer=compact``
        Then: only those fields come back.

        This is the whole feature, and the reason the Streamlit embed takes a
        ``serializer`` argument at all: the field list lives on the model, so
        the dashboard names it rather than repeating it, and the two cannot
        drift apart.
        """
        item = self._record()

        response = self.client.get(
            self.url_detail(API_LAYER_SERIALIZER_CALC, item.pk),
            {"serializer": "compact"},
        )

        self.assertEqual(response.status_code, 200, msg=response.content[:400])
        payload = response.json()

        self.assertEqual(
            payload.get("name"), "Q1",
            msg=f"The named serializer must still return its own fields: {payload}.",
        )
        self.assertIn("field_1", payload)
        for hidden in ("field_2", "internal_note"):
            self.assertNotIn(
                hidden, payload,
                msg=(
                    f"{hidden!r} is not in the compact serializer's field list, so "
                    f"it must not be returned. Got {sorted(payload)}. A query "
                    "parameter that selects nothing would leave every dashboard "
                    "quietly showing the default field list."
                ),
            )

    def test_10_87_an_unknown_serializer_is_refused_not_ignored(self):
        """
        Scenario 10.87: a typo fails loudly.
        Given: a serializer name no model declares
        When: the record is read
        Then: the request is refused and the response names the serializer.

        Falling back to the default would be the worst outcome available: the
        dashboard would render, look plausible, and show the wrong field list
        for as long as nobody compared it against the model.
        """
        item = self._record()

        response = self.client.get(
            self.url_detail(API_LAYER_SERIALIZER_CALC, item.pk),
            {"serializer": "dashbaord"},
        )

        self.assertGreaterEqual(
            response.status_code, 400,
            msg=(
                "An unknown serializer must not silently fall back to the "
                f"default; it answered {response.status_code} with "
                f"{response.content[:200]!r}."
            ),
        )
        self.assertIn(
            "dashbaord", response.content.decode(errors="replace"),
            msg="The refusal must name the serializer that was not found.",
        )

    def test_10_88_the_map_carries_the_default_and_every_declared_name(self):
        """
        Scenario 10.88: declaring one serializer does not remove the default.
        Given: a model declaring ``api_serializers = {"compact": ...}``
        When: its serializer map is built
        Then: both "default" and "compact" are in it.

        The React table and every framework-internal lookup read the default;
        only the dashboard asks for the narrow one. A registration that replaced
        the default rather than adding to it would narrow the whole product to
        whatever one dashboard happened to want.
        """
        from lex.api.serializers.base_serializers import get_serializer_map_for_model

        serializers_map = get_serializer_map_for_model(ApiLayerSerializerCalc)

        self.assertIn(
            "default", serializers_map,
            msg=(
                "Declaring a named serializer must leave the default in place; "
                f"the map holds {sorted(serializers_map)}."
            ),
        )
        self.assertIn(
            "compact", serializers_map,
            msg=(
                "The declared name must be registered, or ?serializer=compact "
                f"could never resolve. Map holds {sorted(serializers_map)}."
            ),
        )
