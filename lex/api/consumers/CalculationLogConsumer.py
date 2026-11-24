import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin
from lex.api.consumers.record_access import SocketReader


class CalculationLogConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/calculation_logs/<record>-<run>``: the live log lines of a record's calculations.

    For signed-in users (see ``auth``) who may read the record (see ``record_access``). The
    lines are broadcast to the record's group (``<model>_<pk>``); a socket for a record its user
    may not read never joins it, stays open and hears nothing, like a quiet run.
    """

    active_consumers = set()

    async def connect(self):
        # Parsed first: disconnect() leaves this group whether or not the socket was served.
        self.calculation_id = self.scope['url_route']['kwargs']['calculationId']
        self.calculation_record = self.calculation_id.split("-")[0]
        if not await self.lex_socket_connect():
            return

        reader = SocketReader(self.scope)
        if await database_sync_to_async(reader.may_read)(self.calculation_record):
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
