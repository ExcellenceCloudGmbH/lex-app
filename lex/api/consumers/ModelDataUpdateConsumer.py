import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from lex.core.signals.ModelMutationSignal import MODEL_MUTATION_GROUP

logger = logging.getLogger(__name__)


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
        from lex.core import realtime

        # One shared group by default; the user's groups with USER_GROUPS/MUTATION_GROUPS.
        self.group_names = await sync_to_async(realtime.socket_groups)(self.group_name, (getattr(self, "scope", None) or {}).get("user"))
        for group in self.group_names:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, "group_names", [self.group_name]):
            await self.channel_layer.group_discard(group, self.channel_name)
        await super().disconnect(close_code)

    async def record_mutation(self, event):
        await self.send(text_data=json.dumps({
            "type": "record_mutation",
            "payload": event["payload"],
        }))
