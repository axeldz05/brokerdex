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
        self.charmander = Creature.objects.create(
            name="Charmander", type="fire", current_price=100.00, hp=39, attack=52, defense=43,
            battle_cooldown=timedelta(hours=1)
        )
        self.squirtle = Creature.objects.create(
            name="Squirtle", type="water", current_price=100.00, hp=44, attack=48, defense=65,
            battle_cooldown=timedelta(hours=1)
        )
        self.bulbasaur = Creature.objects.create(
            name="Bulbasaur", type="grass", current_price=100.00, hp=45,
            battle_cooldown=timedelta(hours=1)
        )

    def test_creature_can_have_up_to_4_abilities(self):
        self.charmander.abilities.add(self.scratch)
        self.charmander.abilities.add(self.ember)
        ab3 = Ability.objects.create(name="Move_3", ability_type="normal", damage_class="physical")
        ab4 = Ability.objects.create(name="Move_4", ability_type="normal", damage_class="physical")
        self.charmander.abilities.add(ab3, ab4)
        ab5 = Ability.objects.create(name="Move_5", ability_type="normal", damage_class="physical")
        with self.assertRaisesMessage(ValidationError, "A creature cannot have more than 4 abilities"):
            self.charmander.abilities.add(ab5)

    def test_creature_cannot_be_in_two_battles_simultaneously(self):
        Battle.objects.start_battle(self.charmander, self.squirtle)
        self.charmander.refresh_from_db()
        self.assertTrue(self.charmander.currently_in_battle)
        with self.assertRaisesMessage(ValidationError, "Charmander is not available to fight"):
            Battle.objects.start_battle(self.charmander, self.bulbasaur)

    def test_creature_cannot_battle_itself(self):
        with self.assertRaisesMessage(ValidationError, "A creature cannot fight with itself"):
            Battle.objects.start_battle(self.charmander, self.charmander)

    def test_battle_ends_when_creature_reaches_zero_hp(self):
        battle = Battle.objects.start_battle(self.charmander, self.squirtle)
        p_charmander = battle.participants.get(creature=self.charmander)
        p_squirtle = battle.participants.get(creature=self.squirtle)
        
        # Turn 1: Charmander attacks Squirtle (Damage = 20, Squirtle's HP drops to 24)
        battle.record_action(actor_participant=p_charmander, target_participant=p_squirtle, ability=self.scratch, damage=20)
        p_squirtle.refresh_from_db()
        self.assertEqual(p_squirtle.current_hp, 24)
        self.assertEqual(battle.status, 'active')
        
        # Turn 2: Charmander attacks again with lethal damage (Damage = 30)
        battle.record_action(actor_participant=p_charmander, target_participant=p_squirtle, ability=self.ember, damage=30)
        
        battle.refresh_from_db()
        p_squirtle.refresh_from_db()
        self.charmander.refresh_from_db()
        
        self.assertEqual(p_squirtle.current_hp, 0)
        self.assertEqual(battle.status, 'finished')
        self.assertEqual(battle.winner, p_charmander)
        self.assertFalse(self.charmander.currently_in_battle)
        self.assertFalse(self.charmander.is_available_for_battle())

    @patch('django.utils.timezone.now')
    def test_cooldown_expires_correctly(self, mock_now):
        from datetime import datetime, timezone as dt_timezone
        current_time = current_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        mock_now.return_value = current_time
        self.charmander.end_battle()
        future_time = current_time + timedelta(hours=2)
        mock_now.return_value = future_time
        self.assertTrue(self.charmander.is_available_for_battle())

    def test_creature_has_battle_stats(self):
        self.assertEqual(self.charmander.elo_rating, 1000)
        self.assertEqual(self.charmander.wins, 0)
        self.assertEqual(self.charmander.losses, 0)

    def test_battle_has_elo_and_turn_fields(self):
        battle = Battle.objects.start_battle(self.charmander, self.squirtle)
        participants = list(battle.participants.all())
        self.assertEqual(battle.creature_1_elo_before, 1000)
        self.assertEqual(battle.creature_2_elo_before, 1000)
        self.assertIsNotNone(battle.next_turn_at)
        self.assertIsNone(battle.creature_1_elo_after)
        self.assertIsNone(battle.creature_2_elo_after)

    def test_battle_investment_creation(self):
        battle = Battle.objects.start_battle(self.charmander, self.squirtle)
        inv = BattleInvestment.objects.create(
            battle=battle,
            turn_number=1,
            creature=self.charmander,
            investor_count=5,
            total_amount=Decimal('150.00')
        )
        self.assertEqual(inv.investor_count, 5)
        self.assertEqual(inv.battle, battle)
        self.assertEqual(inv.creature, self.charmander)
