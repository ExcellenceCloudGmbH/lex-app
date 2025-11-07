import json

from channels.generic.websocket import AsyncWebsocketConsumer


class BackendHealthConsumer(AsyncWebsocketConsumer):
    active_consumers = set()
    async def connect(self):
        await self.accept()
        self.active_consumers.add(self)
    async def disconnect(self, close_code):
        await super().disconnect(close_code)

    async def receive(self, text_data):
        await self.send(text_data=json.dumps({
            'status': "Healthy :)"
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
