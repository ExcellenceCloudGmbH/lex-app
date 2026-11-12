
import json

from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin


# from lex.lex_app.LexLogger.LexLogger import LexLogger


class LogConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """``ws/logs``: the LexLogger stream, for signed-in users (see ``auth``)."""

    # logger = LexLogger()
    socket = None

    async def connect(self):
        if not await self.lex_socket_connect():
            return
        if self.socket is not None:
            await self.socket.disconnect(None)
        self.socket = self
        await self.channel_layer.group_add("log_group", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.lex_socket_disconnect()
        await self.channel_layer.group_discard("log_group", self.channel_name)
        await super().disconnect(close_code)


    async def log_message(self, event):
        await self.send(text_data=json.dumps({
            'id': event.get('id', 'N/A'),
            'logId': event.get('logId', 'N/A'),
            'level': event['level'],
            'message': event['message'],
            'timestamp': event['timestamp'],
            'logName': event.get('logName', 'N/A'),
            'triggerName': event.get('triggerName', 'N/A'),
            'method': event.get('method', 'N/A'),
            'logDetails': event.get('logDetails', 'N/A'),
        }))
    async def receive(self, text_data):
        await self.send(text_data=json.dumps({
            'STATUS': "LexLogger v1.0.0 Created By Hazem Sahbani"
        }))


    # async def log_message(self, event):
    #     await self.send(text_data=json.dumps({
    #         'message': message,
    #     }))
