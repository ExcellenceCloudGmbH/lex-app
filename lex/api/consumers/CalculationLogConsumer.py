import json

from channels.generic.websocket import AsyncWebsocketConsumer


class CalculationLogConsumer(AsyncWebsocketConsumer):
    active_consumers = set()

    async def connect(self):
        self.calculation_id = self.scope['url_route']['kwargs']['calculationId']
        self.calculation_record = self.calculation_id.split("-")[0]

        await self.channel_layer.group_add(f'{self.calculation_record}', self.channel_name)
        await self.accept()
        self.active_consumers.add(self)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(f'{self.calculation_record}', self.channel_name)
        await super().disconnect(close_code)

    async def calculation_log_real_time(self, event):
        payload = event['payload']
        await self.send(text_data=json.dumps({
            'type': 'calculation_log_real_time',
            "logs": payload
        }))

    @classmethod
    async def disconnect_all(cls):
        for consumer in cls.active_consumers.copy():
            await consumer.disconnect(None)
