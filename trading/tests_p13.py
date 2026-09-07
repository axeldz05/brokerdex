from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account
from creature.models import Creature

from .models import MarketIndex, Portfolio
from .services import TradingEngine


class P13PortfolioAndIndicesTests(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username='p13_user',
            email='p13@example.com',
            password='testpass123',
        )
        self.user.balance_cents = 100_000_00
        self.user.save(update_fields=['balance_cents'])
        self.client.force_login(self.user)

    def create_index(self, creature_type, value, timestamp):
        index = MarketIndex.objects.create(
            creature_type=creature_type,
            value=Decimal(value),
            previous_value=Decimal(value),
            creature_count=1,
            total_volume=Decimal('1'),
        )
        MarketIndex.objects.filter(pk=index.pk).update(timestamp=timestamp)
        return index

    def test_market_indices_defaults_to_latest_per_type_and_keeps_filter_shape(
        self,
    ):
        now = timezone.now()
        self.create_index('fire', '100.00', now - timedelta(hours=2))
        self.create_index('fire', '125.00', now)
        self.create_index('water', '80.00', now - timedelta(hours=1))

        response = self.client.get(reverse('trading:market_indices_api'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['indices']), 2)
        indices_by_type = {
            index['type']: index
            for index in response.json()['indices']
        }
        self.assertEqual(set(indices_by_type), {'fire', 'water'})
        self.assertEqual(indices_by_type['fire']['value'], 125.0)

        filtered_response = self.client.get(
            reverse('trading:market_indices_api'),
            {'type': 'fire'},
        )
        filtered_indices = filtered_response.json()['indices']
        self.assertEqual(
            [index['value'] for index in filtered_indices],
            [125.0, 100.0],
        )
        self.assertEqual(
            set(filtered_indices[0]),
            {
                'type',
                'value',
                'previous_value',
                'change_pct',
                'creature_count',
                'total_volume',
                'timestamp',
            },
        )

    def test_portfolio_summary_json_adds_realized_return_fields(self):
        response = self.client.get(reverse('trading:portfolio_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {
                key: payload[key]
                for key in (
                    'realized_pnl',
                    'realized_pnl_pct',
                    'total_return',
                    'total_return_pct',
                )
            },
            {
                'realized_pnl': 0.0,
                'realized_pnl_pct': 0.0,
                'total_return': 0.0,
                'total_return_pct': 0.0,
            },
        )

    def test_closed_sale_is_shared_by_html_and_json_realized_summary(self):
        creature = Creature.objects.create(
            name='P13ProfitMon',
            description='P13 closed position creature.',
            type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            battle_cooldown=timedelta(hours=1),
        )
        TradingEngine.execute_market_buy(
            self.user,
            creature,
            Decimal('2'),
        )
        creature.current_price = Decimal('150.00')
        creature.save(update_fields=['current_price'])
        TradingEngine.execute_market_sell(
            self.user,
            creature,
            Decimal('2'),
        )

        html_response = self.client.get(reverse('trading:portfolio'))
        json_response = self.client.get(
            reverse('trading:portfolio_summary_api')
        )

        self.assertFalse(
            Portfolio.objects.filter(
                owner=self.user,
                creature=creature,
            ).exists()
        )
        expected = {
            'realized_pnl': Decimal('95.50'),
            'realized_pnl_pct': Decimal('47.75'),
            'total_return': Decimal('95.50'),
            'total_return_pct': Decimal('47.75'),
        }
        for key, value in expected.items():
            self.assertEqual(html_response.context[key], value)
            self.assertEqual(json_response.json()[key], float(value))

    def test_portfolio_polling_targets_all_summary_cards_every_15_seconds(self):
        creature = Creature.objects.create(
            name='P13HoldingMon',
            description='P13 open position creature.',
            type='water',
            current_price=Decimal('80.00'),
            previous_close=Decimal('80.00'),
            battle_cooldown=timedelta(hours=1),
        )
        Portfolio.objects.create(
            owner=self.user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=Decimal('80.00'),
        )

        response = self.client.get(reverse('trading:portfolio'))

        self.assertContains(response, 'id="portfolioTotalValue"')
        self.assertContains(response, 'id="portfolioTotalCost"')
        self.assertContains(response, 'id="portfolioUnrealizedPnl"')
        self.assertContains(response, 'id="portfolioRealizedPnl"')
        self.assertContains(response, 'id="portfolioTotalReturn"')
        self.assertContains(
            response,
            "document.getElementById('portfolioRealizedPnl')",
        )
        self.assertContains(
            response,
            "document.getElementById('portfolioTotalReturn')",
        )
        self.assertContains(
            response,
            'setInterval(refreshSummary, 15000);',
        )
        self.assertContains(response, 'Price History')
        self.assertNotContains(response, 'Recent Trades')
