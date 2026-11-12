import json

from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin


class CalculationLogConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/calculation_logs/<id>``: a calculation's live log lines, for signed-in users (see ``auth``)."""

    active_consumers = set()

    async def connect(self):
        # Parsed first: disconnect() leaves this group whether or not the socket was served.
        self.calculation_id = self.scope['url_route']['kwargs']['calculationId']
        self.calculation_record = self.calculation_id.split("-")[0]
        if not await self.lex_socket_connect():
            return

        await self.channel_layer.group_add(f'{self.calculation_record}', self.channel_name)
        await self.accept()
        self.active_consumers.add(self)

    async def disconnect(self, close_code):
        await self.lex_socket_disconnect()
        await self.channel_layer.group_discard(f'{self.calculation_record}', self.channel_name)
        self.active_consumers.discard(self)
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
