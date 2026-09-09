import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class PriceTickerConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer streaming live price updates to clients.
    Clients can subscribe to all creature updates or a specific creature.
    """

    GROUP_ALL = "price_updates"

    async def connect(self):
        url_route = self.scope.get('url_route', {})
        self.creature_id = url_route.get('kwargs', {}).get('creature_id')
        self.groups_to_join = [self.GROUP_ALL]

        if self.creature_id:
            self.groups_to_join.append(f"price_updates_{self.creature_id}")

        for group in self.groups_to_join:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, 'groups_to_join', []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def price_update(self, event):
        """
        Handler for 'price_update' events sent to channel groups.
        Forwards the price update payload to the connected WebSocket client.
        """
        await self.send_json({
            "type": "price_update",
            "creature_id": event["creature_id"],
            "creature_name": event.get("creature_name", ""),
            "current_price": event["current_price"],
            "previous_close": event.get("previous_close", ""),
            "change": event.get("change", "0.00"),
            "change_pct": event.get("change_pct", "0.00"),
        })
