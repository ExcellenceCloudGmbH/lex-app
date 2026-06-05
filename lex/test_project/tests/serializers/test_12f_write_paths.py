"""
Cluster 12f: Serializer write paths — M2M and nested FK.

Targets the many-to-many and FK write branches of
``lex.api.serializers.base_serializers.LexSerializer`` (missing lines
447–459, 472–478, 530–549 in the April 21 coverage report). These
branches handle the payload shapes the frontend actually sends when a
user edits a relationship in a form.

Intent (from docs/features/api-layer/ + LexSerializer docstring):

    * **M2M write on POST.** A payload like ``{"tags": [id1, id2]}``
      creates the related row and then attaches the listed tags in one
      round-trip. The through-table rows appear atomically; a later
      validation failure does not leave a dangling taggable with half
      its tags attached.
    * **M2M write on PATCH.** A subsequent PATCH with a different
      ``tags`` list **replaces** the set — previous through rows are
      removed, new ones added. The "add-only" anti-pattern would cause
      the frontend's "deselect" UX to silently drop. This is the
      contract the UI relies on.
    * **Nullable FK toggle.** POST / PATCH can set ``primary_tag`` to a
      pk (attach), to ``null`` (detach), and back to another pk
      (rewire) — exercising the FK-write branch on ``LexSerializer``.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 12f.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import (
    ALL_MODELS,
    TAGGABLE,
    TagItem,
    TaggableItem,
)

import pytest

pytestmark = pytest.mark.serializers


class TestCluster12f_WritePaths(E2ETestCase):
    """``/api/<model>/create/`` + ``/api/<model>/<pk>/`` for M2M + FK."""

    e2e_models = ALL_MODELS

    def _seed_tags(self):
        """Create three tags and return them by label."""
        return {
            "red":    TagItem.objects.create(label="red"),
            "blue":   TagItem.objects.create(label="blue"),
            "green":  TagItem.objects.create(label="green"),
        }

    # -- 12.29 ---------------------------------------------------------
    def test_12_29_post_creates_m2m_with_pk_list(self) -> None:
        """
        Scenario 12.29: POST with ``tags=[pk1, pk2]`` creates the row
        and attaches both tags atomically. The through-table is the
        source of truth: the test reads it back via the ORM so we
        catch any "serializer says ok, relation never persisted" bug.
        """
        tags = self._seed_tags()
        resp = self.client.post(
            self.url_create(TAGGABLE),
            data={
                "title": "tagged-on-create",
                "tags": [tags["red"].pk, tags["blue"].pk],
            },
            format="json",
        )
        self.assertIn(
            resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED),
            msg=f"POST must succeed; got {resp.status_code}: "
                f"{getattr(resp, 'data', resp.content)!r}",
        )

        item = TaggableItem.objects.get(title="tagged-on-create")
        attached = set(item.tags.values_list("label", flat=True))
        self.assertEqual(
            attached, {"red", "blue"},
            msg=(
                "M2M through-table must reflect the posted tag list. "
                f"Expected {{'red','blue'}}, got {attached!r}."
            ),
        )

    # -- 12.30 ---------------------------------------------------------
    def test_12_30_patch_replaces_m2m_set(self) -> None:
        """
        Scenario 12.30: PATCH with a different ``tags`` list REPLACES
        the existing set — it does not merge. This is the "deselect"
        contract the frontend UI depends on. An "add-only" bug would
        silently keep ``red`` after the user removed it.
        """
        tags = self._seed_tags()
        item = TaggableItem.objects.create(title="patcheable")
        item.tags.add(tags["red"], tags["blue"])

        resp = self.client.patch(
            self.url_detail(TAGGABLE, item.pk),
            data={"tags": [tags["green"].pk]},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            msg=f"PATCH must succeed; got {resp.status_code}: "
                f"{getattr(resp, 'data', resp.content)!r}",
        )

        item.refresh_from_db()
        attached = set(item.tags.values_list("label", flat=True))
        self.assertEqual(
            attached, {"green"},
            msg=(
                "PATCH with tags=[green] must REPLACE the set "
                "(not merge into {red,blue,green}). "
                f"Got {attached!r} — deselection leak."
            ),
        )

    # -- 12.31 ---------------------------------------------------------
    def test_12_31_fk_attach_detach_rewire(self) -> None:
        """
        Scenario 12.31: The nullable FK ``primary_tag`` must support
        the full lifecycle through POST + PATCH:

            (1) attach on create  (POST primary_tag=red)
            (2) rewire via PATCH  (PATCH primary_tag=blue)
            (3) detach via PATCH  (PATCH primary_tag=null)

        Each step is asserted by reading the row back via ORM, so a
        serializer that echoes the payload but doesn't persist the FK
        is caught.
        """
        tags = self._seed_tags()

        # (1) attach on create
        resp = self.client.post(
            self.url_create(TAGGABLE),
            data={"title": "fk-lifecycle", "primary_tag": tags["red"].pk},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 201))
        item = TaggableItem.objects.get(title="fk-lifecycle")
        self.assertEqual(
            item.primary_tag_id, tags["red"].pk,
            "Step 1 (attach on create) — primary_tag must point at 'red'",
        )

        # (2) rewire via PATCH
        resp = self.client.patch(
            self.url_detail(TAGGABLE, item.pk),
            data={"primary_tag": tags["blue"].pk},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Step 2 (rewire) PATCH failed: {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        item.refresh_from_db()
        self.assertEqual(
            item.primary_tag_id, tags["blue"].pk,
            "Step 2 (rewire) — primary_tag must now point at 'blue'",
        )

        # (3) detach via PATCH
        resp = self.client.patch(
            self.url_detail(TAGGABLE, item.pk),
            data={"primary_tag": None},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Step 3 (detach) PATCH failed: {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        item.refresh_from_db()
        self.assertIsNone(
            item.primary_tag_id,
            "Step 3 (detach) — primary_tag must be cleared to NULL. "
            f"Got {item.primary_tag_id!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
