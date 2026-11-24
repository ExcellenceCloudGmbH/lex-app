import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin
from lex.api.consumers.record_access import SocketReader


class CalculationsConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/calculations``: calculation ids and notifications, for signed-in users (see ``auth``).

    A run's id (``calculation_id`` event) reaches a socket whose user may read its record; a
    notification that names no record reaches every signed-in socket (see ``record_access``).
    """

    active_consumers = set()

    async def connect(self):
        if not await self.lex_socket_connect():
            return
        self.reader = SocketReader(self.scope)
        await self.accept()
        await self.channel_layer.group_add(
            "calculations",
            self.channel_name
        )
        self.active_consumers.add(self)

    async def disconnect(self, close_code):
        await self.lex_socket_disconnect()
        await self.channel_layer.group_discard(
            "calculations",
            self.channel_name
        )
        self.active_consumers.discard(self)
        await super().disconnect(close_code)

    async def _may_hear(self, payload) -> bool:
        """A message naming a record goes to who may read it; any other to who is signed in."""
        payload = payload if isinstance(payload, dict) else {}
        record = payload.get("calculation_record") or payload.get("record_id")
        if record:
            return await database_sync_to_async(self.reader.may_read)(record)
        return await database_sync_to_async(self.reader.signed_in)()

    async def calculation_id(self, event):
        if not await self._may_hear(event.get('payload')):
            return
        await self.send(text_data=json.dumps(event))

    async def calculation_notification(self, event):
        payload = event['payload']
        if not await self._may_hear(payload):
            return
        await self.send(text_data=json.dumps({
            'type': 'calculation_notification',
            'payload': payload
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
