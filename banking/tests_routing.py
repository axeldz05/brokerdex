from django.conf import settings
from django.test import SimpleTestCase
from django.urls import get_resolver, resolve, reverse

from . import views


BANKING_ROUTES = {
    "transfer": ("/transfer/", views.transfer),
    "withdraw": ("/withdraw/", views.withdraw),
    "deposit": ("/deposit/", views.deposit),
    "invest": ("/invest/", views.invest),
}


class BankingRoutingTests(SimpleTestCase):
    def test_named_routes_reverse_to_canonical_root_paths(self):
        for route_name, (canonical_path, _) in BANKING_ROUTES.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(
                    reverse(f"banking:{route_name}"),
                    canonical_path,
                )

    def test_canonical_paths_resolve_to_banking_views(self):
        for route_name, (canonical_path, view) in BANKING_ROUTES.items():
            with self.subTest(route_name=route_name):
                match = resolve(canonical_path)

                self.assertEqual(match.view_name, f"banking:{route_name}")
                self.assertIs(match.func, view)

    def test_banking_namespace_is_registered_once_at_the_root(self):
        def nested_patterns(patterns):
            for pattern in patterns:
                yield pattern
                if hasattr(pattern, "url_patterns"):
                    yield from nested_patterns(pattern.url_patterns)

        banking_resolvers = [
            pattern
            for pattern in nested_patterns(get_resolver().url_patterns)
            if getattr(pattern, "namespace", None) == "banking"
        ]

        self.assertEqual(len(banking_resolvers), 1)
        self.assertEqual(str(banking_resolvers[0].pattern), "")

    def test_legacy_banking_paths_redirect_permanently(self):
        for route_name, (canonical_path, _) in BANKING_ROUTES.items():
            with self.subTest(route_name=route_name):
                response = self.client.get(
                    f"/banking/{route_name}/",
                )

                self.assertIn(response.status_code, (301, 308))
                self.assertEqual(response.headers["Location"], canonical_path)

    def test_canonical_routes_keep_authentication_required(self):
        for canonical_path, _ in BANKING_ROUTES.values():
            with self.subTest(canonical_path=canonical_path):
                response = self.client.get(canonical_path)

                self.assertRedirects(
                    response,
                    f"{settings.LOGIN_URL}?next={canonical_path}",
                    fetch_redirect_response=False,
                )
