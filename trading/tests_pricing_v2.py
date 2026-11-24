from decimal import Decimal
from django.test import TestCase

from account.models import Account
from creature.models import Creature
from trading.models import Order, Portfolio, Trade
from trading.services import PricingEngine, TradingEngine
from trading.simulation.strategies import ValueStrategy


class PricingEngineV2TestCase(TestCase):
    def setUp(self):
        self.buyer = Account.objects.create_user(
            username="v2_buyer",
            email="v2_buyer@test.io",
            password="testpass123",
        )
        self.buyer.balance_cents = 100_000_00
        self.buyer.save(update_fields=["balance_cents"])

        self.seller = Account.objects.create_user(
            username="v2_seller",
            email="v2_seller@test.io",
            password="testpass123",
        )
        self.seller.balance_cents = 100_000_00
        self.seller.save(update_fields=["balance_cents"])

        self.creature = Creature.objects.create(
            name="V2Mon",
            type="fire",
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=100_000_00,
            pool_supply=Decimal("1000.00000000"),
            hp=50,
            attack=50,
            defense=50,
            description="Pricing V2 Test Creature",
        )

    def test_rarity_multiplier_eliminated_from_spot_price(self):
        # Creature with reference price $100.00 has spot price $100.00
        normal_price = PricingEngine.calculate_price(self.creature)
        self.assertEqual(normal_price, Decimal("100.00"))

        # Legendary creature must NOT multiply spot price by 1.5
        self.creature.is_legendary = True
        legendary_price = PricingEngine.calculate_price(self.creature)
        self.assertEqual(legendary_price, Decimal("100.00"))

        # Mythical creature must NOT multiply spot price by 2.0
        self.creature.is_legendary = False
        self.creature.is_mythical = True
        mythical_price = PricingEngine.calculate_price(self.creature)
        self.assertEqual(mythical_price, Decimal("100.00"))

    def test_trade_records_aggressor_side_on_market_orders(self):
        # Market buy -> aggressor_side is BUY
        buy_trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal("2")
        )
        self.assertEqual(buy_trade.aggressor_side, Trade.AggressorSide.BUY)

        # Market sell -> aggressor_side is SELL
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal("5"),
            average_cost=Decimal("100.00"),
        )
        sell_trade = TradingEngine.execute_market_sell(
            self.seller, self.creature, Decimal("2")
        )
        self.assertEqual(sell_trade.aggressor_side, Trade.AggressorSide.SELL)

    def test_p2p_trade_records_taker_as_aggressor(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal("10"),
            average_cost=Decimal("100.00"),
        )

        # Seller rests limit sell order first (Maker)
        TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal("3"),
            Decimal("100.00"),
        )

        # Buyer executes market buy (Taker / Aggressor)
        trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal("3")
        )
        self.assertIsNotNone(trade.buyer_id)
        self.assertIsNotNone(trade.seller_id)
        # Buyer was the aggressor that triggered the fill
        self.assertEqual(trade.aggressor_side, Trade.AggressorSide.BUY)

    def test_market_delta_order_flow_imbalance_formula(self):
        # With V_buy = 20, V_sell = 0, V0 = 10:
        # delta = 0.05 * (20 - 0) / (20 + 0 + 10) = 0.05 * (20 / 30) = 0.0333333...
        Trade.objects.create(
            buyer=self.buyer,
            seller=None,
            creature=self.creature,
            quantity=Decimal("20"),
            price_per_unit=Decimal("100.00"),
            commission=Decimal("30.00"),
            aggressor_side=Trade.AggressorSide.BUY,
        )

        delta = PricingEngine.calculate_market_delta(self.creature)
        expected = Decimal("0.05") * (Decimal("20") / Decimal("30"))
        self.assertAlmostEqual(float(delta), float(expected), places=4)

        # Now add 20 units of SELL aggressor volume:
        # V_buy = 20, V_sell = 20 -> delta should be 0.00
        Trade.objects.create(
            buyer=None,
            seller=self.seller,
            creature=self.creature,
            quantity=Decimal("20"),
            price_per_unit=Decimal("100.00"),
            commission=Decimal("30.00"),
            aggressor_side=Trade.AggressorSide.SELL,
        )

        delta_balanced = PricingEngine.calculate_market_delta(self.creature)
        self.assertEqual(delta_balanced, Decimal("0"))

    def test_value_strategy_does_not_loop_dump_legendaries(self):
        # Altie: legendary with reference_price = 3000.00 and current_price = 3000.00
        altie = Creature.objects.create(
            name="AltieTest",
            type="dragon",
            current_price=Decimal("3000.00"),
            previous_close=Decimal("3000.00"),
            pricing_reference_price=Decimal("3000.00"),
            is_legendary=True,
            reserve_cents=300_000_00,
            pool_supply=Decimal("100.00000000"),
        )
        Portfolio.objects.create(
            owner=self.seller,
            creature=altie,
            quantity=Decimal("2.0"),
            average_cost=Decimal("3000.00"),
        )

        # Because rarity multiplier is removed from spot price, spot price remains 3000.00 (0% premium)
        # ValueStrategy requires >= 15% premium to trigger sell
        strategy = ValueStrategy()
        event = strategy.evaluate_and_execute(self.seller, [altie])
        self.assertIsNone(event)
