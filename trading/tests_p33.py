from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from account.models import Account
from creature.models import Creature

from .models import MarketIndex, Notification, Order, Portfolio


class TradingAccessibilityMarkupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Account.objects.create_user(
            username='p33_trader',
            email='p33-trader@example.com',
            password='testpass123',
        )
        cls.creature = Creature.objects.create(
            name='AccessMon',
            description='A creature used for accessibility smoke tests.',
            type='electric',
            current_price=Decimal('100.00'),
            previous_close=Decimal('95.00'),
            battle_cooldown=timedelta(hours=1),
        )
        cls.portfolio = Portfolio.objects.create(
            owner=cls.user,
            creature=cls.creature,
            quantity=Decimal('2'),
            average_cost=Decimal('90.00'),
        )
        cls.order = Order.objects.create(
            account=cls.user,
            creature=cls.creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=Decimal('80.00'),
        )
        cls.notification = Notification.objects.create(
            user=cls.user,
            notification_type=Notification.Type.SYSTEM,
            title='Accessible notice',
            message='Review this notification.',
        )
        MarketIndex.objects.create(
            creature_type='electric',
            value=Decimal('100.00'),
            previous_value=Decimal('95.00'),
            total_volume=Decimal('5'),
            creature_count=1,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_market_exposes_search_table_and_keyboard_row_markup(self):
        response = self.client.get(reverse('trading:market'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<label for="searchInput"')
        self.assertContains(response, 'aria-describedby="marketSearchHelp"')
        self.assertContains(response, 'Available creatures and current prices')
        self.assertContains(response, '<th scope="col"')
        self.assertContains(response, 'class="market-row-link"')
        self.assertContains(
            response,
            f'aria-label="View {self.creature.name} trading details"',
        )

    def test_detail_exposes_labeled_forms_and_busy_chart_fallback(self):
        response = self.client.get(
            reverse('trading:creature_detail', args=[self.creature.pk])
        )

        self.assertEqual(response.status_code, 200)
        for field_id in (
            'buyQty',
            'sellQty',
            'limitOrderType',
            'limitQty',
            'limitPrice',
        ):
            self.assertContains(response, f'for="{field_id}"')
        self.assertContains(response, 'aria-describedby="buyQtyHelp')
        self.assertContains(response, 'aria-describedby="sellQtyHelp')
        self.assertContains(response, 'id="priceChartRegion"')
        self.assertContains(response, 'aria-busy="true"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'id="priceHistoryTableBody"')
        self.assertContains(
            response,
            f'aria-label="Cancel Buy order for {self.creature.name}"',
        )

    def test_portfolio_exposes_polling_charts_fallbacks_and_keyboard_rows(self):
        response = self.client.get(reverse('trading:portfolio'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="portfolioSummary"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-busy="false"')
        self.assertContains(response, 'class="portfolio-row-trigger"')
        self.assertContains(response, 'aria-controls="detailPanel"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'id="distributionChart"')
        self.assertContains(response, 'id="distributionFallbackTable"')
        self.assertContains(response, 'id="incomeExpensesChartRegion"')
        self.assertContains(response, 'id="incomeExpensesFallback"')

    def test_secondary_pages_expose_named_actions_and_semantic_groups(self):
        orders = self.client.get(reverse('trading:orders'))
        notifications = self.client.get(reverse('trading:notifications'))
        indices = self.client.get(reverse('trading:market_indices'))

        self.assertContains(orders, 'Order history and current status')
        self.assertContains(orders, '<th scope="col"')
        self.assertContains(
            orders,
            f'aria-label="Cancel Buy order for {self.creature.name}"',
        )
        self.assertContains(
            notifications,
            'aria-label="Mark Accessible notice as read"',
        )
        self.assertContains(indices, 'role="list"')
        self.assertContains(indices, 'role="listitem"')
