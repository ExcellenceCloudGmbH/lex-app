"""Generic model-mutation broadcast for plain CRUD.

The framework already broadcasts *calculation* state changes over the
``update_calculation_status`` channel group (see ``CalculationSignals.py``).
Ordinary CRUD on a non-``CalculationModel`` record, however, produced no
WebSocket traffic at all — so a list view open elsewhere (another tab, another
user, or a side-by-side embedded view) had no way to know a row was created,
updated, or deleted and silently went stale until a manual refresh.

This module is the single emission point for a lightweight ``record_mutation``
broadcast on the ``model_data_update`` group. The matching consumer
(:class:`~lex.api.consumers.ModelDataUpdateConsumer.ModelDataUpdateConsumer`)
fans it out to connected clients, and the frontend turns it into a server-side
grid refresh scoped to the affected model.

The broadcast is deferred with :func:`django.db.transaction.on_commit` so it
only fires once the mutating transaction has committed — otherwise a client
could refresh and not yet see the row it was told about.
"""

import logging

from django.db import transaction
from lex.utilities.channel_layer import sync_channel_group_send

logger = logging.getLogger(__name__)

MODEL_MUTATION_GROUP = "model_data_update"
MODEL_MUTATION_TYPE = "record_mutation"


def build_model_mutation_message(model_name, action, record_id=None):
    """Build the channel message describing a single CRUD mutation.

    Args:
        model_name: lowercased model name (``instance._meta.model_name``),
            matched against the frontend's active resource to scope the refresh.
        action: ``"created"`` | ``"updated"`` | ``"deleted"``.
        record_id: optional ``"<model_name>_<pk>"`` identifier.

    Returns:
        A dict in the ``{"type": ..., "payload": {...}}`` shape the consumer
        dispatches on.
    """
    payload = {"model_name": model_name, "action": action}
    if record_id is not None:
        payload["record_id"] = record_id
    return {"type": MODEL_MUTATION_TYPE, "payload": payload}


def broadcast_model_mutation(model_name, action, record_id=None):
    """Broadcast a CRUD mutation after the current transaction commits.

    Safe to call from any synchronous context (REST view, signal handler).
    No-ops when ``model_name`` is empty. The actual send is registered via
    ``transaction.on_commit`` so clients only learn about a change once it is
    durably committed.
    """
    if not model_name:
        return

    message = build_model_mutation_message(model_name, action, record_id)
    transaction.on_commit(
        lambda: sync_channel_group_send(MODEL_MUTATION_GROUP, message)
    )
