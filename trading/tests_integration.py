from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError

from account.models import Account
from creature.models import Creature, Ability, Battle, Incubation, EggTemplate
from .models import Portfolio, Order, Trade, PriceHistory, MarketIndex, Notification
from .services import TradingEngine, PricingEngine, MarketIndicesService, VolatilityService


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


class MarketIndicesIntegrationTests(TestCase):
    """Integration tests for market indices."""

    def setUp(self):
        self.client = Client()
        self.user = Account.objects.create_user(
            username='index_user', email='idx@test.com', password='testpass123'
        )

        self.ability = Ability.objects.create(
            name='QuickAttack', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.fire_creature = Creature.objects.create(
            name='Charmander', type='fire',
            current_price=Decimal('150.00'),
            previous_close=Decimal('145.00'),
            hp=39, attack=52, defense=43,
            battle_cooldown=timedelta(hours=1),
            description='Fire lizard.',
        )
        self.fire_creature.abilities.add(self.ability)

        self.fire_creature2 = Creature.objects.create(
            name='Vulpix', type='fire',
            current_price=Decimal('200.00'),
            previous_close=Decimal('190.00'),
            hp=38, attack=41, defense=40,
            battle_cooldown=timedelta(hours=1),
            description='Fire fox.',
        )
        self.fire_creature2.abilities.add(self.ability)

        self.water_creature = Creature.objects.create(
            name='Squirtle', type='water',
            current_price=Decimal('180.00'),
            previous_close=Decimal('175.00'),
            hp=44, attack=48, defense=65,
            battle_cooldown=timedelta(hours=1),
            description='Water turtle.',
        )
        self.water_creature.abilities.add(self.ability)

    def _login(self):
        self.client.login(username='index_user', password='testpass123')

    def test_type_index_calculation(self):
        index = MarketIndicesService.calculate_type_index('fire')
        self.assertIsNotNone(index)
        self.assertEqual(index.creature_type, 'fire')
        expected_value = (Decimal('150.00') + Decimal('200.00')) / Decimal('2')
        self.assertEqual(index.value, expected_value)
        self.assertEqual(index.creature_count, 2)

    def test_all_indices_calculation(self):
        indices = MarketIndicesService.calculate_all_indices()
        types = {i.creature_type for i in indices}
        self.assertIn('fire', types)
        self.assertIn('water', types)

    def test_index_change_pct(self):
        index = MarketIndicesService.calculate_type_index('fire')
        self.assertEqual(index.change_pct, Decimal('0'))

        index2 = MarketIndicesService.calculate_type_index('fire')
        fire_avg = (Decimal('150.00') + Decimal('200.00')) / Decimal('2')
        expected_change = Decimal('0')
        self.assertEqual(index2.change_pct, expected_change)

    def test_market_indices_page_requires_login(self):
        response = self.client.get(reverse('trading:market_indices'))
        self.assertEqual(response.status_code, 302)

    def test_market_indices_page_shows_indices(self):
        self._login()
        MarketIndicesService.calculate_type_index('fire')
        response = self.client.get(reverse('trading:market_indices'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fire-Type Index')
        self.assertContains(response, '175.00')

    def test_market_indices_api(self):
        self._login()
        MarketIndicesService.calculate_type_index('fire')
        response = self.client.get(reverse('trading:market_indices_api'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('indices', data)
        self.assertGreaterEqual(len(data['indices']), 1)
        self.assertEqual(data['indices'][0]['type'], 'fire')
        self.assertEqual(data['indices'][0]['type'], 'fire')


class VolatilityAlertIntegrationTests(TestCase):
    """Integration tests for volatility alerts and circuit breakers."""

    def setUp(self):
        self.client = Client()
        self.user = Account.objects.create_user(
            username='vol_user', email='vol@test.com', password='testpass123'
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

        self.ability = Ability.objects.create(
            name='Tackle', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.creature = Creature.objects.create(
            name='VolatileMon', type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50, attack=50, defense=50,
            battle_cooldown=timedelta(hours=1),
            description='A very volatile creature.',
        )
        self.creature.abilities.add(self.ability)

        from trading.models import Portfolio
        self.portfolio = Portfolio.objects.create(
            owner=self.user,
            creature=self.creature,
            quantity=Decimal('10'),
            average_cost=Decimal('100.00'),
        )

    def _login(self):
        self.client.login(username='vol_user', password='testpass123')

    def test_no_alert_for_small_change(self):
        self.creature.current_price = Decimal('105.00')
        self.creature.previous_close = Decimal('100.00')
        self.creature.save()

        VolatilityService.check_volatility(self.creature)

        self.assertFalse(self.creature.is_trading_halted)
        self.assertEqual(
            Notification.objects.filter(user=self.user).count(), 0
        )

    def test_alert_for_large_change(self):
        self.creature.current_price = Decimal('120.00')
        self.creature.previous_close = Decimal('100.00')
        self.creature.save()

        VolatilityService.check_volatility(self.creature)

        self.creature.refresh_from_db()
        self.assertTrue(self.creature.is_trading_halted)
        self.assertIsNotNone(self.creature.circuit_breaker_expires_at)

        notif = Notification.objects.get(user=self.user)
        self.assertEqual(
            notif.notification_type, Notification.Type.VOLATILITY_ALERT
        )
        self.assertIn('surged', notif.title)

    def test_alert_for_sharp_drop(self):
        self.creature.current_price = Decimal('80.00')
        self.creature.previous_close = Decimal('100.00')
        self.creature.save()

        VolatilityService.check_volatility(self.creature)

        notif = Notification.objects.get(user=self.user)
        self.assertIn('plummeted', notif.title)

    def test_circuit_breaker_blocks_trading(self):
        self._login()
        self.creature.current_price = Decimal('120.00')
        self.creature.previous_close = Decimal('100.00')
        self.creature.save()

        VolatilityService.check_volatility(self.creature)

        with self.assertRaises(ValidationError) as ctx:
            TradingEngine.execute_market_buy(
                self.user, self.creature, Decimal('1')
            )
        self.assertIn('temporarily halted', str(ctx.exception))

    def test_notification_page_shows_alerts(self):
        self._login()

        Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.VOLATILITY_ALERT,
            title='Test Alert',
            message='Test message',
        )

        response = self.client.get(reverse('trading:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Alert')
        self.assertContains(response, 'Test message')

    def test_mark_notification_as_read(self):
        self._login()

        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.VOLATILITY_ALERT,
            title='Read Test',
            message='Mark me as read',
        )

        self.client.post(reverse('trading:notifications'), {
            'notification_id': notif.pk,
        })

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_notifications_api(self):
        self._login()

        Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.VOLATILITY_ALERT,
            title='API Test',
            message='Test',
        )

        response = self.client.get(reverse('trading:notifications_api'))
        data = response.json()
        self.assertEqual(data['unread_count'], 1)


class SeedAllIntegrationTests(TestCase):
    """Integration tests for the seed_all management command."""

    def test_seed_all_creates_data(self):
        from django.core.management import call_command

        call_command('seed_all', '--clear')

        self.assertGreater(Ability.objects.count(), 0)
        self.assertGreater(Creature.objects.count(), 0)
        self.assertGreater(EggTemplate.objects.count(), 0)

    def test_seed_all_with_demo(self):
        from django.core.management import call_command

        call_command('seed_all', '--clear', '--demo')

        self.assertGreater(Account.objects.exclude(
            username='market_maker'
        ).count(), 0)
        self.assertGreater(Order.objects.count(), 0)
        self.assertGreater(Battle.objects.count(), 0)
        self.assertGreater(Incubation.objects.count(), 0)

    def test_seed_all_idempotent(self):
        from django.core.management import call_command

        call_command('seed_all')
        ability_count = Ability.objects.count()
        creature_count = Creature.objects.count()

        call_command('seed_all')

        self.assertEqual(Ability.objects.count(), ability_count)
        self.assertEqual(Creature.objects.count(), creature_count)

    def test_seed_all_clear_then_reseed(self):
        from django.core.management import call_command

        call_command('seed_all', '--clear')
        count_after_first = Creature.objects.count()

        call_command('seed_all', '--clear')
        count_after_second = Creature.objects.count()

        self.assertEqual(count_after_first, count_after_second)
