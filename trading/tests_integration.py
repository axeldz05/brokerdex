from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError

from account.models import Account
from creature.models import Creature, Ability
from .models import Portfolio, Order, Trade, PriceHistory
from .services import TradingEngine, PricingEngine


class TradingIntegrationTests(TestCase):
    """End-to-end integration tests for trading views."""

    def setUp(self):
        self.client = Client()
        self.user = Account.objects.create_user(
            username='trader', email='trader@test.com', password='testpass123'
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

        self.ability = Ability.objects.create(
            name='Thunderbolt', ability_type='electric',
            damage_class='special', power=90,
        )

        self.creature = Creature.objects.create(
            name='Pikachu',
            type='electric',
            current_price=Decimal('150.00'),
            previous_close=Decimal('145.00'),
            hp=35, attack=55, defense=40,
            special_attack=50, special_defense=50, speed=90,
            battle_cooldown=timedelta(hours=1),
            description='A test creature.',
        )
        self.creature.abilities.add(self.ability)

    def _login(self):
        self.client.login(username='trader', password='testpass123')

    def test_market_page_requires_login(self):
        response = self.client.get(reverse('trading:market'))
        self.assertEqual(response.status_code, 302)

    def test_market_page_shows_creatures(self):
        self._login()
        response = self.client.get(reverse('trading:market'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pikachu')
        self.assertContains(response, '$150.00')

    def test_creature_detail_shows_price_and_forms(self):
        self._login()
        url = reverse('trading:creature_detail', args=[self.creature.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pikachu')
        self.assertContains(response, '$150.00')
        self.assertContains(response, 'Buy')
        self.assertContains(response, 'Sell')
        self.assertContains(response, 'Limit Order')

    def test_market_buy_via_post(self):
        self._login()
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.BUY,
            'execution_type': 'MARKET',
            'quantity': '2',
        })
        expected_url = reverse('trading:creature_detail', args=[self.creature.pk])
        self.assertRedirects(response, expected_url)

        portfolio = Portfolio.objects.get(owner=self.user, creature=self.creature)
        self.assertEqual(portfolio.quantity, Decimal('2'))
        self.assertEqual(portfolio.average_cost, Decimal('150.00'))

    def test_market_sell_via_post(self):
        self._login()
        TradingEngine.execute_market_buy(self.user, self.creature, Decimal('3'))
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.SELL,
            'execution_type': 'MARKET',
            'quantity': '1',
        })
        expected_url = reverse('trading:creature_detail', args=[self.creature.pk])
        self.assertRedirects(response, expected_url)

        portfolio = Portfolio.objects.get(owner=self.user, creature=self.creature)
        self.assertEqual(portfolio.quantity, Decimal('2'))

    def test_limit_buy_via_post(self):
        self._login()
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.BUY,
            'execution_type': 'LIMIT',
            'quantity': '5',
            'limit_price': '120.00',
        })
        expected_url = reverse('trading:creature_detail', args=[self.creature.pk])
        self.assertRedirects(response, expected_url)

        order = Order.objects.get(account=self.user, creature=self.creature)
        self.assertEqual(order.order_type, Order.OrderType.BUY)
        self.assertEqual(order.execution_type, Order.ExecutionType.LIMIT)
        self.assertEqual(order.limit_price, Decimal('120.00'))
        self.assertEqual(order.status, Order.Status.OPEN)

    def test_limit_sell_via_post(self):
        self._login()
        TradingEngine.execute_market_buy(self.user, self.creature, Decimal('5'))
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.SELL,
            'execution_type': 'LIMIT',
            'quantity': '3',
            'limit_price': '180.00',
        })
        expected_url = reverse('trading:creature_detail', args=[self.creature.pk])
        self.assertRedirects(response, expected_url)

        order = Order.objects.get(
            account=self.user, creature=self.creature,
            order_type=Order.OrderType.SELL,
        )
        self.assertEqual(order.execution_type, Order.ExecutionType.LIMIT)
        self.assertEqual(order.limit_price, Decimal('180.00'))

    def test_cancel_order_via_post(self):
        self._login()
        order = TradingEngine.place_limit_order(
            self.user, self.creature,
            Order.OrderType.BUY, Decimal('2'), Decimal('100.00'),
        )
        response = self.client.post(
            reverse('trading:cancel_order', args=[order.pk])
        )
        self.assertRedirects(response, reverse('trading:orders'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_portfolio_shows_holdings(self):
        self._login()
        TradingEngine.execute_market_buy(self.user, self.creature, Decimal('4'))
        response = self.client.get(reverse('trading:portfolio'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pikachu')
        self.assertContains(response, '4')

    def test_order_history_shows_orders(self):
        self._login()
        TradingEngine.execute_market_buy(self.user, self.creature, Decimal('1'))
        response = self.client.get(reverse('trading:orders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pikachu')
        self.assertContains(response, 'Buy')
        self.assertContains(response, 'Filled')

    def test_price_history_api_returns_json(self):
        self._login()
        PricingEngine.record_price_snapshot(
            self.creature, PriceHistory.Interval.ONE_HOUR
        )
        url = reverse('trading:price_history_api', args=[self.creature.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = response.json()
        self.assertEqual(data['creature'], 'Pikachu')
        self.assertEqual(data['interval'], '1H')
        self.assertGreaterEqual(len(data['data']), 1)
        entry = data['data'][0]
        self.assertIn('timestamp', entry)
        self.assertIn('open', entry)
        self.assertIn('high', entry)
        self.assertIn('low', entry)
        self.assertIn('close', entry)
        self.assertIn('volume', entry)

    def test_insufficient_funds_via_view_shows_error(self):
        self._login()
        self.user.balance_cents = 100
        self.user.save()
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.BUY,
            'execution_type': 'MARKET',
            'quantity': '1',
        }, follow=True)
        self.assertContains(response, 'Insufficient funds')

    def test_sell_without_holdings_via_view_shows_error(self):
        self._login()
        response = self.client.post(reverse('trading:place_order'), {
            'creature': self.creature.pk,
            'order_type': Order.OrderType.SELL,
            'execution_type': 'MARKET',
            'quantity': '1',
        }, follow=True)
        self.assertContains(response, "don")
        self.assertContains(response, "own any")

    def test_full_buy_sell_cycle(self):
        """Complete cycle: deposit → buy → hold → sell → verify P&L."""
        self._login()

        initial_balance = self.user.balance_cents

        buy_trade = TradingEngine.execute_market_buy(
            self.user, self.creature, Decimal('10')
        )
        self.user.refresh_from_db()
        balance_after_buy = self.user.balance_cents

        self.creature.current_price = Decimal('200.00')
        self.creature.save()

        sell_trade = TradingEngine.execute_market_sell(
            self.user, self.creature, Decimal('10')
        )
        self.user.refresh_from_db()
        balance_after_sell = self.user.balance_cents

        buy_cost = 10 * Decimal('150.00')
        buy_commission = (buy_cost * Decimal('0.015')).quantize(Decimal('0.01'))
        total_buy = buy_cost + buy_commission

        sell_revenue = 10 * Decimal('200.00')
        sell_commission = (sell_revenue * Decimal('0.015')).quantize(Decimal('0.01'))
        net_sell = sell_revenue - sell_commission

        expected_final = initial_balance - int(total_buy * 100) + int(net_sell * 100)
        self.assertEqual(balance_after_sell, expected_final)
        self.assertFalse(
            Portfolio.objects.filter(owner=self.user, creature=self.creature).exists()
        )
