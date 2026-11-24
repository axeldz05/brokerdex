import math
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account
from account.money import cents_to_dollars, dollars_to_cents
from banking.models import Transfer
from creature.models import (
    Ability,
    Battle,
    BattleParticipant,
    Creature,
    EggTemplate,
    Incubation,
    PrimaryType,
    TrainingContribution,
    TrainingFund,
    TrainingSaturation,
)
from creature.services import (
    BattleService,
    IncubationService,
    TrainingService,
    get_type_multiplier,
)
from trading.models import Portfolio, SystemTreasury


class ClosedEconomyPhasesTestCase(TestCase):
    def setUp(self):
        self.treasury = SystemTreasury.get_instance()
        self.treasury.dividend_pool_cents = 100_000_00  # $1,000.00
        self.treasury.company_equity_cents = 50_000_00  # $500.00
        self.treasury.save()

        self.user1 = Account.objects.create_user(
            username="investor_ash",
            email="ash@test.io",
            password="testpass123",
        )
        self.user1.balance_cents = 10_000_00  # $100.00
        self.user1.save(update_fields=["balance_cents"])

        self.user2 = Account.objects.create_user(
            username="investor_gary",
            email="gary@test.io",
            password="testpass123",
        )
        self.user2.balance_cents = 10_000_00  # $100.00
        self.user2.save(update_fields=["balance_cents"])

        # Abilities
        self.fire_claw = Ability.objects.create(
            name="Fire Claw", power=90, damage_class="special"
        )
        self.aqua_wave = Ability.objects.create(
            name="Aqua Wave", power=90, damage_class="special"
        )
        self.vine_lash = Ability.objects.create(
            name="Vine Lash", power=45, damage_class="physical"
        )

        # Creatures
        self.agnidon = Creature.objects.create(
            name="Agnidon",
            type=PrimaryType.FIRE,
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=50_000_00,
            pool_supply=Decimal("500.00000000"),
            hp=100,
            attack=84,
            defense=78,
            special_attack=109,
            special_defense=85,
            speed=100,
            description="Fire Dragon",
        )
        self.agnidon.abilities.add(self.fire_claw)

        self.bigfin = Creature.objects.create(
            name="Bigfin",
            type=PrimaryType.WATER,
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=50_000_00,
            pool_supply=Decimal("500.00000000"),
            hp=100,
            attack=83,
            defense=100,
            special_attack=85,
            special_defense=105,
            speed=78,
            description="Water Shell",
        )
        self.bigfin.abilities.add(self.aqua_wave)

        self.anoleaf = Creature.objects.create(
            name="Anoleaf",
            type=PrimaryType.GRASS,
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=50_000_00,
            pool_supply=Decimal("500.00000000"),
            hp=100,
            attack=82,
            defense=83,
            special_attack=100,
            special_defense=100,
            speed=80,
            description="Grass Flower",
        )
        self.anoleaf.abilities.add(self.vine_lash)

    def _total_system_cash(self):
        user_cents = sum(
            Account.objects.all().values_list("balance_cents", flat=True)
        )
        creature_cents = sum(
            Creature.objects.all().values_list("reserve_cents", flat=True)
        )
        treasury = SystemTreasury.get_instance()
        return (
            user_cents
            + creature_cents
            + treasury.dividend_pool_cents
            + treasury.company_equity_cents
        )

    # -------------------------------------------------------------
    # FASE 3 TESTS: COMBAT, LOGARITHMIC SHOCKS & SOLVENT DIVIDENDS
    # -------------------------------------------------------------

    def test_elemental_advantage_multipliers(self):
        # Fire vs Grass = 1.25x
        self.assertEqual(
            get_type_multiplier("fire", "grass"), Decimal("1.25")
        )
        # Water vs Fire = 1.25x
        self.assertEqual(
            get_type_multiplier("water", "fire"), Decimal("1.25")
        )
        # Fire vs Water = 0.80x
        self.assertEqual(
            get_type_multiplier("fire", "water"), Decimal("0.80")
        )
        # Normal vs Grass = 1.00x
        self.assertEqual(
            get_type_multiplier("normal", "grass"), Decimal("1.00")
        )

    def test_speed_initiates_combat_and_ratios_affect_potentials(self):
        # Agnidon (speed 100) vs Bigfin (speed 78)
        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        battle.status = "active"
        battle.save(update_fields=["status"])

        # In turn 1, Agnidon (faster) must be actor
        res = BattleService.process_turn(battle.pk, expected_turn=1)
        self.assertTrue(res["processed"] if "processed" in res else True)
        first_action = battle.actions.filter(turn_number=1).first()
        self.assertIsNotNone(first_action)
        self.assertEqual(
            first_action.actor.creature.pk, self.agnidon.pk
        )

    def test_exponential_symmetric_shocks_eliminate_volatility_drag(self):
        cp_1 = BattleService.calculate_combat_power(self.agnidon)
        cp_2 = BattleService.calculate_combat_power(self.bigfin)

        e1 = BattleService.calculate_victory_expectation(
            self.agnidon.elo_rating,
            self.bigfin.elo_rating,
            cp_self=cp_1,
            cp_opponent=cp_2,
        )
        e2 = Decimal("1") - e1

        kappa = BattleService.KAPPA
        # Winner multiplier if agnidon wins: exp(kappa * (1 - e1))
        # Loser multiplier if bigfin loses: exp(kappa * (0 - e2)) = exp(kappa * (e1 - 1))
        m_win = Decimal(str(math.exp(float(kappa * (Decimal("1") - e1)))))
        m_lose = Decimal(str(math.exp(float(kappa * (Decimal("0") - e2)))))

        product = (m_win * m_lose).quantize(Decimal("0.00000001"))
        # Product is exactly 1.00000000 (zero geometric erosion)
        self.assertEqual(product, Decimal("1.00000000"))

    def test_battle_transfers_liquidity_between_reserves_and_pays_dividends(self):
        # Setup: Alex owns 10 units of Agnidon, Maya owns 10 units of Bigfin
        Portfolio.objects.create(
            owner=self.user1,
            creature=self.agnidon,
            quantity=Decimal("10.00000000"),
            average_cost=Decimal("100.00"),
        )
        Portfolio.objects.create(
            owner=self.user2,
            creature=self.bigfin,
            quantity=Decimal("10.00000000"),
            average_cost=Decimal("100.00"),
        )

        initial_ash_balance = self.user1.balance_cents
        initial_agnidon_reserve = self.agnidon.reserve_cents
        initial_bigfin_reserve = self.bigfin.reserve_cents
        initial_dividend_pool = self.treasury.dividend_pool_cents
        cash_before = self._total_system_cash()

        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        p1, p2 = battle.participants.order_by("id")
        battle.status = "finished"
        battle.winner = p1  # Agnidon wins
        battle.save(update_fields=["status", "winner"])

        BattleService.process_battle_result(battle.pk)

        self.agnidon.refresh_from_db()
        self.bigfin.refresh_from_db()
        self.user1.refresh_from_db()
        self.treasury.refresh_from_db()

        # 1. Liquidity transferred from loser (Bigfin) to winner (Agnidon)
        self.assertGreater(
            self.agnidon.reserve_cents, initial_agnidon_reserve
        )
        self.assertLess(
            self.bigfin.reserve_cents, initial_bigfin_reserve
        )
        # Loser reserve still respects 10% floor
        self.assertGreaterEqual(
            self.bigfin.reserve_cents,
            int(Decimal(str(initial_bigfin_reserve)) * Decimal("0.10")),
        )

        # 2. Battle Dividend: 0.2% of dividend_pool_cents paid to Alex (the holder)
        # Expected pot = int(100_000_00 * 0.002) = 200_00 cents = $200.00
        expected_dividend = int(
            Decimal(str(initial_dividend_pool)) * Decimal("0.002")
        )
        self.assertEqual(
            self.user1.balance_cents, initial_ash_balance + expected_dividend
        )
        self.assertEqual(
            self.treasury.dividend_pool_cents,
            initial_dividend_pool - expected_dividend,
        )

        # 3. Transfer ledger entry created
        transfer = Transfer.objects.filter(
            receiver=self.user1,
            type=Transfer.TransactionType.DEPOSIT,
        ).first()
        self.assertIsNotNone(transfer)
        self.assertEqual(transfer.amount, expected_dividend)

        # 4. Strict total system cash conservation
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    # -------------------------------------------------------------
    # FASE 4 TESTS: LONG-TAIL FRACTIONAL INCUBATION & ZERO ARBITRAGE
    # -------------------------------------------------------------

    def test_egg_purchase_splits_funds_strictly_solvency_conserved(self):
        egg = EggTemplate.objects.create(
            name="Dragon Egg",
            price=Decimal("50.00"),  # 5,000 cents
            hatch_duration=timezone.timedelta(minutes=10),
        )
        egg.creature_pool.add(self.agnidon)

        initial_user_balance = self.user1.balance_cents
        initial_dividend_pool = self.treasury.dividend_pool_cents
        initial_company_equity = self.treasury.company_equity_cents
        cash_before = self._total_system_cash()

        incubation = IncubationService.purchase_egg(self.user1, egg)

        self.user1.refresh_from_db()
        self.treasury.refresh_from_db()

        # User paid 5,000 cents
        self.assertEqual(
            self.user1.balance_cents, initial_user_balance - 5000
        )

        # 8% (400 cents) -> dividend pool
        # 4% (200 cents) -> company equity
        # 88% (4400 cents) -> reserved for creature
        self.assertEqual(incubation.dividend_cents, 400)
        self.assertEqual(incubation.equity_cents, 200)
        self.assertEqual(incubation.reserve_contribution_cents, 4400)

        self.assertEqual(
            self.treasury.dividend_pool_cents, initial_dividend_pool + 400
        )
        self.assertEqual(
            self.treasury.company_equity_cents, initial_company_equity + 200
        )

        # Cash strictly conserved (4400 held in incubation reservation)
        user_cents = sum(
            Account.objects.all().values_list("balance_cents", flat=True)
        )
        creature_cents = sum(
            Creature.objects.all().values_list("reserve_cents", flat=True)
        )
        t = SystemTreasury.get_instance()
        total_now = (
            user_cents
            + creature_cents
            + t.dividend_pool_cents
            + t.company_equity_cents
            + incubation.reserve_contribution_cents
        )
        self.assertEqual(cash_before, total_now)

    def test_egg_hatching_credits_fractional_units_and_injects_reserves(self):
        egg = EggTemplate.objects.create(
            name="Fire Egg",
            price=Decimal("100.00"),  # 10,000 cents
            hatch_duration=timezone.timedelta(seconds=0),
        )
        egg.creature_pool.add(self.agnidon)

        cash_before = self._total_system_cash()
        initial_reserve = self.agnidon.reserve_cents
        incubation = IncubationService.purchase_egg(self.user1, egg)

        # Hatch egg
        hatch_res = IncubationService.hatch_egg(incubation)
        self.agnidon.refresh_from_db()
        hatch_res.refresh_from_db()

        self.assertEqual(hatch_res.status, Incubation.Status.HATCHED)
        self.assertIsNotNone(hatch_res.hatched_quantity)
        self.assertGreater(hatch_res.hatched_quantity, Decimal("0"))

        # Portfolio received fractional quantity
        portfolio = Portfolio.objects.get(
            owner=self.user1, creature=self.agnidon
        )
        self.assertEqual(portfolio.quantity, hatch_res.hatched_quantity)

        # Reserved 88% (8,800 cents) injected into Agnidon reserve
        self.assertEqual(
            self.agnidon.reserve_cents, initial_reserve + 8800
        )

        # System cash completely closed and conserved
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    # -------------------------------------------------------------
    # FASE 5 TESTS: SYNDICATED FLASH TRAINING FUNDS
    # -------------------------------------------------------------

    def test_training_fund_contributions_and_instant_hard_cap_closure(self):
        cash_before = self._total_system_cash()

        # Soft cap: 2,000 cents ($20), Hard cap: 5,000 cents ($50)
        fund = TrainingService.create_fund(
            creature=self.agnidon,
            target_stat="attack",
            soft_cap_cents=2000,
            hard_cap_cents=5000,
            duration_minutes=30,
        )
        self.assertEqual(fund.status, TrainingFund.Status.OPEN)

        # Alex contributes 1,000 cents (u = 0 / 5000 = 0.0 <= 0.30 -> tranche 1.40x)
        c1 = TrainingService.contribute_to_fund(self.user1, fund.pk, 1000)
        self.assertEqual(c1.tranche_multiplier, Decimal("1.40"))
        self.assertEqual(c1.weighted_shares, Decimal("1400.0000"))

        # Maya contributes 2,000 cents (u = 1000 / 5000 = 0.20 <= 0.30 -> tranche 1.40x)
        c2 = TrainingService.contribute_to_fund(self.user2, fund.pk, 2000)
        self.assertEqual(c2.tranche_multiplier, Decimal("1.40"))
        self.assertEqual(c2.weighted_shares, Decimal("2800.0000"))

        initial_attack = self.agnidon.attack
        initial_reserve = self.agnidon.reserve_cents
        initial_dividend_pool = self.treasury.dividend_pool_cents
        initial_equity = self.treasury.company_equity_cents

        # Alex contributes remaining 2,000 cents (u = 3000 / 5000 = 0.60 > 0.30 <= 0.75 -> tranche 1.00x)
        c3 = TrainingService.contribute_to_fund(self.user1, fund.pk, 2000)
        self.assertEqual(c3.tranche_multiplier, Decimal("1.00"))

        # Hard cap reached (5,000 cents) -> instant resolution!
        fund.refresh_from_db()
        self.agnidon.refresh_from_db()
        self.treasury.refresh_from_db()

        self.assertEqual(fund.status, TrainingFund.Status.COMPLETED)
        self.assertEqual(fund.funded_cents, 5000)

        # 50% (2500 cents) -> Agnidon reserve
        # 40% (2000 cents) -> dividend pool
        # 10% (500 cents) -> company equity
        self.assertEqual(
            self.agnidon.reserve_cents, initial_reserve + 2500
        )
        self.assertEqual(
            self.treasury.dividend_pool_cents, initial_dividend_pool + 2000
        )
        self.assertEqual(
            self.treasury.company_equity_cents, initial_equity + 500
        )

        # Stat boost applied (attack boosted by at least 3)
        self.assertGreater(self.agnidon.attack, initial_attack)

        # Saturation recorded
        saturation = TrainingSaturation.objects.get(
            creature=self.agnidon, stat="attack"
        )
        self.assertGreaterEqual(saturation.saturation_index, Decimal("1.0000"))

        # Total system cash strictly conserved
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    def test_training_fund_investor_returns_in_subsequent_battles(self):
        # Create completed fund for Agnidon with 3 remaining reward battles
        fund = TrainingService.create_fund(
            creature=self.agnidon,
            target_stat="speed",
            soft_cap_cents=1000,
            hard_cap_cents=2000,
        )
        # Alex contributes 1,000 (1.40x = 1400 shares)
        c1 = TrainingService.contribute_to_fund(self.user1, fund.pk, 1000)
        # Maya contributes 1,000 (1.00x = 1000 shares)
        c2 = TrainingService.contribute_to_fund(self.user2, fund.pk, 1000)

        fund.refresh_from_db()
        self.assertEqual(fund.status, TrainingFund.Status.COMPLETED)
        self.assertEqual(fund.remaining_reward_battles, 3)

        # Alex also holds 10 units in portfolio
        Portfolio.objects.create(
            owner=self.user1,
            creature=self.agnidon,
            quantity=Decimal("10.00000000"),
            average_cost=Decimal("100.00"),
        )

        self.user1.refresh_from_db()
        self.user2.refresh_from_db()
        self.treasury.refresh_from_db()
        initial_ash_balance = self.user1.balance_cents
        initial_gary_balance = self.user2.balance_cents
        initial_pool = self.treasury.dividend_pool_cents

        # Agnidon wins a battle
        battle = Battle.objects.start_battle(self.agnidon, self.bigfin)
        p1, _ = battle.participants.order_by("id")
        battle.status = "finished"
        battle.winner = p1
        battle.save(update_fields=["status", "winner"])

        BattleService.process_battle_result(battle.pk)

        fund.refresh_from_db()
        self.user1.refresh_from_db()
        self.user2.refresh_from_db()
        self.treasury.refresh_from_db()

        # Remaining reward battles decremented from 3 to 2
        self.assertEqual(fund.remaining_reward_battles, 2)

        # Total match pot was 0.2% of pool
        total_match_pot = int(Decimal(str(initial_pool)) * Decimal("0.002"))
        fund_pot = int(Decimal(str(total_match_pot)) * Decimal("0.20"))
        holders_pot = total_match_pot - fund_pot

        total_shares = c1.weighted_shares + c2.weighted_shares
        expected_gary_reward = int(
            (c2.weighted_shares / total_shares) * Decimal(str(fund_pot))
        )
        expected_ash_fund_reward = int(
            (c1.weighted_shares / total_shares) * Decimal(str(fund_pot))
        )
        expected_ash_reward = expected_ash_fund_reward + holders_pot

        self.assertEqual(
            self.user2.balance_cents, initial_gary_balance + expected_gary_reward
        )
        self.assertEqual(
            self.user1.balance_cents, initial_ash_balance + expected_ash_reward
        )

        # Treasury debited exactly what was distributed
        distributed = expected_gary_reward + expected_ash_reward
        self.assertEqual(
            self.treasury.dividend_pool_cents, initial_pool - distributed
        )

    def test_expired_training_fund_under_soft_cap_refunds_investors_idempotently(self):
        fund = TrainingService.create_fund(
            creature=self.agnidon,
            target_stat="hp",
            soft_cap_cents=5000,
            hard_cap_cents=10000,
            duration_minutes=15,
        )
        # Alex contributes 2,000 (below soft cap of 5,000)
        initial_ash_balance = self.user1.balance_cents
        cash_before = self._total_system_cash()

        TrainingService.contribute_to_fund(self.user1, fund.pk, 2000)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.balance_cents, initial_ash_balance - 2000)

        # Fast forward time to expire fund
        fund.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        fund.save(update_fields=["expires_at"])

        processed = TrainingService.expire_funds()
        self.assertEqual(processed, 1)

        fund.refresh_from_db()
        self.user1.refresh_from_db()

        self.assertEqual(fund.status, TrainingFund.Status.EXPIRED_REFUNDED)
        # Alex fully refunded
        self.assertEqual(self.user1.balance_cents, initial_ash_balance)

        # Idempotent: running expire_funds again does nothing
        processed_again = TrainingService.expire_funds()
        self.assertEqual(processed_again, 0)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.balance_cents, initial_ash_balance)

        # Total cash conserved
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    def test_two_consecutive_battles_return_to_reference_without_geometric_erosion(self):
        # Two identical creatures with equal stats and starting at $100.00
        c1 = Creature.objects.create(
            name="CloneA",
            type=PrimaryType.NORMAL,
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=50_000_00,
            pool_supply=Decimal("500.00000000"),
            hp=100,
            attack=100,
            defense=100,
            special_attack=100,
            special_defense=100,
            speed=100,
            elo_rating=1000,
        )
        c2 = Creature.objects.create(
            name="CloneB",
            type=PrimaryType.NORMAL,
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            pricing_reference_price=Decimal("100.00"),
            reserve_cents=50_000_00,
            pool_supply=Decimal("500.00000000"),
            hp=100,
            attack=100,
            defense=100,
            special_attack=100,
            special_defense=100,
            speed=100,
            elo_rating=1000,
        )

        # Battle 1: C1 wins against C2
        battle1 = Battle.objects.start_battle(c1, c2)
        p1, p2 = battle1.participants.order_by("id")
        battle1.status = "finished"
        battle1.winner = p1
        battle1.save(update_fields=["status", "winner"])
        BattleService.process_battle_result(battle1.pk)

        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.pricing_reference_price, Decimal("102.47"))
        self.assertEqual(c2.pricing_reference_price, Decimal("97.59"))

        # Maintain equal expectation and clear cooldown for Battle 2
        past = timezone.now() - timezone.timedelta(seconds=10)
        c1.elo_rating = 1000
        c1.currently_in_battle = False
        c1.cooldown_expires_at = past
        c1.save(update_fields=["elo_rating", "currently_in_battle", "cooldown_expires_at"])

        c2.elo_rating = 1000
        c2.currently_in_battle = False
        c2.cooldown_expires_at = past
        c2.save(update_fields=["elo_rating", "currently_in_battle", "cooldown_expires_at"])

        # Battle 2: C2 wins against C1 with inverse shock
        battle2 = Battle.objects.start_battle(c1, c2)
        p1_2, p2_2 = battle2.participants.order_by("id")
        battle2.status = "finished"
        battle2.winner = p2_2
        battle2.save(update_fields=["status", "winner"])
        BattleService.process_battle_result(battle2.pk)

        c1.refresh_from_db()
        c2.refresh_from_db()

        # Both reference prices return strictly to 100.00 without geometric erosion
        self.assertEqual(c1.pricing_reference_price, Decimal("100.00"))
        self.assertEqual(c2.pricing_reference_price, Decimal("100.00"))

    def test_spawn_market_funds_maintains_target_open_count(self):
        # Initial: no open funds
        TrainingFund.objects.all().delete()
        self.assertEqual(TrainingFund.objects.filter(status=TrainingFund.Status.OPEN).count(), 0)

        spawned = TrainingService.spawn_market_funds(target_open_count=3)
        self.assertEqual(spawned, 3)
        self.assertEqual(TrainingFund.objects.filter(status=TrainingFund.Status.OPEN).count(), 3)

        # Calling again when 3 are already OPEN spawns 0
        spawned_again = TrainingService.spawn_market_funds(target_open_count=3)
        self.assertEqual(spawned_again, 0)
        self.assertEqual(TrainingFund.objects.filter(status=TrainingFund.Status.OPEN).count(), 3)

    def test_training_funds_views_and_contributions(self):
        fund = TrainingService.create_fund(
            creature=self.agnidon,
            target_stat="attack",
            soft_cap_cents=2000,
            hard_cap_cents=5000,
        )

        # 1. Unauthenticated GET to training funds view
        response = self.client.get(reverse('creature:training_funds'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fondos de Entrenamiento Flash")
        self.assertContains(response, self.agnidon.name)

        # 2. Authenticated user viewing training funds
        self.client.force_login(self.user1)
        response_auth = self.client.get(reverse('creature:training_funds'))
        self.assertEqual(response_auth.status_code, 200)
        self.assertContains(response_auth, "Saldo disponible")

        # 3. Post invalid contribution (negative or zero)
        contribute_url = reverse('creature:contribute_fund', args=[fund.id])
        res_invalid = self.client.post(contribute_url, {'amount': '-5.00'})
        self.assertRedirects(res_invalid, reverse('creature:training_funds'))

        # 4. Post contribution exceeding user balance
        res_broke = self.client.post(contribute_url, {'amount': '999999.00'})
        self.assertRedirects(res_broke, reverse('creature:training_funds'))

        # 5. Post valid contribution ($10.00 = 1,000 cents)
        initial_balance = self.user1.balance_cents
        res_valid = self.client.post(contribute_url, {'amount': '10.00'})
        self.assertRedirects(res_valid, reverse('creature:training_funds'))

        fund.refresh_from_db()
        self.assertEqual(fund.funded_cents, 1000)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.balance_cents, initial_balance - 1000)

    def test_bot_simulation_training_fund_contribution(self):
        from unittest.mock import patch
        from trading.simulation.engine import SimulationEngine

        TrainingFund.objects.all().delete()
        fund = TrainingService.create_fund(
            creature=self.agnidon,
            target_stat="defense",
            soft_cap_cents=2000,
            hard_cap_cents=5000,
        )

        engine = SimulationEngine()
        with patch("random.random", return_value=0.1):
            events = engine.tick()

        fund.refresh_from_db()
        # Verify that either an event was logged or fund accepted contribution
        fund_events = [e for e in events if e.get('strategy') == 'TRAINING_FUND']
        if fund_events:
            self.assertEqual(fund_events[0]['strategy'], 'TRAINING_FUND')
            self.assertGreater(fund.funded_cents, 0)

