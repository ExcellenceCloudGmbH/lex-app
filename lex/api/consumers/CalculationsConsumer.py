import json

from channels.generic.websocket import AsyncWebsocketConsumer


class CalculationsConsumer(AsyncWebsocketConsumer):
    active_consumers = set()
    async def connect(self):
        await self.accept()
        await self.channel_layer.group_add(
            "calculations",
            self.channel_name
        )
        self.active_consumers.add(self)
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            "calculations",
            self.channel_name
        )
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
