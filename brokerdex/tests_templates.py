from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse


class CoreTemplateAccessibilityTests(SimpleTestCase):
    def _render_layout(self, view_name, messages=()):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True)
        request.resolver_match = SimpleNamespace(view_name=view_name)
        return render_to_string(
            "layout.html",
            {"messages": messages},
            request=request,
        )

    def test_layout_exposes_keyboard_navigation_and_control_names(self):
        html = self._render_layout(
            "trading:market",
            messages=[Message(constants.SUCCESS, "Order placed")],
        )

        self.assertIn(
            '<a class="skip-link" href="#main-content">'
            "Skip to main content</a>",
            html,
        )
        self.assertIn('id="main-content"', html)
        self.assertIn(":focus-visible", html)
        self.assertIn('aria-label="Switch to light theme"', html)
        self.assertIn('aria-label="Open profile menu"', html)
        self.assertIn('aria-label="Dismiss notification"', html)
        self.assertIn('aria-label="Back to top"', html)
        self.assertNotIn("btn-close-white", html)

    def test_layout_marks_the_resolved_navigation_link_as_current(self):
        html = self._render_layout("trading:market")

        self.assertIn(
            f'<a class="nav-link" href="{reverse("trading:market")}" '
            'aria-current="page">Market</a>',
            html,
        )
        self.assertNotIn(
            f'<a class="nav-link" href="{reverse("trading:portfolio")}" '
            'aria-current="page">',
            html,
        )

    def test_core_pages_override_useful_metadata(self):
        index_response = self.client.get(reverse("index"))
        request = RequestFactory().get("/home/")
        request.user = AnonymousUser()
        request.resolver_match = SimpleNamespace(view_name="home")
        home_html = render_to_string("home.html", request=request)

        self.assertContains(
            index_response,
            "<title>Invest in Creatures | Brokerdex</title>",
            html=True,
        )
        self.assertContains(
            index_response,
            (
                '<meta name="description" content="Trade fractional creature '
                "assets, follow battles, and hatch new creatures on "
                'Brokerdex.">'
            ),
            html=True,
        )
        self.assertIn(
            "<title>Explore Creature Investing | Brokerdex</title>",
            home_html,
        )

    def test_bootstrap_assets_use_one_version(self):
        html = self._render_layout("trading:market")

        self.assertEqual(html.count("bootstrap@5.3.3"), 2)
        self.assertNotIn("bootstrap@5.2.3", html)
