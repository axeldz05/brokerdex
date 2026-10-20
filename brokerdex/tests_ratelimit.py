from decimal import Decimal
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, RequestFactory
from django.urls import reverse

from account.models import Account
from brokerdex.views import ratelimited_view


class RateLimitingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.time_patcher = patch("time.time", return_value=1700000000.0)
        self.time_patcher.start()
        self.factory = RequestFactory()
        self.user = Account.objects.create_user(
            username="ratelimit_user",
            email="ratelimit@example.com",
            password="StrongPassword123!",
            balance_cents=100000,
        )
        self.client.force_login(self.user)

    def tearDown(self):
        self.time_patcher.stop()
        cache.clear()

    def test_ratelimited_view_returns_json_for_webhook_and_api(self):
        request = self.factory.post("/webhooks/deposits/")
        response = ratelimited_view(request)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response["Retry-After"], "60")
        self.assertJSONEqual(
            response.content.decode("utf-8"),
            {
                "status": "error",
                "code": "rate_limited",
                "message": "Too many requests. Please try again later.",
            },
        )

    def test_ratelimited_view_returns_html_for_browser_requests(self):
        request = self.factory.get("/account/login/")
        response = ratelimited_view(request)
        self.assertEqual(response.status_code, 429)
        self.assertIn("text/html", response["Content-Type"])
        self.assertEqual(response["Retry-After"], "60")
        self.assertContains(
            response,
            "429 - Too Many Requests",
            status_code=429,
        )

    def test_login_rate_limiting_blocks_after_five_attempts(self):
        self.client.logout()
        url = reverse("account:login")

        # First 5 POST attempts should be processed (returning 200 invalid form)
        for _ in range(5):
            response = self.client.post(
                url,
                {"username": "wrong_user", "password": "wrong_password"},
                REMOTE_ADDR="192.0.2.1",
            )
            self.assertEqual(response.status_code, 200)

        # 6th attempt from the same IP must be blocked by rate limiting
        response = self.client.post(
            url,
            {"username": "wrong_user", "password": "wrong_password"},
            REMOTE_ADDR="192.0.2.1",
        )
        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "429 - Too Many Requests",
            status_code=429,
        )

    def test_register_rate_limiting_blocks_after_three_attempts(self):
        self.client.logout()
        url = reverse("account:register")

        # 3 initial attempts
        for i in range(3):
            response = self.client.post(
                url,
                {
                    "username": f"bot_user_{i}",
                    "email": f"bot_{i}@example.com",
                    "password": "Password123!",
                    "password_confirm": "MismatchPassword!",
                },
                REMOTE_ADDR="192.0.2.2",
            )
            self.assertEqual(response.status_code, 200)

        # 4th attempt from same IP must be blocked
        response = self.client.post(
            url,
            {
                "username": "bot_user_4",
                "email": "bot_4@example.com",
                "password": "Password123!",
                "password_confirm": "Password123!",
            },
            REMOTE_ADDR="192.0.2.2",
        )
        self.assertEqual(response.status_code, 429)

    def test_deposit_webhook_rate_limiting_blocks_after_sixty_requests(self):
        url = reverse("banking:deposit_webhook")

        for _ in range(60):
            response = self.client.post(
                url,
                data="{}",
                content_type="application/json",
                REMOTE_ADDR="192.0.2.3",
            )
            # Will be 401 (invalid signature) but not rate limited yet
            self.assertEqual(response.status_code, 401)

        # 61st request must be rate limited with JSON 429
        response = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            REMOTE_ADDR="192.0.2.3",
        )
        self.assertEqual(response.status_code, 429)
        self.assertJSONEqual(
            response.content.decode("utf-8"),
            {
                "status": "error",
                "code": "rate_limited",
                "message": "Too many requests. Please try again later.",
            },
        )

    def test_transfer_rate_limiting_blocks_after_ten_requests(self):
        url = reverse("banking:transfer")

        receiver = Account.objects.create_user(
            username="receiver_acc",
            email="receiver@example.com",
            password="StrongPassword123!",
        )

        for _ in range(10):
            response = self.client.post(
                url,
                {
                    "receiver_username": receiver.username,
                    "amount": Decimal("1.00"),
                },
            )
            self.assertIn(response.status_code, [200, 302])

        # 11th request by the same user must be rate limited
        response = self.client.post(
            url,
            {
                "receiver_username": receiver.username,
                "amount": Decimal("1.00"),
            },
        )
        self.assertEqual(response.status_code, 429)

    def test_withdraw_rate_limiting_blocks_after_ten_requests(self):
        url = reverse("banking:withdraw")

        for _ in range(10):
            response = self.client.post(
                url,
                {
                    "amount": Decimal("1.00"),
                    "target_identifier": "0000000000000000000001",
                },
            )
            self.assertIn(response.status_code, [200, 302])

        # 11th request by the same user must be rate limited
        response = self.client.post(
            url,
            {
                "amount": Decimal("1.00"),
                "target_identifier": "0000000000000000000001",
            },
        )
        self.assertEqual(response.status_code, 429)


    def test_place_order_rate_limiting_blocks_after_twenty_requests(self):
        url = reverse("trading:place_order")
        from creature.models import Creature
        creature = Creature.objects.create(
            name="Ratelimit Beast",
            current_price=Decimal("10.00"),
            pricing_reference_price=Decimal("10.00"),
        )

        for _ in range(20):
            response = self.client.post(
                url,
                {
                    "execution_type": "MARKET",
                    "order_type": "BUY",
                    "quantity": "1",
                    "creature": str(creature.id),
                },
            )
            # Will redirect to creature detail
            self.assertEqual(response.status_code, 302)

        # 21st request by the same user must be rate limited
        response = self.client.post(
            url,
            {
                "execution_type": "MARKET",
                "order_type": "BUY",
                "quantity": "1",
                "creature": str(creature.id),
            },
        )
        self.assertEqual(response.status_code, 429)

