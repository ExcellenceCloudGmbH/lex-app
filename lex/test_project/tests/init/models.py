"""Models for Cluster 1 — Project Bootstrap & maintenance commands.

``IncidentDatetimeItem`` exercises the ``rebase_incident_datetimes`` management
command. It carries a user-entered ``event_at`` (the kind of field the incident
corrupted) alongside the LexModel-managed ``created_at`` / ``edited_at``, so a
test can prove the command re-anchors the former and leaves the latter untouched.

Rule #3: no cross-cluster imports — models live here and are imported by this
cluster's test modules only.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from lex.core.models.LexModel import LexModel


class IncidentDatetimeItem(LexModel):
    """A customer-shaped model: a user datetime plus managed timestamps."""

    name = models.CharField(max_length=100)
    # User-entered instant — the class of field the TIME_ZONE incident corrupted.
    event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"


class IncidentScanItem(LexModel):
    """A project-shaped model for ``lex incident_tz scan``.

    ``due_on`` holds a calendar date a user picks, ``meets_at`` a time of day a
    user types, and ``synced_at`` is written by the clock — the three kinds of
    values the scan must tell apart.
    """

    name = models.CharField(max_length=100)
    due_on = models.DateTimeField(null=True, blank=True)
    meets_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "lex_app"


ALL_MODELS = [IncidentDatetimeItem]
SCAN_MODELS = [IncidentScanItem]
INCIDENT = "incidentdatetimeitem"
