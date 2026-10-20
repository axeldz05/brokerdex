import random
from decimal import Decimal
from math import ceil

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum as models_Sum
from django.utils import timezone

from account.money import dollars_to_cents, quantize_money
from account.models import Account
from .models import (
    Battle,
    BattleAction,
    BattleInvestment,
    BattleParticipant,
    Creature,
    Incubation,
    TrainingSession,
)


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

            hatches_at = timezone.now() + egg_template.hatch_duration
            incubation = Incubation.objects.create(
                user=locked_user,
                egg_template=egg_template,
                hatches_at=hatches_at,
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
            if portfolio:
                portfolio.quantity += Decimal('1')
                portfolio.save(update_fields=['quantity', 'updated_at'])
            else:
                Portfolio.objects.create(
                    owner_id=incubation.user_id,
                    creature=creature,
                    quantity=Decimal('1'),
                    average_cost=creature.current_price,
                )

            completed_at = timezone.now()
            incubation.status = Incubation.Status.HATCHED
            incubation.hatched_at = completed_at
            incubation.hatched_creature = creature
            incubation.completion_notified_at = completed_at
            incubation.save(update_fields=[
                'status',
                'hatched_at',
                'hatched_creature',
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

    @staticmethod
    def calculate_potential_change(rating_self, rating_opponent):
        """
        Calculate potential price change percentages based on ELO difference.
        Returns (win_pct, lose_pct) as Decimal percentages.
        win_pct: positive value change if this creature wins.
        lose_pct: negative value change if this creature loses.
        """
        expected = Decimal('1') / (Decimal('1') + Decimal('10') ** (Decimal(rating_opponent - rating_self) / Decimal('400')))
        win_pct = ((Decimal('1') - expected) * BattleService.MAX_PRICE_CHANGE_PCT).quantize(Decimal('0.01'))
        lose_pct = (-expected * BattleService.MAX_PRICE_CHANGE_PCT).quantize(Decimal('0.01'))
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

    @staticmethod
    def apply_reference_price_shock(creature, change_pct):
        """Apply one persisted Decimal battle shock to a locked creature."""
        if change_pct == 0:
            return False
        reference_price = (
            creature.pricing_reference_price
            or creature.current_price
        )
        creature.pricing_reference_price = quantize_money(
            reference_price * (
                Decimal('1') + change_pct / Decimal('100')
            )
        )
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
        win_1, _ = cls.calculate_potential_change(
            creature_1.elo_rating, creature_2.elo_rating
        )
        win_2, _ = cls.calculate_potential_change(
            creature_2.elo_rating, creature_1.elo_rating
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

            if turn_number % 2:
                attacker, defender = participants
            else:
                defender, attacker = participants

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
                raw_damage = (
                    power * attack_stat / max(defense_stat, 1)
                ) / 2.5
                random_factor = random.randint(85, 100) / 100.0
                damage = max(1, int(raw_damage * random_factor))

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
            c1_fields = ['elo_rating', 'wins', 'losses']
            c2_fields = ['elo_rating', 'wins', 'losses']
            if cls.apply_reference_price_shock(c1, shock_1):
                c1_fields.append('pricing_reference_price')
            if cls.apply_reference_price_shock(c2, shock_2):
                c2_fields.append('pricing_reference_price')

            c1.save(update_fields=c1_fields)
            c2.save(update_fields=c2_fields)

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
