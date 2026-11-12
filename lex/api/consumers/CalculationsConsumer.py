import json

from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin


class CalculationsConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/calculations``: calculation ids and notifications, for signed-in users (see ``auth``)."""

    active_consumers = set()

    async def connect(self):
        if not await self.lex_socket_connect():
            return
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

    async def calculation_id(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps(event))

    async def calculation_notification(self, event):
        payload = event['payload']
        await self.send(text_data=json.dumps({
            'type': 'calculation_notification',
            'payload': payload
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
