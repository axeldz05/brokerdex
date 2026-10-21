import math
import random
from decimal import Decimal, ROUND_HALF_UP
from math import ceil

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum as models_Sum
from django.utils import timezone

from account.money import (
    cents_to_dollars,
    dollars_to_cents,
    quantize_money,
    quantize_quantity,
)
from account.models import Account
from banking.models import Transfer
from trading.models import Notification, Portfolio, SystemTreasury
from .models import (
    Battle,
    BattleAction,
    BattleInvestment,
    BattleParticipant,
    Creature,
    Incubation,
    TrainingContribution,
    TrainingFund,
    TrainingSaturation,
    TrainingSession,
)


TYPE_ADVANTAGES = {
    'fire': {'grass', 'ice', 'bug', 'steel'},
    'water': {'fire', 'ground', 'rock'},
    'grass': {'water', 'ground', 'rock'},
    'electric': {'water', 'flying'},
    'psychic': {'fighting', 'poison'},
    'fighting': {'normal', 'ice', 'rock', 'dark', 'steel'},
    'dark': {'psychic', 'ghost'},
    'steel': {'ice', 'rock', 'fairy'},
    'dragon': {'dragon'},
    'fairy': {'fighting', 'dragon', 'dark'},
    'normal': set(),
    'flying': {'grass', 'fighting', 'bug'},
    'ground': {'fire', 'electric', 'poison', 'rock', 'steel'},
    'rock': {'fire', 'ice', 'flying', 'bug'},
    'bug': {'grass', 'psychic', 'dark'},
    'ghost': {'psychic', 'ghost'},
    'ice': {'grass', 'ground', 'flying', 'dragon'},
    'poison': {'grass', 'fairy'},
}

TYPE_DISADVANTAGES = {
    'fire': {'water', 'rock', 'ground', 'dragon'},
    'water': {'grass', 'electric', 'dragon'},
    'grass': {'fire', 'ice', 'flying', 'bug', 'poison', 'steel', 'dragon'},
    'electric': {'ground', 'grass', 'electric', 'dragon'},
    'psychic': {'steel', 'psychic', 'dark'},
    'fighting': {'flying', 'psychic', 'fairy', 'bug', 'poison'},
    'dark': {'fighting', 'dark', 'fairy'},
    'steel': {'fire', 'water', 'electric', 'steel'},
    'dragon': {'steel', 'fairy'},
    'fairy': {'fire', 'poison', 'steel'},
    'normal': {'rock', 'steel', 'ghost'},
    'flying': {'electric', 'rock', 'steel'},
    'ground': {'grass', 'bug', 'flying'},
    'rock': {'fighting', 'ground', 'steel'},
    'bug': {'fire', 'fighting', 'flying', 'ghost', 'steel', 'fairy'},
    'ghost': {'dark', 'normal'},
    'ice': {'fire', 'water', 'ice', 'steel'},
    'poison': {'poison', 'ground', 'rock', 'ghost', 'steel'},
}


def get_type_multiplier(attacker_type: str, defender_type: str) -> Decimal:
    """Return 1.25 for advantage, 0.80 for disadvantage, 1.00 neutral."""
    att = (attacker_type or '').lower()
    dfn = (defender_type or '').lower()
    if dfn in TYPE_ADVANTAGES.get(att, set()):
        return Decimal('1.25')
    if dfn in TYPE_DISADVANTAGES.get(att, set()):
        return Decimal('0.80')
    return Decimal('1.00')


def _countdown_until(deadline, now):
    return max(0, ceil((deadline - now).total_seconds()))


def _recoverable_scheduling_q(deadline_field, status_choices, now):
    stale_before = now - timezone.timedelta(
        seconds=getattr(
            settings,
            'CREATURE_RECOVERY_GRACE_SECONDS',
            300,
        )
    )
    stale_scheduled = (
        Q(scheduling_status=status_choices.SCHEDULED)
        & Q(**{f'{deadline_field}__lte': now})
        & (
            Q(last_scheduling_attempt_at__isnull=True)
            | Q(last_scheduling_attempt_at__lte=stale_before)
        )
    )
    return (
        Q(scheduling_status__in=[
            status_choices.PENDING,
            status_choices.FAILED,
        ])
        | stale_scheduled
    )


class HatchConfigurationError(ValidationError):
    """A permanent hatch error that requires refunding the purchase."""


class TrainingNotReadyError(ValidationError):
    """A premature task delivery that must not refund training."""

    def __init__(self, session_id, execute_at, now):
        self.retry_after_seconds = _countdown_until(execute_at, now)
        super().__init__(
            f"Training session {session_id} is not ready."
        )


class IncubationService:
    """
    Service for purchasing and hatching eggs.
    """

    @classmethod
    def purchase_egg(cls, user, egg_template):
        """
        Purchase an egg: deduct balance, create incubation record,
        schedule Celery task for hatching.
        """
        price = egg_template.price
        if not egg_template.creature_pool.exists():
            raise HatchConfigurationError(
                f"No creatures in pool for {egg_template.name}"
            )

        with transaction.atomic():
            locked_user = Account.objects.select_for_update().get(pk=user.pk)

            cost_cents = dollars_to_cents(price)
            if locked_user.balance_cents < cost_cents:
                raise ValidationError(
                    f"Insufficient funds. Egg costs ${price}, "
                    f"have ${locked_user.balance_dollars}."
                )

            locked_user.balance_cents -= cost_cents
            locked_user.save(update_fields=['balance_cents'])

            dividend_cents = int((Decimal(cost_cents) * Decimal('0.08')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            equity_cents = int((Decimal(cost_cents) * Decimal('0.04')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            reserve_cents = cost_cents - dividend_cents - equity_cents

            treasury = SystemTreasury.get_for_update()
            treasury.dividend_pool_cents += dividend_cents
            treasury.company_equity_cents += equity_cents
            treasury.save(update_fields=['dividend_pool_cents', 'company_equity_cents', 'updated_at'])

            hatches_at = timezone.now() + egg_template.hatch_duration
            incubation = Incubation.objects.create(
                user=locked_user,
                egg_template=egg_template,
                hatches_at=hatches_at,
                reserve_contribution_cents=reserve_cents,
                dividend_cents=dividend_cents,
                equity_cents=equity_cents,
            )
            transaction.on_commit(
                lambda: cls.schedule_hatch(incubation.pk)
            )

        return incubation

    @classmethod
    def schedule_hatch(cls, incubation_id):
        from .tasks import hatch_egg

        now = timezone.now()
        with transaction.atomic():
            incubation = Incubation.objects.select_for_update().get(
                pk=incubation_id
            )
            if incubation.status != Incubation.Status.INCUBATING:
                return False
            max_attempts = getattr(settings, 'MAX_SCHEDULING_ATTEMPTS', 5)
            if incubation.scheduling_attempts >= max_attempts:
                cls.fail_hatch(
                    incubation.pk,
                    f"Scheduling exceeded max attempts ({max_attempts})",
                )
                return False
            incubation.scheduling_attempts += 1
            incubation.last_scheduling_attempt_at = now
            incubation.save(update_fields=[
                'scheduling_attempts',
                'last_scheduling_attempt_at',
            ])

        countdown = _countdown_until(incubation.hatches_at, now)
        try:
            task = hatch_egg.apply_async(
                args=[str(incubation.pk)],
                countdown=countdown,
            )
        except Exception as exc:
            Incubation.objects.filter(
                pk=incubation.pk,
                status=Incubation.Status.INCUBATING,
            ).update(
                scheduling_status=Incubation.SchedulingStatus.FAILED,
                scheduling_error=str(exc),
            )
            return False

        Incubation.objects.filter(
            pk=incubation.pk,
            status=Incubation.Status.INCUBATING,
        ).update(
            celery_task_id=task.id,
            scheduling_status=Incubation.SchedulingStatus.SCHEDULED,
            scheduling_error='',
        )
        return True

    @classmethod
    def recover_scheduling(cls):
        now = timezone.now()
        incubation_ids = Incubation.objects.filter(
            status=Incubation.Status.INCUBATING,
        ).filter(
            _recoverable_scheduling_q(
                'hatches_at',
                Incubation.SchedulingStatus,
                now,
            )
        ).values_list('pk', flat=True)

        return sum(
            bool(cls.schedule_hatch(incubation_id))
            for incubation_id in incubation_ids
        )

    @classmethod
    def hatch_egg(cls, incubation):
        """
        Execute hatching: select a random creature from the egg's pool,
        randomize its starter price, and credit it to the user's portfolio.
        """
        incubation_id = getattr(incubation, 'pk', incubation)
        from trading.models import Portfolio
        from trading.models import Notification

        with transaction.atomic():
            incubation = (
                Incubation.objects.select_for_update()
                .select_related('egg_template', 'user')
                .get(pk=incubation_id)
            )
            if incubation.status != Incubation.Status.INCUBATING:
                return incubation
            if timezone.now() < incubation.hatches_at:
                raise ValidationError(
                    f"Incubation {incubation.pk} is not ready to hatch."
                )

            creature_pool = list(
                incubation.egg_template.creature_pool.all()
            )
            if not creature_pool:
                raise HatchConfigurationError(
                    f"No creatures in pool for "
                    f"{incubation.egg_template.name}"
                )
            creature = random.choice(creature_pool)
            creature = Creature.objects.select_for_update().get(
                pk=creature.pk
            )

            Account.objects.select_for_update().get(pk=incubation.user_id)
            portfolio = (
                Portfolio.objects.select_for_update()
                .filter(
                    owner_id=incubation.user_id,
                    creature=creature,
                )
                .first()
            )

            roll = random.random()
            if roll < 0.75:
                z_factor = random.uniform(0.25, 0.80)
            elif roll < 0.97:
                z_factor = random.uniform(0.80, 2.20)
            else:
                z_factor = random.uniform(2.20, 12.00)

            c_egg = cents_to_dollars(dollars_to_cents(incubation.egg_template.price))
            p_spot = creature.current_price
            if p_spot <= Decimal('0'):
                p_spot = Decimal('1.00')
            q_raw = (c_egg * Decimal(str(z_factor))) / p_spot
            hatched_units = max(Decimal('0.00000001'), quantize_quantity(q_raw))

            if portfolio:
                portfolio.quantity += hatched_units
                portfolio.save(update_fields=['quantity', 'updated_at'])
            else:
                Portfolio.objects.create(
                    owner_id=incubation.user_id,
                    creature=creature,
                    quantity=hatched_units,
                    average_cost=creature.current_price,
                )

            if incubation.reserve_contribution_cents > 0:
                creature.reserve_cents += incubation.reserve_contribution_cents
                creature.save(update_fields=['reserve_cents'])

            completed_at = timezone.now()
            incubation.status = Incubation.Status.HATCHED
            incubation.hatched_at = completed_at
            incubation.hatched_creature = creature
            incubation.hatched_quantity = hatched_units
            incubation.completion_notified_at = completed_at
            incubation.save(update_fields=[
                'status',
                'hatched_at',
                'hatched_creature',
                'hatched_quantity',
                'completion_notified_at',
            ])

            Notification.objects.create(
                user_id=incubation.user_id,
                notification_type=Notification.Type.INCUBATION_COMPLETE,
                title=f"{creature.name} hatched!",
                message=(
                    f"Your {incubation.egg_template.name} hatched into "
                    f"{creature.name}."
                ),
                related_creature=creature,
            )

        return incubation

    @classmethod
    def fail_hatch(cls, incubation_id, reason):
        """Mark a permanent hatch failure and refund exactly once."""
        with transaction.atomic():
            incubation = (
                Incubation.objects.select_for_update()
                .select_related('egg_template')
                .get(pk=incubation_id)
            )
            if incubation.status != Incubation.Status.INCUBATING:
                return incubation, False

            account = Account.objects.select_for_update().get(
                pk=incubation.user_id
            )
            account.balance_cents += dollars_to_cents(
                incubation.egg_template.price
            )
            account.save(update_fields=['balance_cents'])

            if incubation.dividend_cents > 0 or incubation.equity_cents > 0:
                treasury = SystemTreasury.get_for_update()
                treasury.dividend_pool_cents = max(0, treasury.dividend_pool_cents - incubation.dividend_cents)
                treasury.company_equity_cents = max(0, treasury.company_equity_cents - incubation.equity_cents)
                treasury.save(update_fields=['dividend_pool_cents', 'company_equity_cents', 'updated_at'])

            failed_at = timezone.now()
            incubation.status = Incubation.Status.FAILED
            incubation.failed_at = failed_at
            incubation.failure_reason = str(reason)
            incubation.refunded_at = failed_at
            incubation.scheduling_status = (
                Incubation.SchedulingStatus.FAILED
            )
            incubation.scheduling_error = str(reason)
            incubation.save(update_fields=[
                'status',
                'failed_at',
                'failure_reason',
                'refunded_at',
                'scheduling_status',
                'scheduling_error',
            ])
            return incubation, True


class BattleService:
    """
    Service for battle lifecycle: ELO-based valuation, turn timing,
    investment tracking, leaderboard, and battle queries.
    ELO rating is NEVER exposed to the user — used only for price impact calculation.
    """

    K_FACTOR = 32  # Standard ELO K-factor
    MAX_PRICE_CHANGE_PCT = Decimal('5.00')  # Max ±5% price swing from a battle
    KAPPA = Decimal(str(math.log(1.05)))

    @staticmethod
    def calculate_combat_power(creature):
        """Combine hp, attack, defense, special_attack, special_defense, speed."""
        if not creature:
            return Decimal('100')
        return Decimal(str(
            creature.hp + creature.attack + creature.defense +
            creature.special_attack + creature.special_defense + creature.speed
        ))

    @staticmethod
    def calculate_elo_change(rating_self, rating_opponent, score_self):
        """
        Standard ELO rating change formula.
        score_self = 1 for win, 0 for loss.
        Returns the change in rating (positive or negative).
        """
        expected = Decimal('1') / (Decimal('1') + Decimal('10') ** (Decimal(rating_opponent - rating_self) / Decimal('400')))
        change = Decimal(str(BattleService.K_FACTOR)) * (Decimal(str(score_self)) - expected)
        return int(round(change))

    @classmethod
    def calculate_victory_expectation(cls, rating_self, rating_opponent, cp_self=None, cp_opponent=None):
        """
        Calculate victory expectation E_i in [0, 1] incorporating ELO and relative Combat Power.
        """
        r_diff = Decimal(rating_opponent - rating_self)
        if cp_self is not None and cp_opponent is not None:
            cp_s = Decimal(str(cp_self))
            cp_o = Decimal(str(cp_opponent))
            cp_total = cp_s + cp_o
            if cp_total > 0:
                cp_diff = (cp_s - cp_o) / cp_total
                r_diff -= Decimal('800') * cp_diff

        expected = Decimal('1') / (
            Decimal('1') + Decimal('10') ** (r_diff / Decimal('400'))
        )
        return expected

    @classmethod
    def calculate_potential_change(cls, rating_self, rating_opponent, cp_self=None, cp_opponent=None):
        """
        Calculate potential price change percentages based on expectation E_i.
        Uses exponential symmetric shock:
        win_pct = (exp(kappa * (1 - E_i)) - 1) * 100
        lose_pct = (exp(kappa * (0 - E_i)) - 1) * 100
        """
        expected = cls.calculate_victory_expectation(
            rating_self, rating_opponent, cp_self=cp_self, cp_opponent=cp_opponent
        )
        kappa = cls.KAPPA
        win_factor = Decimal(str(math.exp(float(kappa * (Decimal('1') - expected)))))
        lose_factor = Decimal(str(math.exp(float(kappa * (Decimal('0') - expected)))))

        win_pct = ((win_factor - Decimal('1')) * Decimal('100')).quantize(Decimal('0.01'))
        lose_pct = ((lose_factor - Decimal('1')) * Decimal('100')).quantize(Decimal('0.01'))
        return win_pct, lose_pct

    @staticmethod
    def calculate_price_shocks(potential_change_1, potential_change_2):
        """
        Return the symmetric reference-price shocks for every battle outcome.

        The active preview and result processing both use these persisted
        potential changes, so the displayed ELO impact is the applied impact.
        """
        change_1 = Decimal(str(potential_change_1))
        change_2 = Decimal(str(potential_change_2))
        zero = Decimal('0')
        return {
            'creature_1_wins': (change_1, -change_1),
            'creature_2_wins': (-change_2, change_2),
            'draw': (zero, zero),
        }

    @classmethod
    def apply_reference_price_shock(cls, creature, score, expected=None, change_pct=None):
        """
        Apply exponential symmetric battle shock:
        P_ref, final = quantize_money(P_ref, initial * exp(kappa * (score - expected)))
        where score in {1, 0, 0.5}.
        A draw (score = 0.5) leaves reference price unchanged.
        """
        if score == Decimal('0.5') or score == 0.5:
            return False

        reference_price = (
            creature.pricing_reference_price
            or creature.current_price
        )

        if expected is None:
            if score == 0:
                return False
            creature.pricing_reference_price = quantize_money(
                reference_price * (
                    Decimal('1') + Decimal(str(score)) / Decimal('100')
                )
            )
            return True

        kappa = cls.KAPPA
        exponent = float(kappa * (Decimal(str(score)) - Decimal(str(expected))))
        multiplier = Decimal(str(math.exp(exponent)))
        creature.pricing_reference_price = quantize_money(reference_price * multiplier)
        return True

    @classmethod
    def start_battle(cls, creature_1, creature_2):
        """
        Start a battle between two creatures with ELO snapshot and
        potential value change pre-computation.
        """
        with transaction.atomic():
            creatures = list(
                Creature.objects.select_for_update()
                .filter(pk__in=[creature_1.pk, creature_2.pk])
                .order_by('pk')
            )
            creatures_by_id = {
                creature.pk: creature for creature in creatures
            }
            return cls._start_locked_battle(
                creatures_by_id[creature_1.pk],
                creatures_by_id[creature_2.pk],
            )

    @classmethod
    def matchmake_random_battle(cls):
        now = timezone.now()
        with transaction.atomic():
            creatures = list(
                Creature.objects.select_for_update(skip_locked=True)
                .filter(
                    currently_in_battle=False,
                    cooldown_expires_at__lte=now,
                )
                .order_by('?')[:2]
            )
            if len(creatures) != 2:
                return None
            return cls._start_locked_battle(*creatures)

    @classmethod
    def _start_locked_battle(cls, creature_1, creature_2):
        cp_1 = cls.calculate_combat_power(creature_1)
        cp_2 = cls.calculate_combat_power(creature_2)
        win_1, _ = cls.calculate_potential_change(
            creature_1.elo_rating, creature_2.elo_rating, cp_self=cp_1, cp_opponent=cp_2
        )
        win_2, _ = cls.calculate_potential_change(
            creature_2.elo_rating, creature_1.elo_rating, cp_self=cp_2, cp_opponent=cp_1
        )
        price_shocks = cls.calculate_price_shocks(win_1, win_2)

        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)

        battle = Battle.objects.start_battle(creature_1, creature_2)

        battle.creature_1_elo_before = creature_1.elo_rating
        battle.creature_2_elo_before = creature_2.elo_rating
        battle.creature_1_potential_change = (
            price_shocks['creature_1_wins'][0]
        )
        battle.creature_2_potential_change = (
            price_shocks['creature_2_wins'][1]
        )
        battle.next_turn_at = (
            timezone.now() + timezone.timedelta(seconds=turn_interval)
        )
        battle.save(update_fields=[
            'creature_1_elo_before',
            'creature_2_elo_before',
            'creature_1_potential_change',
            'creature_2_potential_change',
            'next_turn_at',
        ])

        participants = list(
            battle.participants.select_related('creature').all()
        )
        for participant in participants:
            participant.price_before = participant.creature.current_price
            participant.save(update_fields=['price_before'])

        return battle

    @classmethod
    def process_turn(cls, battle_id, expected_turn=None):
        should_process_result = False
        with transaction.atomic():
            battle = Battle.objects.select_for_update().get(pk=battle_id)
            if battle.status != 'active':
                return {
                    'status': battle.status,
                    'battle': battle,
                    'processed': False,
                }
            if (
                expected_turn is not None
                and battle.current_turn != expected_turn
            ):
                return {
                    'status': battle.status,
                    'battle': battle,
                    'processed': False,
                }

            turn_number = battle.current_turn
            if BattleAction.objects.filter(
                battle=battle,
                turn_number=turn_number,
            ).exists():
                return {
                    'status': battle.status,
                    'battle': battle,
                    'processed': False,
                }

            participants = list(
                BattleParticipant.objects.select_for_update()
                .filter(battle=battle)
                .select_related('creature')
                .prefetch_related('creature__abilities')
                .order_by('id')
            )
            if len(participants) != 2:
                raise ValidationError(
                    f"Battle {battle.pk} must have two participants."
                )

            previous_turn = turn_number - 1
            if previous_turn >= 1:
                cls.record_turn_investments(battle, previous_turn)

            s1 = participants[0].creature.speed
            s2 = participants[1].creature.speed
            if s1 >= s2:
                opener, responder = participants[0], participants[1]
            else:
                opener, responder = participants[1], participants[0]

            if turn_number % 2 == 1:
                attacker, defender = opener, responder
            else:
                attacker, defender = responder, opener

            abilities = list(attacker.creature.abilities.all())
            if not abilities:
                cls._finish_battle_without_winner(battle, participants)
                should_process_result = True
            else:
                ability = random.choice(abilities)
                power = ability.power or 10
                if ability.damage_class == 'special':
                    attack_stat = attacker.creature.special_attack
                    defense_stat = defender.creature.special_defense
                else:
                    attack_stat = attacker.creature.attack
                    defense_stat = defender.creature.defense

                elemental_mult = get_type_multiplier(
                    attacker.creature.type, defender.creature.type
                )
                raw_damage = (
                    power * attack_stat / max(defense_stat, 1)
                ) / 2.5 * float(elemental_mult)

                # Speed ratio for critical hit / evasion
                att_speed = max(1, attacker.creature.speed)
                def_speed = max(1, defender.creature.speed)
                speed_ratio = att_speed / (att_speed + def_speed)

                # Evasion check: if defender has higher speed
                evasion_chance = 0.0
                if def_speed > att_speed:
                    evasion_chance = min(
                        0.20,
                        ((def_speed - att_speed) / (att_speed + def_speed)) * 0.25,
                    )

                if random.random() < evasion_chance:
                    damage = 0
                else:
                    crit_chance = min(0.30, max(0.05, speed_ratio * 0.20))
                    crit_mult = 1.5 if random.random() < crit_chance else 1.0
                    random_factor = random.randint(85, 100) / 100.0
                    damage = max(1, int(raw_damage * crit_mult * random_factor))

                battle.record_action(
                    actor_participant=attacker,
                    target_participant=defender,
                    ability=ability,
                    damage=damage,
                )
                battle.refresh_from_db()
                if battle.status == 'active':
                    turn_interval = getattr(
                        settings,
                        'BATTLE_TURN_INTERVAL',
                        180,
                    )
                    battle.next_turn_at = (
                        timezone.now()
                        + timezone.timedelta(seconds=turn_interval)
                    )
                    battle.save(update_fields=['next_turn_at'])
                else:
                    cls.record_turn_investments(battle, turn_number)
                    should_process_result = True

        if should_process_result:
            battle = cls.process_battle_result(battle_id)

        return {
            'status': battle.status,
            'battle': battle,
            'processed': True,
            'turn_number': turn_number,
        }

    @staticmethod
    def _finish_battle_without_winner(battle, participants):
        creature_ids = [
            participant.creature_id for participant in participants
        ]
        creatures = list(
            Creature.objects.select_for_update()
            .filter(pk__in=creature_ids)
            .order_by('pk')
        )
        now = timezone.now()
        for creature in creatures:
            creature.currently_in_battle = False
            creature.cooldown_expires_at = now + creature.battle_cooldown
            creature.save(update_fields=[
                'currently_in_battle',
                'cooldown_expires_at',
            ])
        battle.status = 'finished'
        battle.winner = None
        battle.save(update_fields=['status', 'winner'])

    @classmethod
    def recover_battles(cls, schedule_turn=None):
        """Recover overdue battles with an optional async scheduling edge."""
        now = timezone.now()
        active_battles = list(
            Battle.objects.filter(
                status='active',
                next_turn_at__lte=now,
            ).values_list('pk', 'current_turn')
        )
        for battle_id, current_turn in active_battles:
            if schedule_turn is None:
                cls.process_turn(battle_id, current_turn)
            else:
                schedule_turn(battle_id, current_turn)

        finished_ids = list(
            Battle.objects.filter(
                status='finished',
                result_processed_at__isnull=True,
            ).values_list('pk', flat=True)
        )
        for battle_id in finished_ids:
            cls.process_battle_result(battle_id)

        return len(active_battles) + len(finished_ids)

    @classmethod
    def process_battle_result(cls, battle):
        """
        Process the result of a finished battle:
        - Calculate ELO changes
        - Update creature wins/losses
        - Update creature prices via PricingEngine
        - Store post-battle ELO on the battle record
        - Create notifications for holders of both creatures
        """
        battle_id = getattr(battle, 'pk', battle)
        with transaction.atomic():
            battle = (
                Battle.objects.select_for_update()
                .get(pk=battle_id)
            )
            if (
                battle.status != 'finished'
                or battle.result_processed_at is not None
            ):
                return battle

            participants = list(
                BattleParticipant.objects.select_for_update()
                .filter(battle=battle)
                .select_related('creature')
                .order_by('id')
            )
            if len(participants) != 2:
                raise ValidationError(
                    f"Battle {battle.pk} must have two participants."
                )

            p1, p2 = participants
            locked_creatures = list(
                Creature.objects.select_for_update()
                .filter(pk__in=[p1.creature_id, p2.creature_id])
                .order_by('pk')
            )
            creatures_by_id = {
                creature.pk: creature for creature in locked_creatures
            }
            c1 = creatures_by_id[p1.creature_id]
            c2 = creatures_by_id[p2.creature_id]
            winner = battle.winner
            potential_change_1 = battle.creature_1_potential_change
            potential_change_2 = battle.creature_2_potential_change
            potential_fields = []
            if potential_change_1 is None:
                potential_change_1, _ = cls.calculate_potential_change(
                    c1.elo_rating,
                    c2.elo_rating,
                )
                battle.creature_1_potential_change = potential_change_1
                potential_fields.append('creature_1_potential_change')
            if potential_change_2 is None:
                potential_change_2, _ = cls.calculate_potential_change(
                    c2.elo_rating,
                    c1.elo_rating,
                )
                battle.creature_2_potential_change = potential_change_2
                potential_fields.append('creature_2_potential_change')

            if winner and winner.pk == p1.pk:
                score_1, score_2 = 1, 0
                outcome = 'creature_1_wins'
            elif winner and winner.pk == p2.pk:
                score_1, score_2 = 0, 1
                outcome = 'creature_2_wins'
            else:
                score_1, score_2 = 0.5, 0.5  # Draw (fallback)
                outcome = 'draw'

            elo_change_1 = cls.calculate_elo_change(
                c1.elo_rating, c2.elo_rating, score_1
            )
            elo_change_2 = cls.calculate_elo_change(
                c2.elo_rating, c1.elo_rating, score_2
            )

            c1.elo_rating += elo_change_1
            c2.elo_rating += elo_change_2

            if winner and winner.pk == p1.pk:
                c1.wins += 1
                c2.losses += 1
            elif winner and winner.pk == p2.pk:
                c2.wins += 1
                c1.losses += 1

            price_shocks = cls.calculate_price_shocks(
                potential_change_1,
                potential_change_2,
            )
            shock_1, shock_2 = price_shocks[outcome]

            c1_initial_ref = c1.pricing_reference_price or c1.current_price
            c2_initial_ref = c2.pricing_reference_price or c2.current_price

            c1_fields = ['elo_rating', 'wins', 'losses']
            c2_fields = ['elo_rating', 'wins', 'losses']
            if cls.apply_reference_price_shock(c1, shock_1):
                c1_fields.append('pricing_reference_price')
            if cls.apply_reference_price_shock(c2, shock_2):
                c2_fields.append('pricing_reference_price')

            # Liquidity transfer between reserves when there is a winner
            if winner and outcome != 'draw':
                winner_c = c1 if winner.pk == p1.pk else c2
                loser_c = c2 if winner.pk == p1.pk else c1
                w_initial_ref = c1_initial_ref if winner.pk == p1.pk else c2_initial_ref
                if w_initial_ref and w_initial_ref > 0:
                    shock_pct = (
                        winner_c.pricing_reference_price - w_initial_ref
                    ) / w_initial_ref
                    if shock_pct > 0:
                        raw_transfer = int(
                            Decimal(str(loser_c.reserve_cents)) * shock_pct
                        )
                        floor_cents = int(
                            Decimal(str(loser_c.reserve_cents)) * Decimal('0.10')
                        )
                        available = max(0, loser_c.reserve_cents - floor_cents)
                        transfer_cents = min(raw_transfer, available)
                        if transfer_cents > 0:
                            loser_c.reserve_cents -= transfer_cents
                            winner_c.reserve_cents += transfer_cents
                            if 'reserve_cents' not in c1_fields:
                                c1_fields.append('reserve_cents')
                            if 'reserve_cents' not in c2_fields:
                                c2_fields.append('reserve_cents')

            c1.save(update_fields=c1_fields)
            c2.save(update_fields=c2_fields)

            # Distribute solvent battle dividends
            if winner and outcome != 'draw':
                winner_creature = c1 if winner.pk == p1.pk else c2
                cls._distribute_battle_dividends(winner_creature)

            battle.creature_1_elo_after = c1.elo_rating
            battle.creature_2_elo_after = c2.elo_rating
            battle.save(update_fields=[
                'creature_1_elo_after',
                'creature_2_elo_after',
                *potential_fields,
            ])

            from trading.services import PricingEngine
            PricingEngine.update_creature_price(c1)
            PricingEngine.update_creature_price(c2)

            p1.price_after = c1.current_price
            p2.price_after = c2.current_price
            p1.save(update_fields=['price_after'])
            p2.save(update_fields=['price_after'])

            cls._create_battle_notifications(battle, participants)
            battle.result_processed_at = timezone.now()
            battle.save(update_fields=['result_processed_at'])

        return battle

    @staticmethod
    def _create_battle_notifications(battle, participants):
        """Create BATTLE_RESULT notifications for holders of both creatures."""
        from trading.models import Portfolio, Notification

        winner_name = battle.winner.creature.name if battle.winner else "Draw"

        for p in participants:
            holders = Portfolio.objects.filter(
                creature=p.creature,
                quantity__gt=0,
            ).select_related('owner').distinct()

            is_winner = battle.winner and battle.winner.pk == p.pk
            if battle.winner is None:
                direction = "drew"
                emoji = "🤝"
            else:
                direction = "won" if is_winner else "lost"
                emoji = "🏆" if is_winner else "💔"

            opponent = next((other for other in participants if other.pk != p.pk), None)
            opponent_name = opponent.creature.name if opponent else "Unknown"

            notifications = []
            for entry in holders:
                notifications.append(Notification(
                    user=entry.owner,
                    notification_type=Notification.Type.BATTLE_RESULT,
                    title=f"{emoji} {p.creature.name} {direction} the battle!",
                    message=(
                        f"{p.creature.name} {direction} against "
                        f"{opponent_name}. "
                        f"Winner: {winner_name}."
                    ),
                    related_creature=p.creature,
                ))

            Notification.objects.bulk_create(notifications)

    @classmethod
    def _distribute_battle_dividends(cls, winner_creature):
        """
        Distribute 0.2% of dividend_pool_cents to holders (and active training fund investors if any).
        Strict lock order: 1º Creature (already locked), 2º Accounts (pk ordered), 3º SystemTreasury.
        """
        portfolios = list(
            Portfolio.objects.filter(
                creature=winner_creature,
                quantity__gt=0,
            ).select_related('owner')
        )
        active_fund = TrainingFund.objects.filter(
            creature=winner_creature,
            status=TrainingFund.Status.COMPLETED,
            remaining_reward_battles__gt=0,
        ).order_by('closed_at', 'created_at').first()

        account_ids = {p.owner_id for p in portfolios}
        if active_fund:
            fund_investor_ids = active_fund.contributions.values_list('investor_id', flat=True)
            account_ids.update(fund_investor_ids)

        if not account_ids:
            return

        # 2º Account lock (ordered by pk)
        locked_accounts = {
            acc.pk: acc
            for acc in Account.objects.select_for_update()
            .filter(pk__in=sorted(account_ids))
            .order_by('pk')
        }

        # 3º SystemTreasury lock
        treasury = SystemTreasury.get_for_update()
        match_pot_cents = int(
            Decimal(str(treasury.dividend_pool_cents)) * Decimal('0.002')
        )
        if match_pot_cents <= 0:
            return

        fund_pot = 0
        if active_fund:
            fund_pot = int(Decimal(str(match_pot_cents)) * Decimal('0.20'))
        holders_pot = match_pot_cents - fund_pot

        total_held = sum(p.quantity for p in portfolios)
        distributed_holders = 0
        if total_held > 0 and holders_pot > 0:
            for p in portfolios:
                cents = int(
                    (p.quantity / total_held) * Decimal(str(holders_pot))
                )
                if cents > 0:
                    owner = locked_accounts.get(p.owner_id)
                    if owner:
                        owner.balance_cents += cents
                        owner.save(update_fields=['balance_cents'])
                        distributed_holders += cents
                        Transfer.objects.create(
                            sender=None,
                            receiver=owner,
                            amount=cents,
                            type=Transfer.TransactionType.DEPOSIT,
                            status=Transfer.TransactionStatus.COMPLETED,
                            description=f"Battle dividend: {winner_creature.name}",
                        )
                        Notification.objects.create(
                            user=owner,
                            notification_type=Notification.Type.BATTLE_RESULT,
                            title=f"Dividendo por victoria de {winner_creature.name}",
                            message=f"Recibiste {cents}¢ por la victoria de {winner_creature.name} en combate.",
                            related_creature=winner_creature,
                        )

        distributed_fund = 0
        if active_fund and fund_pot > 0:
            contributions = list(active_fund.contributions.all().select_related('investor'))
            total_shares = sum(c.weighted_shares for c in contributions)
            if total_shares > 0:
                for c in contributions:
                    inv_cents = int(
                        (c.weighted_shares / total_shares) * Decimal(str(fund_pot))
                    )
                    if inv_cents > 0:
                        inv = locked_accounts.get(c.investor_id)
                        if inv:
                            inv.balance_cents += inv_cents
                            inv.save(update_fields=['balance_cents'])
                            distributed_fund += inv_cents
                            Transfer.objects.create(
                                sender=None,
                                receiver=inv,
                                amount=inv_cents,
                                type=Transfer.TransactionType.DEPOSIT,
                                status=Transfer.TransactionStatus.COMPLETED,
                                description=f"Training Fund Dividend: {winner_creature.name}",
                            )
                            Notification.objects.create(
                                user=inv,
                                notification_type=Notification.Type.TRAINING_COMPLETE,
                                title=f"Rendimiento de Fondo: {winner_creature.name}",
                                message=f"Recibiste {inv_cents}¢ por la victoria de {winner_creature.name}.",
                                related_creature=winner_creature,
                            )
            active_fund.remaining_reward_battles -= 1
            active_fund.save(update_fields=['remaining_reward_battles'])

        total_distributed = distributed_holders + distributed_fund
        if total_distributed > 0:
            treasury.dividend_pool_cents -= total_distributed
            treasury.save(update_fields=['dividend_pool_cents', 'updated_at'])

    @staticmethod
    def record_turn_investments(battle, turn_number):
        """
        Count unique Trade buyers and bought volume during the turn window.
        """
        from trading.models import Trade

        participants = list(
            battle.participants.select_related('creature').all()
        )
        if len(participants) != 2:
            return

        # Reference time: the previous turn's action, or battle creation.
        prev_action = BattleAction.objects.filter(
            battle=battle,
            turn_number=turn_number - 1,
        ).order_by('timestamp').last()

        if prev_action:
            since = prev_action.timestamp
        else:
            since = battle.created_at

        until = timezone.now()
        for p in participants:
            investment = Trade.objects.filter(
                creature=p.creature,
                buyer__isnull=False,
                executed_at__gte=since,
                executed_at__lte=until,
            ).aggregate(
                investor_count=Count('buyer_id', distinct=True),
                total_amount=models_Sum('quantity'),
            )

            BattleInvestment.objects.update_or_create(
                battle=battle,
                turn_number=turn_number,
                creature=p.creature,
                defaults={
                    'investor_count': investment['investor_count'],
                    'total_amount': (
                        investment['total_amount'] or Decimal('0')
                    ),
                }
            )

    @staticmethod
    def get_active_battles():
        """Return active battles with participant data, ordered by turn urgency."""
        return Battle.objects.filter(status='active').prefetch_related(
            'participants__creature',
        ).order_by('next_turn_at')

    @staticmethod
    def get_battle_detail(battle_id):
        """Return a battle with all related data for detail view."""
        try:
            battle = Battle.objects.prefetch_related(
                'participants__creature',
                'actions__actor__creature',
                'actions__target__creature',
                'actions__ability',
                'investments',
            ).get(pk=battle_id)
            return battle
        except Battle.DoesNotExist:
            return None

    @staticmethod
    def get_leaderboard(limit=20):
        """
        Return creatures ordered by wins descending with win/loss record.
        ELO rating is NOT included in the output.
        """
        return Creature.objects.filter(
            Q(wins__gt=0) | Q(losses__gt=0)
        ).annotate(
            total_battles=F('wins') + F('losses'),
        ).order_by('-wins')[:limit]

    @staticmethod
    def get_user_creature_battles(user):
        """
        Return battles involving creatures in the user's portfolio.
        """
        from trading.models import Portfolio
        user_creature_ids = Portfolio.objects.filter(
            owner=user, quantity__gt=0
        ).values_list('creature_id', flat=True)

        return Battle.objects.filter(
            Q(status='finished') | Q(status='active'),
            participants__creature_id__in=user_creature_ids,
        ).distinct().prefetch_related(
            'participants__creature'
        ).order_by('-updated_at')

    @staticmethod
    def get_historical_battles(limit=20):
        """
        Return finished battles ordered by the absolute value change impact.
        """
        return Battle.objects.filter(
            status='finished'
        ).exclude(
            creature_1_potential_change__isnull=True,
            creature_2_potential_change__isnull=True,
        ).prefetch_related(
            'participants__creature'
        ).order_by('-updated_at')[:limit]

    @staticmethod
    def get_creature_battle_history(creature):
        """
        Return all battles a specific creature participated in.
        """
        return Battle.objects.filter(
            participants__creature=creature
        ).distinct().prefetch_related(
            'participants__creature'
        ).order_by('-updated_at')

    @staticmethod
    def get_top_battles(creature, limit=5):
        """
        Return the creature's top battles sorted by absolute valuation impact.
        Each entry is a dict with battle, opponent, change_pct, change_abs, won.
        """
        from decimal import Decimal as D
        battles = Battle.objects.filter(
            participants__creature=creature,
            status='finished',
        ).distinct().prefetch_related('participants__creature').order_by('-updated_at')

        entries = []
        for battle in battles:
            ps = list(battle.participants.all())
            if len(ps) != 2:
                continue
            is_p1 = (ps[0].creature_id == creature.id)
            p = ps[0] if is_p1 else ps[1]
            opp = ps[1] if is_p1 else ps[0]

            win_pct = battle.creature_1_potential_change if is_p1 else battle.creature_2_potential_change
            lose_pct = battle.creature_2_potential_change if is_p1 else battle.creature_1_potential_change
            win_pct = win_pct or D('0')
            lose_pct = lose_pct or D('0')

            won = bool(battle.winner and battle.winner.pk == p.pk)
            change_pct = win_pct if won else -lose_pct
            change_abs = (creature.current_price * change_pct / 100).quantize(D('0.01'))

            entries.append({
                'battle': battle,
                'opponent': opp.creature.name,
                'change_pct': change_pct,
                'change_abs': change_abs,
                'won': won,
            })

        entries.sort(key=lambda x: abs(x['change_pct']), reverse=True)
        return entries[:limit]

    @staticmethod
    def battle_to_dict(battle):
        """Convert a Battle with related data into a JSON-safe dict."""
        participants = list(battle.participants.all())
        p1 = participants[0] if participants else None
        p2 = participants[1] if len(participants) > 1 else None

        actions_data = []
        for action in battle.actions.all():
            actions_data.append({
                'turn': action.turn_number,
                'actor': action.actor.creature.name if action.actor else 'Unknown',
                'target': action.target.creature.name if action.target else 'Unknown',
                'ability': action.ability.name if action.ability else 'Unknown',
                'damage': action.damage_dealt,
                'description': f"{action.actor.creature.name if action.actor else 'Unknown'} used {action.ability.name if action.ability else 'Unknown'}!",
                'timestamp': action.timestamp.isoformat(),
            })

        investments_by_turn = {}
        for inv in battle.investments.all():
            key = inv.turn_number
            if key not in investments_by_turn:
                investments_by_turn[key] = []
            investments_by_turn[key].append({
                'creature': inv.creature.name,
                'count': inv.investor_count,
            })

        return {
            'id': battle.id,
            'status': battle.status,
            'current_turn': battle.current_turn,
            'participants': [
                {
                    'creature': p.creature.name,
                    'hp': p.current_hp,
                    'max_hp': p.creature.hp,
                    'icon_url': p.creature.small_icon_url,
                    'is_winner': battle.winner and battle.winner.pk == p.pk,
                }
                for p in [p1, p2] if p
            ],
            'potential_changes': {
                'creature_1': float(battle.creature_1_potential_change) if battle.creature_1_potential_change else None,
                'creature_2': float(battle.creature_2_potential_change) if battle.creature_2_potential_change else None,
            },
            'actions': actions_data,
            'investments_per_turn': investments_by_turn,
        }


class TrainingService:
    """
    Service for training creatures to boost their stats.
    """
    TRAINING_COST_MULTIPLIER = Decimal('0.10')
    STAT_BOOST_RANGE = (1, 5)

    @classmethod
    def get_training_cost(cls, creature):
        return quantize_money(
            creature.current_price * cls.TRAINING_COST_MULTIPLIER
        )

    @classmethod
    def get_possible_stat_boosts(cls, creature):
        return random.randint(*cls.STAT_BOOST_RANGE)

    @classmethod
    def train_creature(cls, user, creature, portfolio_entry):
        """
        Pay training fee, boost stats asynchronously.
        """
        with transaction.atomic():
            locked_creature = Creature.objects.select_for_update().get(
                pk=creature.pk
            )
            locked_user = Account.objects.select_for_update().get(pk=user.pk)
            from trading.models import Portfolio
            locked_portfolio = Portfolio.objects.select_for_update().get(
                pk=portfolio_entry.pk,
                owner=locked_user,
                creature=locked_creature,
            )
            cost = cls.get_training_cost(locked_creature)
            cost_cents = dollars_to_cents(cost)
            if locked_user.balance_cents < cost_cents:
                raise ValidationError(
                    f"Insufficient funds. Training costs ${cost}, "
                    f"have ${locked_user.balance_dollars}."
                )

            locked_user.balance_cents -= cost_cents
            locked_user.save(update_fields=['balance_cents'])
            execute_at = timezone.now() + timezone.timedelta(seconds=30)
            session = TrainingSession.objects.create(
                user=locked_user,
                creature=locked_creature,
                portfolio=locked_portfolio,
                cost_cents=cost_cents,
                execute_at=execute_at,
            )
            transaction.on_commit(
                lambda: cls.schedule_training(session.pk)
            )
        return session

    @classmethod
    def schedule_training(cls, session_id):
        from .tasks import complete_training

        now = timezone.now()
        with transaction.atomic():
            session = TrainingSession.objects.select_for_update().get(
                pk=session_id
            )
            if session.status != TrainingSession.Status.PENDING:
                return False
            max_attempts = getattr(settings, 'MAX_SCHEDULING_ATTEMPTS', 5)
            if session.scheduling_attempts >= max_attempts:
                cls.fail_training(
                    session.pk,
                    f"Scheduling exceeded max attempts ({max_attempts})",
                )
                return False
            session.scheduling_attempts += 1
            session.last_scheduling_attempt_at = now
            session.save(update_fields=[
                'scheduling_attempts',
                'last_scheduling_attempt_at',
            ])

        countdown = _countdown_until(session.execute_at, now)
        try:
            task = complete_training.apply_async(
                args=[str(session.pk)],
                countdown=countdown,
            )
        except Exception as exc:
            TrainingSession.objects.filter(
                pk=session.pk,
                status=TrainingSession.Status.PENDING,
            ).update(
                scheduling_status=(
                    TrainingSession.SchedulingStatus.FAILED
                ),
                scheduling_error=str(exc),
            )
            return False

        TrainingSession.objects.filter(
            pk=session.pk,
            status=TrainingSession.Status.PENDING,
        ).update(
            celery_task_id=task.id,
            scheduling_status=TrainingSession.SchedulingStatus.SCHEDULED,
            scheduling_error='',
        )
        return True

    @classmethod
    def recover_scheduling(cls):
        now = timezone.now()
        session_ids = TrainingSession.objects.filter(
            status=TrainingSession.Status.PENDING,
        ).filter(
            _recoverable_scheduling_q(
                'execute_at',
                TrainingSession.SchedulingStatus,
                now,
            )
        ).values_list('pk', flat=True)
        return sum(
            bool(cls.schedule_training(session_id))
            for session_id in session_ids
        )

    @classmethod
    def fail_training(cls, session_id, reason):
        """Mark a failed training terminal and refund its cost once."""
        with transaction.atomic():
            session = TrainingSession.objects.select_for_update().get(
                pk=session_id
            )
            if session.status != TrainingSession.Status.PENDING:
                return session, False

            refunded = session.refunded_at is None
            failed_at = timezone.now()
            if refunded:
                account = Account.objects.select_for_update().get(
                    pk=session.user_id
                )
                account.balance_cents += session.cost_cents
                account.save(update_fields=['balance_cents'])
                session.refunded_at = failed_at

            failure_reason = str(reason)
            session.status = TrainingSession.Status.FAILED
            session.failed_at = failed_at
            session.failure_reason = failure_reason
            session.scheduling_status = (
                TrainingSession.SchedulingStatus.FAILED
            )
            session.scheduling_error = failure_reason
            session.save(update_fields=[
                'status',
                'failed_at',
                'failure_reason',
                'refunded_at',
                'scheduling_status',
                'scheduling_error',
                'updated_at',
            ])
            return session, refunded

    @classmethod
    def complete_legacy_training(
        cls,
        creature_id,
        portfolio_id,
        task_id,
    ):
        """Adapt an in-flight pre-TrainingSession Celery message."""
        from trading.models import Portfolio

        with transaction.atomic():
            portfolio = (
                Portfolio.objects.select_for_update()
                .select_related('owner', 'creature')
                .get(pk=portfolio_id, creature_id=creature_id)
            )
            session = TrainingSession.objects.filter(
                celery_task_id=task_id
            ).first()
            if session is None:
                session = TrainingSession.objects.create(
                    user=portfolio.owner,
                    creature=portfolio.creature,
                    portfolio=portfolio,
                    cost_cents=0,
                    execute_at=timezone.now(),
                    celery_task_id=task_id,
                    scheduling_status=(
                        TrainingSession.SchedulingStatus.SCHEDULED
                    ),
                )
        return cls.complete_training(session)

    @classmethod
    def complete_training(cls, session):
        session_id = getattr(session, 'pk', session)
        from trading.models import Notification
        from trading.services import PricingEngine

        with transaction.atomic():
            session = (
                TrainingSession.objects.select_for_update()
                .select_related('creature', 'user')
                .get(pk=session_id)
            )
            if session.status != TrainingSession.Status.PENDING:
                return session
            now = timezone.now()
            if now < session.execute_at:
                raise TrainingNotReadyError(
                    session.pk,
                    session.execute_at,
                    now,
                )

            creature = Creature.objects.select_for_update().get(
                pk=session.creature_id
            )
            boost = cls.get_possible_stat_boosts(creature)
            stat = random.choice([
                'hp',
                'attack',
                'defense',
                'special_attack',
                'special_defense',
                'speed',
            ])
            setattr(creature, stat, getattr(creature, stat) + boost)
            creature.save(update_fields=[stat])
            PricingEngine.update_creature_price(creature)

            completed_at = timezone.now()
            Notification.objects.create(
                user_id=session.user_id,
                notification_type=Notification.Type.TRAINING_COMPLETE,
                title=f"{creature.name} completed training!",
                message=(
                    f"{creature.name} gained {boost} {stat.replace('_', ' ')}."
                ),
                related_creature=creature,
            )
            session.status = TrainingSession.Status.COMPLETED
            session.result_stat = stat
            session.result_boost = boost
            session.completed_at = completed_at
            session.completion_notified_at = completed_at
            session.save(update_fields=[
                'status',
                'result_stat',
                'result_boost',
                'completed_at',
                'completion_notified_at',
                'updated_at',
            ])
        return session

    @classmethod
    def create_fund(
        cls, creature, target_stat, soft_cap_cents, hard_cap_cents, duration_minutes=30
    ):
        """
        Create an open syndicated flash training fund.
        Caps are scaled by saturation index theta_{i,s} if present.
        """
        saturation = TrainingSaturation.objects.filter(
            creature=creature, stat=target_stat
        ).first()
        theta = saturation.saturation_index if saturation else Decimal('1.0000')

        scaled_hard_cap = int(
            (Decimal(hard_cap_cents) * theta).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )
        scaled_soft_cap = int(
            (Decimal(soft_cap_cents) * theta).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )

        now = timezone.now()
        expires_at = now + timezone.timedelta(minutes=duration_minutes)

        return TrainingFund.objects.create(
            creature=creature,
            target_stat=target_stat,
            soft_cap_cents=scaled_soft_cap,
            hard_cap_cents=scaled_hard_cap,
            opens_at=now,
            expires_at=expires_at,
            status=TrainingFund.Status.OPEN,
        )

    @classmethod
    def contribute_to_fund(cls, user, fund_id, amount_cents):
        """
        Contribute to an open training fund.
        Tranche multipliers:
        - u <= 0.30: 1.40x
        - 0.30 < u <= 0.75: 1.00x
        - u > 0.75: 0.75x
        If hard cap reached, resolves immediately.
        """
        if amount_cents <= 0:
            raise ValidationError("Contribution must be positive.")

        with transaction.atomic():
            fund = TrainingFund.objects.select_for_update().get(pk=fund_id)
            if fund.status != TrainingFund.Status.OPEN:
                raise ValidationError("Training fund is not open.")
            if timezone.now() >= fund.expires_at:
                raise ValidationError("Training fund has expired.")

            # Strict lock order: 1º Creature, 2º Account, 3º SystemTreasury
            creature = Creature.objects.select_for_update().get(pk=fund.creature_id)
            investor = Account.objects.select_for_update().get(pk=user.pk)

            remaining_cap = fund.hard_cap_cents - fund.funded_cents
            if remaining_cap <= 0:
                raise ValidationError("Training fund hard cap reached.")

            accepted_cents = min(amount_cents, remaining_cap)
            if investor.balance_cents < accepted_cents:
                raise ValidationError(
                    f"Insufficient funds. Need {accepted_cents}¢, have {investor.balance_cents}¢."
                )

            # Prior fill fraction
            u = Decimal(fund.funded_cents) / Decimal(fund.hard_cap_cents)
            if u <= Decimal('0.30'):
                multiplier = Decimal('1.40')
            elif u <= Decimal('0.75'):
                multiplier = Decimal('1.00')
            else:
                multiplier = Decimal('0.75')

            weighted_shares = (Decimal(accepted_cents) * multiplier).quantize(
                Decimal('0.0001'), rounding=ROUND_HALF_UP
            )

            # Deduct balance
            investor.balance_cents -= accepted_cents
            investor.save(update_fields=['balance_cents'])

            # Record transfer for ledger
            Transfer.objects.create(
                sender=investor,
                receiver=None,
                amount=accepted_cents,
                type=Transfer.TransactionType.WITHDRAW,
                status=Transfer.TransactionStatus.COMPLETED,
                description=f"Training Fund Contribution: {creature.name} ({fund.target_stat})",
            )

            # Record contribution
            contribution = TrainingContribution.objects.create(
                fund=fund,
                investor=investor,
                amount_cents=accepted_cents,
                tranche_multiplier=multiplier,
                weighted_shares=weighted_shares,
            )

            fund.funded_cents += accepted_cents
            fund.save(update_fields=['funded_cents', 'updated_at'])

            if fund.funded_cents == fund.hard_cap_cents:
                cls._resolve_fund_locked(fund, creature)

        return contribution

    @classmethod
    def _resolve_fund_locked(cls, fund, creature):
        """
        Execute locked resolution of completed fund:
        - 50% creature reserve, 40% dividend pool, 10% company equity
        - Stat mutation: 75% Breakthrough (+3..+7), 25% Volatile (+6..+10, secondary -3..-5)
        - Saturation theta_{i,s} update
        """
        total = fund.funded_cents
        creature_cents = int((Decimal(total) * Decimal('0.50')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        dividend_cents = int((Decimal(total) * Decimal('0.40')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        equity_cents = total - creature_cents - dividend_cents

        creature.reserve_cents += creature_cents

        treasury = SystemTreasury.get_for_update()
        treasury.dividend_pool_cents += dividend_cents
        treasury.company_equity_cents += equity_cents
        treasury.save(update_fields=['dividend_pool_cents', 'company_equity_cents', 'updated_at'])

        # Stat mutation
        target_stat = fund.target_stat
        current_val = getattr(creature, target_stat)
        stat_fields = ['reserve_cents', target_stat]

        if random.random() < 0.75:
            # Breakthrough (+3 to +7)
            boost = random.randint(3, 7)
            setattr(creature, target_stat, current_val + boost)
        else:
            # Volatile Specialization (+6 to +10 on target, -3 to -5 on secondary, floor 10)
            boost = random.randint(6, 10)
            setattr(creature, target_stat, current_val + boost)
            all_stats = ['hp', 'attack', 'defense', 'special_attack', 'special_defense', 'speed']
            candidates = [s for s in all_stats if s != target_stat]
            secondary_stat = random.choice(candidates)
            sec_val = getattr(creature, secondary_stat)
            penalty = random.randint(3, 5)
            setattr(creature, secondary_stat, max(10, sec_val - penalty))
            stat_fields.append(secondary_stat)

        creature.save(update_fields=stat_fields)

        # Update saturation
        now = timezone.now()
        fund.closed_at = now
        t_max = max(1.0, (fund.expires_at - fund.opens_at).total_seconds())
        duration = max(0.0, (now - fund.opens_at).total_seconds())
        v_fill = max(0.0, min(1.0, 1.0 - (duration / t_max)))

        saturation, _ = TrainingSaturation.objects.get_or_create(
            creature=creature, stat=target_stat
        )
        curr_theta = float(saturation.saturation_index)
        new_theta = max(1.0, curr_theta * math.exp(0.5 * (v_fill - 0.5)))
        saturation.saturation_index = Decimal(str(new_theta)).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP
        )
        saturation.save(update_fields=['saturation_index'])

        fund.status = TrainingFund.Status.COMPLETED
        fund.save(update_fields=['status', 'closed_at', 'updated_at'])

    @classmethod
    def expire_funds(cls):
        """
        Check and process expired open funds.
        - If funded_cents >= soft_cap_cents: resolves normally.
        - If funded_cents < soft_cap_cents: refunds 100% of contributions idempotently.
        """
        now = timezone.now()
        expired_funds = list(
            TrainingFund.objects.filter(
                status=TrainingFund.Status.OPEN,
                expires_at__lte=now,
            )
        )
        processed = 0
        for fund_stub in expired_funds:
            with transaction.atomic():
                fund = TrainingFund.objects.select_for_update().get(pk=fund_stub.pk)
                if fund.status != TrainingFund.Status.OPEN:
                    continue

                creature = Creature.objects.select_for_update().get(pk=fund.creature_id)

                if fund.funded_cents >= fund.soft_cap_cents:
                    cls._resolve_fund_locked(fund, creature)
                else:
                    contributions = list(fund.contributions.all().select_related('investor'))
                    investor_ids = sorted({c.investor_id for c in contributions})
                    locked_accounts = {
                        acc.pk: acc
                        for acc in Account.objects.select_for_update()
                        .filter(pk__in=investor_ids)
                        .order_by('pk')
                    }
                    for c in contributions:
                        inv = locked_accounts.get(c.investor_id)
                        if inv:
                            inv.balance_cents += c.amount_cents
                            inv.save(update_fields=['balance_cents'])
                            Transfer.objects.create(
                                sender=None,
                                receiver=inv,
                                amount=c.amount_cents,
                                type=Transfer.TransactionType.DEPOSIT,
                                status=Transfer.TransactionStatus.COMPLETED,
                                description=f"Training Fund Refund: {creature.name} ({fund.target_stat})",
                            )
                    fund.status = TrainingFund.Status.EXPIRED_REFUNDED
                    fund.closed_at = now
                    fund.save(update_fields=['status', 'closed_at', 'updated_at'])
                processed += 1
        return processed
