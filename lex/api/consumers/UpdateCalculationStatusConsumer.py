import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

logger = logging.getLogger(__name__)


def _snapshot_for(user):
    """Active calculations the socket's user may hear about (all, unless scoped)."""
    from django.apps import apps

    from lex.core import realtime

    snapshot = ActiveCalculationStateStore.snapshot()
    if not realtime.scoped():
        return snapshot
    allowed = set(realtime.socket_groups("update_calculation_status", user))
    visible = []
    for entry in snapshot:
        try:
            model = apps.get_model(entry.get("model_label"))
            instance = model._default_manager.filter(pk=entry.get("record_pk")).first()
        except Exception:
            instance = None
        if instance is not None and allowed & set(realtime.event_groups("update_calculation_status", instance)):
            visible.append(entry)
    return visible


class UpdateCalculationStatusConsumer(AsyncWebsocketConsumer):
    active_consumers = set()

    async def connect(self):
        from lex.core import realtime

        user = (getattr(self, "scope", None) or {}).get("user")
        self.group_names = await sync_to_async(realtime.socket_groups)('update_calculation_status', user)
        for group in self.group_names:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()
        self.active_consumers.add(self)

        snapshot = await sync_to_async(_snapshot_for)(user)
        await self.send(text_data=json.dumps({
            "type": "calculation_reconciliation",
            "payload": {
                "active_calculations": snapshot,
            },
        }))

    async def disconnect(self, close_code):
        for group in getattr(self, "group_names", ['update_calculation_status']):
            await self.channel_layer.group_discard(group, self.channel_name)
        self.active_consumers.discard(self)
        await super().disconnect(close_code)

    async def calculation_success(self, event):
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self.send(text_data=json.dumps({
            'type': 'calculation_success',
            'payload': payload
        }))

    async def calculation_error(self, event):
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self.send(text_data=json.dumps({
            'type': 'calculation_error',
            'payload': payload
        }))

    async def calculation_in_progress(self, event):
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.mark_in_progress)(
                record_id=record_id,
                calculation_id=payload.get('calculation_id'),
                record=payload.get('record'),
            )
        await self.send(text_data=json.dumps({
            'type': 'calculation_in_progress',
            'payload': payload
        }))

    async def calculation_aborted(self, event):
        # Startup-reset path: a stuck IN_PROGRESS row was flipped to ABORTED
        # by ``model_registration._handle_calculation_model_reset`` on boot.
        payload = event['payload']
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self.send(text_data=json.dumps({
            'type': 'calculation_aborted',
            'payload': payload
        }))

    async def calculation_cancelled(self, event):
        # User-initiated cancel path: ``CalculationModel.cancel()`` or a
        # cooperative ``CalculationCancelled`` exception bubbled out of
        # ``calculate()``.
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self.send(text_data=json.dumps({
            'type': 'calculation_cancelled',
            'payload': payload
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
