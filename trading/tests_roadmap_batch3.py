from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from account.models import Account
from creature.models import Creature

from .accounting import compute_income_expenses
from .models import MarketIndex, Notification, Order, Portfolio, Trade
from .services import (
    MarketIndicesService,
    PricingEngine,
    TradingEngine,
    VolatilityService,
)


class BatchThreeTestCase(TestCase):
    def create_account(self, username):
        account = Account.objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='testpass123',
        )
        account.balance_cents = 100_000_00
        account.save(update_fields=['balance_cents'])
        return account

    def create_creature(self, name, price='100.00', **kwargs):
        return Creature.objects.create(
            name=name,
            description=f'{name} test creature.',
            type=kwargs.pop('type', 'fire'),
            current_price=Decimal(price),
            previous_close=Decimal(price),
            battle_cooldown=timedelta(hours=1),
            **kwargs,
        )


class StablePricingTests(BatchThreeTestCase):
    def test_new_creature_initializes_pricing_reference(self):
        creature = self.create_creature('ReferenceMon', '123.45')

        self.assertEqual(
            creature.pricing_reference_price,
            Decimal('123.45'),
        )

    def test_legendary_price_does_not_compound_across_repeated_ticks(self):
        creature = self.create_creature(
            'LegendaryMon',
            '100.00',
            is_legendary=True,
        )

        prices = [
            PricingEngine.update_creature_price(creature)
            for _ in range(3)
        ]

        creature.refresh_from_db()
        self.assertEqual(
            prices,
            [Decimal('150.00'), Decimal('150.00'), Decimal('150.00')],
        )
        self.assertEqual(creature.current_price, Decimal('150.00'))

    def test_dynamic_price_rounds_half_up_at_money_boundary(self):
        creature = self.create_creature(
            'RoundingMon',
            '1.03',
            is_legendary=True,
        )

        self.assertEqual(
            PricingEngine.calculate_price(creature),
            Decimal('1.55'),
        )

    def test_equal_buy_and_sell_volume_has_no_market_delta(self):
        buyer = self.create_account('market_buyer')
        seller = self.create_account('market_seller')
        creature = self.create_creature('BalancedMon')
        Trade.objects.create(
            buyer=buyer,
            seller=None,
            creature=creature,
            quantity=Decimal('10'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('15.00'),
        )
        Trade.objects.create(
            buyer=None,
            seller=seller,
            creature=creature,
            quantity=Decimal('10'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('15.00'),
        )

        self.assertEqual(
            PricingEngine.calculate_market_delta(creature),
            Decimal('0'),
        )

    def test_same_circuit_breaker_notifies_holder_only_once(self):
        holder = self.create_account('volatility_holder')
        creature = self.create_creature('AlertMon')
        Portfolio.objects.create(
            owner=holder,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=Decimal('100.00'),
        )
        creature.previous_close = Decimal('100.00')
        creature.current_price = Decimal('120.00')
        creature.save(update_fields=['previous_close', 'current_price'])

        VolatilityService.check_volatility(creature)
        VolatilityService.check_volatility(creature)

        self.assertEqual(
            Notification.objects.filter(
                user=holder,
                related_creature=creature,
                notification_type=Notification.Type.VOLATILITY_ALERT,
            ).count(),
            1,
        )


class AccountingTests(BatchThreeTestCase):
    def setUp(self):
        self.user = self.create_account('accounting_user')
        self.creature = self.create_creature('AccountingMon')

    def test_commissions_are_counted_once_and_domain_values_are_decimal(self):
        Trade.objects.create(
            buyer=self.user,
            seller=None,
            creature=self.creature,
            quantity=Decimal('1'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('1.50'),
        )
        Trade.objects.create(
            buyer=None,
            seller=self.user,
            creature=self.creature,
            quantity=Decimal('1'),
            price_per_unit=Decimal('120.00'),
            commission=Decimal('1.80'),
        )

        result = compute_income_expenses(self.user)
        income = result['income_data'][0]
        expenses = result['expenses_data'][0]

        self.assertEqual(income['sales'], Decimal('120.00'))
        self.assertEqual(expenses['purchases'], Decimal('100.00'))
        self.assertEqual(expenses['commissions'], Decimal('3.30'))
        self.assertEqual(result['total_income'], Decimal('120.00'))
        self.assertEqual(result['total_expenses'], Decimal('103.30'))
        self.assertEqual(result['net_flow'], Decimal('16.70'))
        self.assertIsInstance(result['net_flow'], Decimal)

    def test_summary_json_and_dashboard_share_exact_accounting(self):
        Trade.objects.create(
            buyer=self.user,
            seller=None,
            creature=self.creature,
            quantity=Decimal('1'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('1.50'),
        )
        self.client.force_login(self.user)

        summary = self.client.get(
            reverse('trading:portfolio_summary_api')
        )
        dashboard = self.client.get(reverse('dashboard:dashboard'))

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(
            set(summary.json()),
            {
                'balance',
                'total_value',
                'total_cost',
                'total_pnl',
                'total_pnl_pct',
                'realized_pnl',
                'realized_pnl_pct',
                'total_return',
                'total_return_pct',
                'income_data',
                'expenses_data',
                'net_data',
                'total_income',
                'total_expenses',
                'net_flow',
                'period',
            },
        )
        self.assertEqual(summary.json()['total_expenses'], 101.5)
        self.assertEqual(
            summary.json()['expenses_data'][0]['commissions'],
            1.5,
        )
        self.assertEqual(
            dashboard.context['total_expenses'],
            Decimal('101.50'),
        )
        self.assertEqual(
            dashboard.context['expenses_data'][0]['commissions'],
            Decimal('1.50'),
        )


class RealizedProfitTests(BatchThreeTestCase):
    def setUp(self):
        self.user = self.create_account('profit_user')
        self.creature = self.create_creature('ProfitMon')
        self.client.force_login(self.user)

    def test_closed_position_uses_cost_snapshot_for_realized_pnl(self):
        TradingEngine.execute_market_buy(
            self.user,
            self.creature,
            Decimal('2'),
        )
        self.creature.current_price = Decimal('150.00')
        self.creature.save(update_fields=['current_price'])

        sell_trade = TradingEngine.execute_market_sell(
            self.user,
            self.creature,
            Decimal('2'),
        )
        response = self.client.get(reverse('trading:portfolio'))

        self.assertEqual(
            sell_trade.realized_cost_basis,
            Decimal('200.00'),
        )
        self.assertFalse(
            Portfolio.objects.filter(
                owner=self.user,
                creature=self.creature,
            ).exists()
        )
        self.assertEqual(
            response.context['realized_pnl'],
            Decimal('95.50'),
        )

    def test_limit_sell_persists_cost_snapshot_before_reducing_portfolio(self):
        Portfolio.objects.create(
            owner=self.user,
            creature=self.creature,
            quantity=Decimal('3'),
            average_cost=Decimal('80.00'),
        )
        order = TradingEngine.place_limit_order(
            self.user,
            self.creature,
            Order.OrderType.SELL,
            Decimal('2'),
            Decimal('120.00'),
        )
        self.creature.current_price = Decimal('120.00')
        self.creature.save(update_fields=['current_price'])

        TradingEngine.check_limit_orders(self.creature)

        trade = Trade.objects.get(sell_order=order)
        self.assertEqual(
            trade.realized_cost_basis,
            Decimal('160.00'),
        )

    def test_historical_sell_without_snapshot_is_not_recalculated(self):
        Trade.objects.create(
            buyer=None,
            seller=self.user,
            creature=self.creature,
            quantity=Decimal('1'),
            price_per_unit=Decimal('500.00'),
            commission=Decimal('7.50'),
            realized_cost_basis=None,
        )

        response = self.client.get(reverse('trading:portfolio'))

        self.assertEqual(response.context['realized_pnl'], Decimal('0'))


class VolumeWeightedIndexTests(BatchThreeTestCase):
    def test_index_weights_each_creature_price_by_its_trade_volume(self):
        trader = self.create_account('index_trader')
        low_volume = self.create_creature('LowVolumeMon', '100.00')
        high_volume = self.create_creature('HighVolumeMon', '200.00')
        Trade.objects.create(
            buyer=trader,
            seller=None,
            creature=low_volume,
            quantity=Decimal('1'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('1.50'),
        )
        Trade.objects.create(
            buyer=trader,
            seller=None,
            creature=high_volume,
            quantity=Decimal('3'),
            price_per_unit=Decimal('200.00'),
            commission=Decimal('9.00'),
        )

        index = MarketIndicesService.calculate_type_index('fire')

        self.assertIsInstance(index, MarketIndex)
        self.assertEqual(index.value, Decimal('175.00'))
        self.assertEqual(index.total_volume, Decimal('4'))

    def test_index_falls_back_to_simple_average_without_volume(self):
        self.create_creature('NoVolumeOne', '100.00', type='water')
        self.create_creature('NoVolumeTwo', '200.00', type='water')

        index = MarketIndicesService.calculate_type_index('water')

        self.assertEqual(index.value, Decimal('150.00'))
        self.assertEqual(index.total_volume, Decimal('0'))
