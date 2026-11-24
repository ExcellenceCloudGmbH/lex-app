import json
import logging

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from lex.api.consumers.auth import LexSocketAuthMixin
from lex.api.consumers.record_access import SocketReader
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

logger = logging.getLogger(__name__)


class UpdateCalculationStatusConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/calculation_status_update``: calculation states, for signed-in users (see ``auth``).

    A refused socket gets no snapshot of the active calculations either. A served one hears
    about the runs of records its user may read (see ``record_access``): in the snapshot on
    connect and in each state change, with its error message and traceback. Every state change
    still updates this process's store, whoever hears about it.
    """

    active_consumers = set()

    async def connect(self):
        if not await self.lex_socket_connect():
            return
        self.reader = SocketReader(self.scope)
        await self.channel_layer.group_add(f'update_calculation_status', self.channel_name)
        await self.accept()
        self.active_consumers.add(self)

        snapshot = await sync_to_async(ActiveCalculationStateStore.snapshot)()
        snapshot = await database_sync_to_async(self.reader.visible)(snapshot)
        await self.send(text_data=json.dumps({
            "type": "calculation_reconciliation",
            "payload": {
                "active_calculations": snapshot,
            },
        }))

    async def disconnect(self, close_code):
        await self.lex_socket_disconnect()
        await self.channel_layer.group_discard(f'update_calculation_status', self.channel_name)
        self.active_consumers.discard(self)
        await super().disconnect(close_code)

    async def _relay(self, message_type, payload):
        """Send a state change to this socket if its user may read the run's record."""
        if not await database_sync_to_async(self.reader.may_read)(payload.get('record_id')):
            return
        await self.send(text_data=json.dumps({
            'type': message_type,
            'payload': payload
        }))

    async def calculation_success(self, event):
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self._relay('calculation_success', payload)

    async def calculation_error(self, event):
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self._relay('calculation_error', payload)

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
        await self._relay('calculation_in_progress', payload)

    async def calculation_aborted(self, event):
        # Startup-reset path: a stuck IN_PROGRESS row was flipped to ABORTED
        # by ``model_registration._handle_calculation_model_reset`` on boot.
        payload = event['payload']
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self._relay('calculation_aborted', payload)

    async def calculation_cancelled(self, event):
        # User-initiated cancel path: ``CalculationModel.cancel()`` or a
        # cooperative ``CalculationCancelled`` exception bubbled out of
        # ``calculate()``.
        payload = event['payload']
        # Keep ASGI-process state store in sync (event may originate from a Celery worker)
        record_id = payload.get('record_id')
        if record_id:
            await sync_to_async(ActiveCalculationStateStore.clear)(record_id)
        await self._relay('calculation_cancelled', payload)

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
