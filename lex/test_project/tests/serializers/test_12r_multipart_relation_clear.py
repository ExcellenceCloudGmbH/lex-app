"""Cluster 12r — clearing relations and nullable values through a multipart update.

Intent: a user who empties a foreign key (or any nullable field) in the edit
form and saves must see the value gone. The admin frontend sends every update
as multipart ``PATCH``, which cannot carry ``null``: the cleared value was
dropped from the FormData, the backend saw nothing to change, answered the
no-op 200, and the old value stayed — with no error anywhere (LEX-1582). The
frontend now sends an explicit empty value for a cleared field, and an empty
list as ``"[]"`` for a cleared many-to-many. This cluster pins the backend
side of that contract:

* ``fk=""`` clears a nullable foreign key; omitting the key keeps it.
* an empty value on a required field answers 400, never a silent no-op.
* ``m2m="[]"`` clears a many-to-many; repeated keys still replace the set.
* ``date=""`` clears a nullable scalar field.

Cluster 12r — scenarios 12.81–12.87. Type: E.
Covers: lex/api/serializers/base_serializers.py (LexManyRelatedField,
        LexSerializer.many_related_field_class),
        lex/api/views/model_entries/One.py (update, no-op detection).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12r_multipart_relation_clear.py -v
"""

from __future__ import annotations

import datetime

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    TAGGABLE,
    WIDE,
    RelatedItem,
    TagItem,
    TaggableItem,
    WideItem,
)

pytestmark = pytest.mark.serializers


class TestCluster12r_MultipartRelationClear(E2ETestCase):
    """Multipart PATCH: empty value clears, omitted key keeps."""

    e2e_models = ALL_MODELS

    def _patch(self, model: str, pk, data):
        return self.client.patch(self.url_detail(model, pk), data=data, format="multipart")

    def _taggable(self, title: str = "taggable") -> tuple[TaggableItem, TagItem, TagItem]:
        red = TagItem.objects.create(label=f"{title}-red")
        blue = TagItem.objects.create(label=f"{title}-blue")
        item = TaggableItem.objects.create(title=title, primary_tag=red)
        item.tags.set([red, blue])
        return item, red, blue

    def test_12_81_empty_value_clears_a_nullable_fk(self) -> None:
        """
        Scenario 12.81: an empty value on a nullable FK clears it (LEX-1582).
        Given: a record whose nullable FK points at a row
        When: PATCH (multipart) with the FK sent as an empty string
        Then: 200, the FK is NULL in the database and in the response
        """
        item, _red, _blue = self._taggable("fk-clear")

        resp = self._patch(TAGGABLE, item.pk, {"primary_tag": ""})

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Clearing the FK must be a valid update, got {resp.status_code}: "
            f"{getattr(resp, 'data', None)!r}",
        )
        item.refresh_from_db()
        self.assertIsNone(
            item.primary_tag_id,
            "The cleared FK must not survive the save (LEX-1582: it silently did).",
        )
        self.assertIsNone(
            resp.data.get("primary_tag"),
            f"The response must show the cleared FK, got {resp.data.get('primary_tag')!r}.",
        )

    def test_12_82_omitting_the_fk_keeps_it(self) -> None:
        """
        Scenario 12.82: a payload without the FK key leaves the FK alone.
        Given: a record whose nullable FK points at a row
        When: PATCH (multipart) changing only another field
        Then: 200, the other field changes and the FK is untouched
        """
        item, red, _blue = self._taggable("fk-keep")

        resp = self._patch(TAGGABLE, item.pk, {"title": "fk-keep-renamed"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        item.refresh_from_db()
        self.assertEqual(item.title, "fk-keep-renamed")
        self.assertEqual(
            item.primary_tag_id, red.pk,
            "Omitting the FK key must keep the stored FK (PATCH semantics).",
        )

    def test_12_83_empty_value_on_a_required_field_is_refused_not_ignored(self) -> None:
        """
        Scenario 12.83: emptying a required field answers 400.
        Given: a record with a required (non-blank) field
        When: PATCH (multipart) with that field sent empty — the same marker
              the frontend now sends for any cleared input
        Then: 400 with a field error naming it — never a silent 200 no-op
        """
        item, _red, _blue = self._taggable("required-clear")

        resp = self._patch(TAGGABLE, item.pk, {"title": ""})

        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Emptying a required field must be refused, got {resp.status_code}: "
            f"{getattr(resp, 'data', None)!r}",
        )
        item.refresh_from_db()
        self.assertEqual(item.title, "required-clear", "A refused update must not write.")

    def test_12_84_empty_list_marker_clears_a_m2m(self) -> None:
        """
        Scenario 12.84: ``"[]"`` on a many-to-many clears the set.
        Given: a record with two tags attached
        When: PATCH (multipart) with ``tags="[]"`` — the frontend's marker for
              an emptied multi-select, since FormData cannot carry an empty list
        Then: 200 and no tag is attached
        """
        item, _red, _blue = self._taggable("m2m-clear")

        resp = self._patch(TAGGABLE, item.pk, {"tags": "[]"})

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Clearing the M2M must be a valid update, got {resp.status_code}: "
            f"{getattr(resp, 'data', None)!r}",
        )
        item.refresh_from_db()
        self.assertEqual(list(item.tags.all()), [], "The cleared M2M must be empty.")

    def test_12_85_empty_value_clears_a_m2m(self) -> None:
        """
        Scenario 12.85: a bare empty value on a many-to-many clears the set too.
        Given: a record with two tags attached
        When: PATCH (multipart) with ``tags=""``
        Then: 200 and no tag is attached
        """
        item, _red, _blue = self._taggable("m2m-clear-empty")

        resp = self._patch(TAGGABLE, item.pk, {"tags": ""})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        item.refresh_from_db()
        self.assertEqual(list(item.tags.all()), [])

    def test_12_86_repeated_keys_still_replace_a_m2m(self) -> None:
        """
        Scenario 12.86: repeated multipart keys still replace the M2M set.
        Given: a record with two tags attached
        When: PATCH (multipart) with ``tags`` repeated for one new tag
        Then: 200 and exactly that tag is attached
        """
        item, _red, _blue = self._taggable("m2m-replace")
        green = TagItem.objects.create(label="m2m-replace-green")

        resp = self._patch(TAGGABLE, item.pk, {"tags": [green.pk]})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        item.refresh_from_db()
        self.assertEqual([tag.pk for tag in item.tags.all()], [green.pk])

    def test_12_87_empty_value_clears_a_nullable_scalar(self) -> None:
        """
        Scenario 12.87: an empty value clears a nullable non-relation field.
        Given: a record with a nullable date set and a nullable FK set
        When: PATCH (multipart) with both sent as empty strings
        Then: 200 and both are NULL
        """
        related = RelatedItem.objects.create(name="wide-target")
        wide = WideItem.objects.create(
            name="wide-clear", created_on=datetime.date(2026, 1, 2), related=related,
        )

        resp = self._patch(WIDE, wide.pk, {"created_on": "", "related": ""})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        wide.refresh_from_db()
        self.assertIsNone(wide.created_on)
        self.assertIsNone(wide.related_id)
