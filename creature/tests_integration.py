from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from account.models import Account
from .models import Creature, Ability, EggTemplate, Incubation
from .services import IncubationService


class IncubationIntegrationTests(TestCase):
    """Integration tests for the incubation system."""

    def setUp(self):
        self.celery_patcher = patch('creature.tasks.hatch_egg.apply_async')
        self.mock_async = self.celery_patcher.start()
        self.mock_async.return_value = MagicMock(id='fake-task-id')
        self.addCleanup(self.celery_patcher.stop)

        self.client = Client()
        self.user = Account.objects.create_user(
            username='incubator',
            email='inc@test.com',
            password='testpass123',
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

        self.ability = Ability.objects.create(
            name='Tackle', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.creature = Creature.objects.create(
            name='Bulbasaur', type='grass',
            current_price=Decimal('120.00'),
            previous_close=Decimal('110.00'),
            hp=45, attack=49, defense=49,
            special_attack=65, special_defense=65, speed=45,
            battle_cooldown=timedelta(hours=1),
            description='A test creature.',
        )
        self.creature.abilities.add(self.ability)

        self.egg = EggTemplate.objects.create(
            name='Starter Egg',
            description='A mysterious egg that may contain a starter.',
            price=Decimal('500.00'),
            hatch_duration=timedelta(hours=1),
        )
        self.egg.creature_pool.add(self.creature)

    def _login(self):
        self.client.login(username='incubator', password='testpass123')

    def test_incubation_shop_requires_login(self):
        response = self.client.get(reverse('creature:incubation_shop'))
        self.assertEqual(response.status_code, 302)

    def test_incubation_shop_shows_eggs(self):
        self._login()
        response = self.client.get(reverse('creature:incubation_shop'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starter Egg')
        self.assertContains(response, '$500.00')

    def test_purchase_egg_deducts_balance(self):
        self._login()
        response = self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        self.assertRedirects(response, reverse('creature:incubation_shop'))

        self.user.refresh_from_db()
        expected_balance = 100_000_00 - 500_00
        self.assertEqual(self.user.balance_cents, expected_balance)

    def test_purchase_egg_creates_incubation(self):
        self._login()
        self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        incubation = Incubation.objects.get(user=self.user)
        self.assertEqual(incubation.egg_template, self.egg)
        self.assertEqual(incubation.status, Incubation.Status.INCUBATING)

    def test_purchase_egg_insufficient_funds(self):
        self._login()
        self.user.balance_cents = 100
        self.user.save()

        response = self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk]),
            follow=True,
        )
        self.assertContains(response, 'Insufficient funds')

    def test_incubation_status_page_shows_incubations(self):
        self._login()
        self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        response = self.client.get(reverse('creature:incubation_status'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starter Egg')
        self.assertContains(response, 'Incubating')

    @patch('celery.app.task.Task.apply_async')
    def test_hatch_egg_creates_portfolio(self, mock_async):
        mock_async.return_value = MagicMock(id='fake-task-id')

        incubation = Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=timezone.now() - timedelta(minutes=1),
            status=Incubation.Status.INCUBATING,
        )

        result = IncubationService.hatch_egg(incubation)

        incubation.refresh_from_db()
        self.assertEqual(incubation.status, Incubation.Status.HATCHED)
        self.assertIsNotNone(incubation.hatched_creature)
        self.assertEqual(incubation.hatched_creature, self.creature)

        from trading.models import Portfolio
        portfolio = Portfolio.objects.get(
            owner=self.user,
            creature=self.creature,
        )
        self.assertEqual(portfolio.quantity, Decimal('1'))

    def test_incubation_status_shows_hatched(self):
        self._login()

        incubation = Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=timezone.now() - timedelta(hours=2),
            status=Incubation.Status.HATCHED,
            hatched_at=timezone.now(),
            hatched_creature=self.creature,
        )

        response = self.client.get(reverse('creature:incubation_status'))
        self.assertContains(response, 'Hatched')
        self.assertContains(response, 'Bulbasaur')
