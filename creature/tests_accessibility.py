from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account

from .models import (
    Ability,
    Battle,
    BattleAction,
    Creature,
    EggTemplate,
    Incubation,
)


class BattleAccessibilityMarkupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Account.objects.create_user(
            username='battle-a11y-viewer',
            email='battle-accessibility@example.com',
            password='testpass123',
        )
        cls.ability = Ability.objects.create(
            name='Accessible Strike',
            ability_type='normal',
            damage_class='physical',
            power=40,
        )
        cls.creature_1 = cls.create_creature(
            name='Markup Alpha',
            creature_type='fire',
            hp=80,
        )
        cls.creature_2 = cls.create_creature(
            name='Markup Beta',
            creature_type='water',
            hp=100,
        )
        cls.creature_1.wins = 1
        cls.creature_1.save(update_fields=['wins'])
        cls.battle = Battle.objects.start_battle(
            cls.creature_1,
            cls.creature_2,
        )
        cls.participant_1, cls.participant_2 = cls.battle.participants.all()
        cls.participant_2.current_hp = 60
        cls.participant_2.save(update_fields=['current_hp'])
        BattleAction.objects.create(
            battle=cls.battle,
            turn_number=1,
            actor=cls.participant_1,
            target=cls.participant_2,
            ability=cls.ability,
            damage_dealt=40,
        )

    @classmethod
    def create_creature(cls, *, name, creature_type, hp):
        creature = Creature.objects.create(
            name=name,
            type=creature_type,
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=hp,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
        )
        creature.abilities.add(cls.ability)
        return creature

    def setUp(self):
        self.client.force_login(self.user)

    def test_battle_list_exposes_hp_values_and_live_countdown(self):
        response = self.client.get(reverse('creature:battle_list'))

        self.assertContains(response, 'aria-label="Markup Alpha HP"')
        self.assertContains(response, 'aria-valuenow="80"')
        self.assertContains(response, 'aria-valuemin="0"')
        self.assertContains(response, 'aria-valuemax="80"')
        self.assertContains(response, 'aria-valuetext="80 of 80 HP"')
        self.assertContains(response, 'role="timer"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-atomic="true"')
        self.assertContains(
            response,
            'aria-label="Time until next battle action"',
        )

    def test_battle_detail_uses_semantic_timeline_and_live_result(self):
        self.participant_1.price_before = Decimal('100.00')
        self.participant_1.price_after = Decimal('110.00')
        self.participant_1.save(update_fields=['price_before', 'price_after'])
        self.participant_2.price_before = Decimal('100.00')
        self.participant_2.price_after = Decimal('90.00')
        self.participant_2.save(update_fields=['price_before', 'price_after'])
        self.battle.status = 'finished'
        self.battle.winner = self.participant_1
        self.battle.save(update_fields=['status', 'winner'])

        response = self.client.get(
            reverse('creature:battle_detail', args=[self.battle.pk]),
        )

        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-atomic="true"')
        self.assertContains(response, 'aria-label="Battle result"')
        self.assertContains(
            response,
            '<ol class="timeline list-unstyled" aria-label="Battle turns">',
        )
        self.assertContains(
            response,
            '<li class="timeline-item mb-3">',
        )
        self.assertContains(response, 'Winner')
        self.assertContains(response, '▲')
        self.assertContains(response, '▼')

    def test_battle_controls_and_icons_have_accessible_metadata(self):
        response = self.client.get(reverse('creature:battle_list'))

        self.assertContains(
            response,
            (
                'id="active-tab" data-bs-toggle="tab" '
                'data-bs-target="#active" type="button" role="tab" '
                'aria-controls="active" aria-selected="true"'
            ),
        )
        self.assertContains(response, 'aria-hidden="true"')
        self.assertContains(response, 'Actions</span>')
        self.assertContains(
            response,
            'aria-label="View full battle history for Markup Alpha"',
        )


class IncubationAccessibilityMarkupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Account.objects.create_user(
            username='incubation-a11y',
            email='incubation-accessibility@example.com',
            password='testpass123',
        )
        cls.egg = EggTemplate.objects.create(
            name='Accessible Egg',
            description='An egg used for accessible markup tests.',
            price=Decimal('500.00'),
            hatch_duration=timedelta(hours=1),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_shop_labels_incubation_progress_timer_and_purchase_button(self):
        Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.get(reverse('creature:incubation_shop'))

        self.assertContains(
            response,
            'aria-label="Accessible Egg incubation progress"',
        )
        self.assertContains(response, 'aria-valuemin="0"')
        self.assertContains(response, 'aria-valuemax="100"')
        self.assertContains(response, 'percent complete"')
        self.assertContains(response, 'role="timer"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-atomic="true"')
        self.assertContains(
            response,
            'aria-label="Time until Accessible Egg hatches"',
        )
        self.assertContains(
            response,
            'aria-label="Purchase Accessible Egg"',
        )
        self.assertContains(
            response,
            '<i class="bi bi-cart me-1" aria-hidden="true"></i>',
            html=True,
        )

    def test_status_announces_hatched_result(self):
        now = timezone.now()
        Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=now - timedelta(minutes=30),
            status=Incubation.Status.HATCHED,
            hatched_at=now,
        )

        response = self.client.get(reverse('creature:incubation_status'))

        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-atomic="true"')
        self.assertContains(response, 'aria-label="Incubation result"')
        self.assertContains(response, 'Hatched!')
