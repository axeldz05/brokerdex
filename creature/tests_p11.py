import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account
from account.money import quantize_money
from trading.models import Notification, Portfolio
from trading.services import PricingEngine

from .models import Ability, Creature
from .services import BattleService


class BattlePricingShockTests(TestCase):
    def setUp(self):
        self.ability = Ability.objects.create(
            name='P11 Strike',
            ability_type='normal',
            damage_class='physical',
            power=40,
        )
        self.underdog = self._create_creature(
            'P11 Underdog',
            elo_rating=1000,
        )
        self.favorite = self._create_creature(
            'P11 Favorite',
            elo_rating=1200,
        )
        self.user = Account.objects.create_user(
            username='p11-holder',
            email='p11-holder@example.com',
            password='testpass123',
        )

    def _create_creature(self, name, elo_rating):
        creature = Creature.objects.create(
            name=name,
            type='normal',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=100,
            attack=50,
            defense=50,
            special_attack=50,
            special_defense=50,
            speed=50,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
            elo_rating=elo_rating,
        )
        creature.abilities.add(self.ability)
        return creature

    def _start_battle(self):
        return BattleService.start_battle(self.underdog, self.favorite)

    def _finish_with_winner(self, battle, winner_index):
        participants = list(battle.participants.all())
        winner = participants[winner_index]
        loser = participants[1 - winner_index]
        battle.record_action(
            winner,
            loser,
            self.ability,
            loser.current_hp,
        )
        battle.refresh_from_db()
        return participants

    def _expected_reference(self, change_pct):
        return quantize_money(
            Decimal('100.00') * (
                Decimal('1') + change_pct / Decimal('100')
            )
        )

    def test_underdog_win_applies_previewed_larger_symmetric_shock(self):
        battle = self._start_battle()
        self.assertGreater(
            battle.creature_1_potential_change,
            battle.creature_2_potential_change,
        )
        shock = battle.creature_1_potential_change
        participants = self._finish_with_winner(battle, winner_index=0)

        BattleService.process_battle_result(battle.pk)

        self.underdog.refresh_from_db()
        self.favorite.refresh_from_db()
        participants[0].refresh_from_db()
        participants[1].refresh_from_db()
        self.assertEqual(
            self.underdog.pricing_reference_price,
            self._expected_reference(shock),
        )
        self.assertEqual(
            self.favorite.pricing_reference_price,
            self._expected_reference(-shock),
        )
        self.assertEqual(participants[0].price_before, Decimal('100.00'))
        self.assertEqual(
            participants[0].price_after,
            self.underdog.current_price,
        )
        self.assertEqual(participants[1].price_before, Decimal('100.00'))
        self.assertEqual(
            participants[1].price_after,
            self.favorite.current_price,
        )

    def test_active_preview_displays_the_same_symmetric_shock_rule(self):
        battle = self._start_battle()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('creature:battle_detail', args=[battle.pk])
        )

        self.assertContains(
            response,
            (
                f'{self.underdog.name} win: ▲ '
                f'+{battle.creature_1_potential_change}%'
            ),
        )
        self.assertContains(
            response,
            (
                f'{self.favorite.name} lose: ▼ '
                f'-{battle.creature_1_potential_change}%'
            ),
        )
        self.assertContains(
            response,
            (
                f'{self.favorite.name} win: ▲ '
                f'+{battle.creature_2_potential_change}%'
            ),
        )
        self.assertContains(
            response,
            (
                f'{self.underdog.name} lose: ▼ '
                f'-{battle.creature_2_potential_change}%'
            ),
        )

    def test_favorite_win_applies_previewed_smaller_symmetric_shock(self):
        battle = self._start_battle()
        shock = battle.creature_2_potential_change
        self.assertLess(
            shock,
            battle.creature_1_potential_change,
        )
        self._finish_with_winner(battle, winner_index=1)

        BattleService.process_battle_result(battle.pk)

        self.underdog.refresh_from_db()
        self.favorite.refresh_from_db()
        self.assertEqual(
            self.favorite.pricing_reference_price,
            self._expected_reference(shock),
        )
        self.assertEqual(
            self.underdog.pricing_reference_price,
            self._expected_reference(-shock),
        )
        winner_delta = (
            self.favorite.pricing_reference_price - Decimal('100.00')
        )
        loser_delta = (
            self.underdog.pricing_reference_price - Decimal('100.00')
        )
        self.assertEqual(winner_delta, -loser_delta)

    def test_draw_keeps_reference_prices_unchanged(self):
        battle = self._start_battle()
        participants = list(battle.participants.all())
        battle.status = 'finished'
        battle.winner = None
        battle.save(update_fields=['status', 'winner'])

        BattleService.process_battle_result(battle.pk)

        self.underdog.refresh_from_db()
        self.favorite.refresh_from_db()
        participants[0].refresh_from_db()
        participants[1].refresh_from_db()
        self.assertEqual(
            self.underdog.pricing_reference_price,
            Decimal('100.00'),
        )
        self.assertEqual(
            self.favorite.pricing_reference_price,
            Decimal('100.00'),
        )
        self.assertEqual(participants[0].price_before, Decimal('100.00'))
        self.assertEqual(participants[0].price_after, Decimal('100.00'))
        self.assertEqual(participants[1].price_before, Decimal('100.00'))
        self.assertEqual(participants[1].price_after, Decimal('100.00'))

    def test_result_retry_does_not_compound_reference_shock(self):
        Portfolio.objects.create(
            owner=self.user,
            creature=self.underdog,
            quantity=Decimal('1'),
            average_cost=Decimal('100.00'),
        )
        battle = self._start_battle()
        shock = battle.creature_1_potential_change
        participants = self._finish_with_winner(battle, winner_index=0)

        BattleService.process_battle_result(battle.pk)
        self.underdog.refresh_from_db()
        self.favorite.refresh_from_db()
        first_references = (
            self.underdog.pricing_reference_price,
            self.favorite.pricing_reference_price,
        )
        first_snapshots = []
        for participant in participants:
            participant.refresh_from_db()
            first_snapshots.append(participant.price_after)

        BattleService.process_battle_result(battle.pk)

        self.underdog.refresh_from_db()
        self.favorite.refresh_from_db()
        self.assertEqual(
            first_references,
            (
                self._expected_reference(shock),
                self._expected_reference(-shock),
            ),
        )
        self.assertEqual(
            (
                self.underdog.pricing_reference_price,
                self.favorite.pricing_reference_price,
            ),
            first_references,
        )
        for participant, first_snapshot in zip(
            participants,
            first_snapshots,
        ):
            participant.refresh_from_db()
            self.assertEqual(participant.price_after, first_snapshot)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user,
                notification_type=Notification.Type.BATTLE_RESULT,
            ).count(),
            1,
        )

    def test_periodic_pricing_does_not_count_historical_win_rate(self):
        battle = self._start_battle()
        winner = battle.participants.first()
        battle.status = 'finished'
        battle.winner = winner
        battle.result_processed_at = timezone.now()
        battle.save(update_fields=[
            'status',
            'winner',
            'result_processed_at',
        ])

        prices = [
            PricingEngine.update_creature_price(self.underdog)
            for _ in range(2)
        ]

        self.underdog.refresh_from_db()
        self.assertEqual(
            prices,
            [Decimal('100.00'), Decimal('100.00')],
        )
        self.assertEqual(
            self.underdog.pricing_reference_price,
            Decimal('100.00'),
        )

    def test_draw_notifications_use_drew_for_both_participants(self):
        for creature in (self.underdog, self.favorite):
            Portfolio.objects.create(
                owner=self.user,
                creature=creature,
                quantity=Decimal('1'),
                average_cost=Decimal('100.00'),
            )
        battle = self._start_battle()
        battle.status = 'finished'
        battle.winner = None
        battle.save(update_fields=['status', 'winner'])

        BattleService.process_battle_result(battle.pk)

        notifications = Notification.objects.filter(
            user=self.user,
            notification_type=Notification.Type.BATTLE_RESULT,
        )
        self.assertEqual(notifications.count(), 2)
        for notification in notifications:
            self.assertIn('drew', notification.title)
            self.assertIn('drew against', notification.message)
            self.assertNotIn('lost', notification.title)
            self.assertNotIn('lost against', notification.message)

    def test_public_battle_payload_does_not_expose_elo(self):
        battle = self._start_battle()

        payload = BattleService.battle_to_dict(battle)

        self.assertNotIn('elo', json.dumps(payload).lower())
