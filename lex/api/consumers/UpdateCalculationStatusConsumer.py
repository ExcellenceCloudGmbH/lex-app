import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

logger = logging.getLogger(__name__)


class UpdateCalculationStatusConsumer(AsyncWebsocketConsumer):
    active_consumers = set()

    async def connect(self):
        await self.channel_layer.group_add(f'update_calculation_status', self.channel_name)
        await self.accept()
        self.active_consumers.add(self)

        snapshot = await sync_to_async(ActiveCalculationStateStore.snapshot)()
        await self.send(text_data=json.dumps({
            "type": "calculation_reconciliation",
            "payload": {
                "active_calculations": snapshot,
            },
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(f'update_calculation_status', self.channel_name)
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
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self.send(text_data=json.dumps({
            'type': 'calculation_aborted',
            'payload': payload
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
