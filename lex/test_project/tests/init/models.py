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

from lex.core.models.LexModel import LexModel


class IncidentDatetimeItem(LexModel):
    """A customer-shaped model: a user datetime plus managed timestamps."""

    name = models.CharField(max_length=100)
    # User-entered instant — the class of field the TIME_ZONE incident corrupted.
    event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"


ALL_MODELS = [IncidentDatetimeItem]
INCIDENT = "incidentdatetimeitem"
