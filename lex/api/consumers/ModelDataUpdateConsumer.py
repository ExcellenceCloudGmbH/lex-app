import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from lex.api.consumers.auth import LexSocketAuthMixin

logger = logging.getLogger(__name__)

MODEL_MUTATION_GROUP = "model_data_update"


class ModelDataUpdateConsumer(LexSocketAuthMixin, AsyncWebsocketConsumer):
    """Fans generic CRUD ``record_mutation`` broadcasts out to clients.

    The backend emits a ``record_mutation`` message to the
    ``model_data_update`` group on every REST create / update / delete (see
    :mod:`lex.core.signals.ModelMutationSignal`). This consumer joins that
    group on connect and forwards each message verbatim to the browser, where
    the frontend refreshes any open list view scoped to the affected model.
    Only for signed-in users (see :mod:`lex.api.consumers.auth`).
    """

    group_name = MODEL_MUTATION_GROUP

    async def connect(self):
        if not await self.lex_socket_connect():
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.lex_socket_disconnect()
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await super().disconnect(close_code)

    async def record_mutation(self, event):
        await self.send(text_data=json.dumps({
            "type": "record_mutation",
            "payload": event["payload"],
        }))
