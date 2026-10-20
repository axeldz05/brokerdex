from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from datetime import timedelta
from unittest.mock import patch
from .models import Creature, Ability, Battle, BattleInvestment

class CreatureAndBattleTests(TestCase):
    def setUp(self):
        self.scratch = Ability.objects.create(name="Scratch", ability_type="normal", damage_class="physical", power=40)
        self.ember = Ability.objects.create(name="Ember", ability_type="fire", damage_class="special", power=40)
        self.agnidon = Creature.objects.create(
            name="Agnidon", type="fire", current_price=100.00, hp=39, attack=52, defense=43,
            battle_cooldown=timedelta(hours=1)
        )
        self.bigfin = Creature.objects.create(
            name="Bigfin", type="water", current_price=100.00, hp=44, attack=48, defense=65,
            battle_cooldown=timedelta(hours=1)
        )
        self.anoleaf = Creature.objects.create(
            name="Anoleaf", type="grass", current_price=100.00, hp=45,
            battle_cooldown=timedelta(hours=1)
        )

    def test_creature_get_absolute_url(self):
        url = self.agnidon.get_absolute_url()
        self.assertEqual(url, f"/trading/creature/{self.agnidon.id}/")

    def test_creature_ability_methods(self):
        self.agnidon.abilities.add(self.scratch)
        qs = self.agnidon.get_abilities_queryset()
        self.assertIn(self.scratch, qs)

        # Remove by ID
        self.agnidon.remove_ability(self.scratch.id)
        self.assertFalse(self.agnidon.abilities.filter(id=self.scratch.id).exists())

        # Add and remove by instance
        self.agnidon.add_ability(self.ember)
        self.assertTrue(self.agnidon.abilities.filter(id=self.ember.id).exists())
        self.agnidon.remove_ability(self.ember)
        self.assertFalse(self.agnidon.abilities.filter(id=self.ember.id).exists())

    def test_creature_admin_form_initial_and_save(self):
        from creature.admin import CreatureForm
        self.agnidon.abilities.add(self.scratch)
        form = CreatureForm(instance=self.agnidon)
        self.assertIn(self.scratch, form.fields['abilities'].initial)

        # Test form submission and save
        data = {
            'name': 'Agnidon Updated',
            'description': 'A fire creature',
            'type': self.agnidon.type,
            'current_price': '105.00',
            'previous_close': '100.00',
            'hp': 39,
            'attack': 52,
            'defense': 43,
            'special_attack': 60,
            'special_defense': 50,
            'speed': 65,
            'battle_cooldown': '01:00:00',
            'cooldown_expires_at': '2026-10-20 00:00:00',
            'elo_rating': 1000,
            'wins': 0,
            'losses': 0,
            'abilities': [str(self.scratch.pk), str(self.ember.pk)],
        }
        bound_form = CreatureForm(data=data, instance=self.agnidon)
        self.assertTrue(bound_form.is_valid(), bound_form.errors)
        saved = bound_form.save()
        self.assertEqual(saved.abilities.count(), 2)

    def test_creature_can_have_up_to_4_abilities(self):
        self.agnidon.abilities.add(self.scratch)
        self.agnidon.abilities.add(self.ember)
        ab3 = Ability.objects.create(name="Move_3", ability_type="normal", damage_class="physical")
        ab4 = Ability.objects.create(name="Move_4", ability_type="normal", damage_class="physical")
        self.agnidon.abilities.add(ab3, ab4)
        ab5 = Ability.objects.create(name="Move_5", ability_type="normal", damage_class="physical")
        with self.assertRaisesMessage(ValidationError, "A creature cannot have more than 4 abilities"):
            self.agnidon.abilities.add(ab5)

    def test_creature_cannot_be_in_two_battles_simultaneously(self):
        Battle.objects.start_battle(self.agnidon, self.bigfin)
        self.agnidon.refresh_from_db()
        self.assertTrue(self.agnidon.currently_in_battle)
        with self.assertRaisesMessage(ValidationError, "Agnidon is not available to fight"):
            Battle.objects.start_battle(self.agnidon, self.anoleaf)

    def test_creature_cannot_battle_itself(self):
        with self.assertRaisesMessage(ValidationError, "A creature cannot fight with itself"):
            Battle.objects.start_battle(self.agnidon, self.agnidon)

    def test_battle_ends_when_creature_reaches_zero_hp(self):
        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        p_agnidon = battle.participants.get(creature=self.agnidon)
        p_bigfin = battle.participants.get(creature=self.bigfin)
        
        # Turn 1: Agnidon attacks Bigfin (Damage = 20, Bigfin's HP drops to 24)
        battle.record_action(actor_participant=p_agnidon, target_participant=p_bigfin, ability=self.scratch, damage=20)
        p_bigfin.refresh_from_db()
        self.assertEqual(p_bigfin.current_hp, 24)
        self.assertEqual(battle.status, 'active')
        
        # Turn 2: Agnidon attacks again with lethal damage (Damage = 30)
        battle.record_action(actor_participant=p_agnidon, target_participant=p_bigfin, ability=self.ember, damage=30)
        
        battle.refresh_from_db()
        p_bigfin.refresh_from_db()
        self.agnidon.refresh_from_db()
        
        self.assertEqual(p_bigfin.current_hp, 0)
        self.assertEqual(battle.status, 'finished')
        self.assertEqual(battle.winner, p_agnidon)
        self.assertFalse(self.agnidon.currently_in_battle)
        self.assertFalse(self.agnidon.is_available_for_battle())

    @patch('django.utils.timezone.now')
    def test_cooldown_expires_correctly(self, mock_now):
        from datetime import datetime, timezone as dt_timezone
        current_time = current_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        mock_now.return_value = current_time
        self.agnidon.end_battle()
        future_time = current_time + timedelta(hours=2)
        mock_now.return_value = future_time
        self.assertTrue(self.agnidon.is_available_for_battle())

    def test_creature_has_battle_stats(self):
        self.assertEqual(self.agnidon.elo_rating, 1000)
        self.assertEqual(self.agnidon.wins, 0)
        self.assertEqual(self.agnidon.losses, 0)

    def test_battle_has_elo_and_turn_fields(self):
        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        participants = list(battle.participants.all())
        self.assertEqual(battle.creature_1_elo_before, 1000)
        self.assertEqual(battle.creature_2_elo_before, 1000)
        self.assertIsNotNone(battle.next_turn_at)
        self.assertIsNone(battle.creature_1_elo_after)
        self.assertIsNone(battle.creature_2_elo_after)

    def test_battle_investment_creation(self):
        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        inv = BattleInvestment.objects.create(
            battle=battle,
            turn_number=1,
            creature=self.agnidon,
            investor_count=5,
            total_amount=Decimal('150.00')
        )
        self.assertEqual(inv.investor_count, 5)
        self.assertEqual(inv.battle, battle)
        self.assertEqual(inv.creature, self.agnidon)

    def test_get_type_color_filter(self):
        from creature.templatetags.type_colors import get_type_color
        from trading.templatetags.trading_tags import TYPE_COLORS
        for type_name, expected_color in TYPE_COLORS.items():
            self.assertEqual(get_type_color(type_name), expected_color)
            self.assertEqual(get_type_color(type_name.upper()), expected_color)
        self.assertEqual(get_type_color("unknown_type"), "#A8A878")
        self.assertEqual(get_type_color(""), "#A8A878")
