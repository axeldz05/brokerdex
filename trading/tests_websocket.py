from decimal import Decimal

from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase

from creature.models import Creature
from trading.consumers import PriceTickerConsumer
from trading.services import PricingEngine


class PriceTickerWebsocketTests(TestCase):
    async def test_websocket_connect_and_receives_broadcast(self):
        communicator = WebsocketCommunicator(
            PriceTickerConsumer.as_asgi(),
            "/ws/prices/",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "price_updates",
            {
                "type": "price_update",
                "creature_id": "test-uuid-1234",
                "creature_name": "Pikachu",
                "current_price": "155.00",
                "previous_close": "150.00",
                "change": "5.00",
                "change_pct": "3.33",
            },
        )

        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "price_update")
        self.assertEqual(response["creature_id"], "test-uuid-1234")
        self.assertEqual(response["creature_name"], "Pikachu")
        self.assertEqual(response["current_price"], "155.00")
        self.assertEqual(response["change"], "5.00")
        self.assertEqual(response["change_pct"], "3.33")

        await communicator.disconnect()


class PricingEngineBroadcastIntegrationTests(TransactionTestCase):
    def test_update_creature_price_triggers_broadcast_on_commit(self):
        creature = Creature.objects.create(
            name="Bulbasaur",
            type="grass",
            current_price=Decimal("100.00"),
            previous_close=Decimal("95.00"),
            hp=45,
            attack=49,
            defense=49,
            special_attack=65,
            special_defense=65,
            speed=45,
        )

        new_price = PricingEngine.update_creature_price(creature)
        self.assertIsNotNone(new_price)
        self.assertGreater(new_price, Decimal("0"))
