import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

MODEL_MUTATION_GROUP = "model_data_update"


class ModelDataUpdateConsumer(AsyncWebsocketConsumer):
    """Fans generic CRUD ``record_mutation`` broadcasts out to clients.

    The backend emits a ``record_mutation`` message to the
    ``model_data_update`` group on every REST create / update / delete (see
    :mod:`lex.core.signals.ModelMutationSignal`). This consumer joins that
    group on connect and forwards each message verbatim to the browser, where
    the frontend refreshes any open list view scoped to the affected model.
    """

    group_name = MODEL_MUTATION_GROUP

    async def connect(self):
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await super().disconnect(close_code)

    async def record_mutation(self, event):
        await self.send(text_data=json.dumps({
            "type": "record_mutation",
            "payload": event["payload"],
        }))
