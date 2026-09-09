import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from account.models import Account
from banking.models import Transfer
from creature.models import Creature
from creature.services import BattleService
from trading.models import Order, Portfolio, Trade


WEBHOOK_SECRET = "p40-deposit-webhook-secret"


class P40AccountJourneyTests(TestCase):
    def test_register_creates_identifiers_and_email_login_works(self):
        password = "P40StrongPass123!"
        response = self.client.post(
            reverse("account:register"),
            {
                "username": "p40_register",
                "email": "p40-register@example.com",
                "password1": password,
                "password2": password,
            },
        )

        account = Account.objects.get(username="p40_register")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("index"))
        self.assertEqual(len(account.cvu), 22)
        self.assertTrue(account.cvu.isdigit())
        self.assertTrue(account.alias)

        self.client.logout()
        login_response = self.client.post(
            reverse("account:login"),
            {
                "username": account.email.upper(),
                "password": password,
            },
        )

        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.url, reverse("index"))
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            account.pk,
        )


@override_settings(DEPOSIT_WEBHOOK_SECRET=WEBHOOK_SECRET)
class P40FinancialJourneyTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create_user(
            username="p40_trader",
            email="p40-trader@example.com",
            password="P40TradePass123!",
        )
        self.creature = Creature.objects.create(
            name="P40 Market Creature",
            description="Cross-app integration fixture.",
            type="water",
            current_price=Decimal("25.00"),
            previous_close=Decimal("25.00"),
            battle_cooldown=timedelta(hours=1),
        )

    def _post_deposit(self, amount_cents=10_000):
        payload = {
            "status": "COMPLETED",
            "provider_reference": "p40-provider-deposit",
            "target_identifier": self.account.cvu,
            "identifier_type": "CVU",
            "amount_cents": amount_cents,
        }
        body = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(
            WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            reverse("banking:deposit_webhook"),
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": signature},
        )

    def _buy_two_units(self):
        self.client.force_login(self.account)
        return self.client.post(
            reverse("trading:place_order"),
            {
                "creature": self.creature.pk,
                "order_type": Order.OrderType.BUY,
                "execution_type": Order.ExecutionType.MARKET,
                "quantity": "2.00000000",
            },
        )

    def _fund_and_buy(self):
        deposit_response = self._post_deposit()
        buy_response = self._buy_two_units()
        self.assertEqual(deposit_response.status_code, 200)
        self.assertEqual(buy_response.status_code, 302)

    def test_signed_deposit_webhook_is_idempotent_and_credits_cents(self):
        first = self._post_deposit(amount_cents=12_345)
        second = self._post_deposit(amount_cents=12_345)

        self.assertEqual(first.json(), {"status": "ok"})
        self.assertEqual(second.json(), {"status": "ok"})
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 12_345)
        transfer = Transfer.objects.get(
            provider_reference="p40-provider-deposit",
        )
        self.assertEqual(transfer.amount, 12_345)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.COMPLETED,
        )
        self.assertEqual(
            Transfer.objects.filter(
                provider_reference="p40-provider-deposit",
            ).count(),
            1,
        )

    def test_market_buy_creates_decimal_portfolio_and_debits_cents(self):
        self._fund_and_buy()

        portfolio = Portfolio.objects.get(
            owner=self.account,
            creature=self.creature,
        )
        self.account.refresh_from_db()
        self.assertEqual(portfolio.quantity, Decimal("2.00000000"))
        self.assertEqual(portfolio.average_cost, Decimal("25.00"))
        self.assertEqual(self.account.balance_cents, 4_925)
        trade = Trade.objects.get(
            buyer=self.account,
            creature=self.creature,
        )
        self.assertIsNone(trade.seller)
        self.assertEqual(trade.commission, Decimal("0.75"))

    def test_dashboard_ledger_and_summary_follow_persisted_cash_flows(self):
        self._fund_and_buy()

        response = self.client.get(reverse("dashboard:dashboard"))

        self.assertEqual(response.status_code, 200)
        ledger = {
            entry.label: entry.amount
            for entry in response.context["ledger"]
        }
        self.assertEqual(
            ledger,
            {
                "Deposit": Decimal("100.00"),
                "Market buy: P40 Market Creature": Decimal("-50.75"),
            },
        )
        self.assertEqual(response.context["total_value"], Decimal("50.00"))
        self.assertEqual(response.context["total_cost"], Decimal("50.00"))
        self.assertEqual(response.context["total_pnl"], Decimal("0.00"))
        self.assertEqual(response.context["total_income"], Decimal("100.00"))
        self.assertEqual(
            response.context["total_expenses"],
            Decimal("50.75"),
        )
        self.assertEqual(response.context["net_flow"], Decimal("49.25"))

    def test_dashboard_and_portfolio_summary_api_have_financial_parity(self):
        self._fund_and_buy()
        self.creature.current_price = Decimal("30.00")
        self.creature.save(update_fields=["current_price"])

        dashboard = self.client.get(
            reverse("dashboard:dashboard"),
            {"period": "yearly"},
        )
        summary = self.client.get(
            reverse("trading:portfolio_summary_api"),
            {"period": "yearly"},
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(summary.status_code, 200)
        payload = summary.json()
        for key in (
            "total_value",
            "total_cost",
            "total_pnl",
            "total_pnl_pct",
            "income_data",
            "expenses_data",
            "net_data",
            "total_income",
            "total_expenses",
            "net_flow",
            "period",
        ):
            with self.subTest(key=key):
                expected = json.loads(json.dumps(
                    dashboard.context[key],
                    default=float,
                ))
                self.assertEqual(payload[key], expected)
        self.account.refresh_from_db()
        self.assertEqual(
            Decimal(str(payload["balance"])),
            Decimal(self.account.balance_cents) / Decimal("100"),
        )


class P40RoutingAndBeatContractTests(SimpleTestCase):
    BANKING_ROUTES = {
        "transfer": "/transfer/",
        "withdraw": "/withdraw/",
        "deposit": "/deposit/",
        "invest": "/invest/",
    }

    def test_banking_routes_are_root_canonical_paths(self):
        for route_name, path in self.BANKING_ROUTES.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(reverse(f"banking:{route_name}"), path)

    def test_legacy_banking_paths_redirect_permanently(self):
        for route_name, path in self.BANKING_ROUTES.items():
            with self.subTest(route_name=route_name):
                response = self.client.get(
                    f"/banking/{route_name}/?source=p40",
                )
                self.assertIn(response.status_code, (301, 308))
                self.assertEqual(
                    response.headers["Location"],
                    f"{path}?source=p40",
                )

    def test_beat_registers_weekly_snapshot_and_recovery_tasks(self):
        schedule = settings.CELERY_BEAT_SCHEDULE
        weekly = schedule["snapshot-precios-semanal"]
        self.assertEqual(
            weekly["task"],
            "trading.tasks.record_weekly_price_snapshots",
        )
        self.assertEqual(weekly["schedule"]._orig_minute, 0)
        self.assertEqual(weekly["schedule"]._orig_hour, 0)
        self.assertEqual(weekly["schedule"]._orig_day_of_week, 1)

        self.assertEqual(
            schedule[
                "recuperar-operaciones-creature-cada-minuto"
            ]["task"],
            "creature.tasks.recover_creature_operations",
        )
        self.assertEqual(
            schedule[
                "recuperar-retiros-pendientes-cada-minuto"
            ]["task"],
            "banking.tasks.recover_pending_withdrawals",
        )


class P40BattlePublicContractTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create_user(
            username="p40_battle_viewer",
            email="p40-battle@example.com",
            password="P40BattlePass123!",
        )
        self.client.force_login(self.account)
        first = self._create_creature(
            "P40 Underdog",
            829,
            creature_id=uuid.UUID("11111111-1111-4111-a111-111111111111"),
        )
        second = self._create_creature(
            "P40 Favorite",
            1731,
            creature_id=uuid.UUID("22222222-2222-4222-a222-222222222222"),
        )
        self.battle = BattleService.start_battle(first, second)

    def _create_creature(self, name, elo_rating, creature_id=None):
        params = {
            "name": name,
            "description": "P40 battle public contract fixture.",
            "type": "normal",
            "current_price": Decimal("40.00"),
            "previous_close": Decimal("40.00"),
            "battle_cooldown": timedelta(hours=1),
            "cooldown_expires_at": timezone.now() - timedelta(minutes=1),
            "elo_rating": elo_rating,
        }
        if creature_id:
            params["id"] = creature_id
        return Creature.objects.create(**params)


    def test_battle_html_and_json_contracts_do_not_expose_elo(self):
        url = reverse("creature:battle_detail", args=[self.battle.pk])

        html_response = self.client.get(url)
        api_response = self.client.get(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(html_response.status_code, 200)
        self.assertNotContains(html_response, "829")
        self.assertNotContains(html_response, "1731")
        self.assertEqual(api_response.status_code, 200)
        self.assertNotIn(
            "elo",
            json.dumps(api_response.json()).lower(),
        )
