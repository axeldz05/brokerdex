import random
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Count, Q, Sum as models_Sum, F
from django.conf import settings

from account.models import Account
from .models import Creature, EggTemplate, Incubation, Ability, Battle, BattleParticipant, BattleAction, BattleInvestment


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
        from .tasks import hatch_egg

        price = egg_template.price

        with transaction.atomic():
            locked_user = Account.objects.select_for_update().get(pk=user.pk)

            cost_cents = int(price * 100)
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

        task = hatch_egg.apply_async(
            args=[str(incubation.pk)],
            eta=hatches_at,
        )

        incubation.celery_task_id = task.id
        incubation.save(update_fields=['celery_task_id'])

        return incubation

    @classmethod
    def hatch_egg(cls, incubation):
        """
        Execute hatching: select a random creature from the egg's pool,
        randomize its starter price, and credit it to the user's portfolio.
        """
        if incubation.status != Incubation.Status.INCUBATING:
            return incubation

        if timezone.now() < incubation.hatches_at:
            return incubation

        egg_template = incubation.egg_template
        creature_pool = list(egg_template.creature_pool.all())
        if not creature_pool:
            raise ValidationError(f"No creatures in pool for {egg_template.name}")

        creature = random.choice(creature_pool)

        from trading.models import Portfolio

        with transaction.atomic():
            incubation.status = Incubation.Status.HATCHED
            incubation.hatched_at = timezone.now()
            incubation.hatched_creature = creature
            incubation.save(update_fields=[
                'status', 'hatched_at', 'hatched_creature'
            ])

            Portfolio.objects.create(
                owner=incubation.user,
                creature=creature,
                quantity=Decimal('1'),
                average_cost=creature.current_price,
            )

        return incubation


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

    @classmethod
    def start_battle(cls, creature_1, creature_2):
        """
        Start a battle between two creatures with ELO snapshot and
        potential value change pre-computation.
        """
        win_1, lose_1 = cls.calculate_potential_change(
            creature_1.elo_rating, creature_2.elo_rating
        )
        win_2, lose_2 = cls.calculate_potential_change(
            creature_2.elo_rating, creature_1.elo_rating
        )

        from django.utils import timezone
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)

        with transaction.atomic():
            battle = Battle.objects.start_battle(creature_1, creature_2)

            creature_1_elo = creature_1.elo_rating
            creature_2_elo = creature_2.elo_rating

            battle.creature_1_elo_before = creature_1_elo
            battle.creature_2_elo_before = creature_2_elo
            battle.creature_1_potential_change = win_1  # win_pct for creature_1
            battle.creature_2_potential_change = win_2  # win_pct for creature_2
            battle.next_turn_at = timezone.now() + timezone.timedelta(seconds=turn_interval)
            battle.save(update_fields=[
                'creature_1_elo_before', 'creature_2_elo_before',
                'creature_1_potential_change', 'creature_2_potential_change',
                'next_turn_at',
            ])

        return battle

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
        if battle.status != 'finished':
            return

        participants = list(battle.participants.select_related('creature').all())
        if len(participants) != 2:
            return

        p1, p2 = participants
        winner = battle.winner

        with transaction.atomic():
            c1 = Creature.objects.select_for_update().get(pk=p1.creature_id)
            c2 = Creature.objects.select_for_update().get(pk=p2.creature_id)

            if winner and winner.pk == p1.pk:
                score_1, score_2 = 1, 0
            elif winner and winner.pk == p2.pk:
                score_1, score_2 = 0, 1
            else:
                score_1, score_2 = 0.5, 0.5  # Draw (fallback)

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

            c1.save(update_fields=['elo_rating', 'wins', 'losses'])
            c2.save(update_fields=['elo_rating', 'wins', 'losses'])

            battle.creature_1_elo_after = c1.elo_rating
            battle.creature_2_elo_after = c2.elo_rating
            battle.save(update_fields=['creature_1_elo_after', 'creature_2_elo_after'])

        from trading.services import PricingEngine
        PricingEngine.update_creature_price(c1)
        PricingEngine.update_creature_price(c2)

        cls._create_battle_notifications(battle, participants)

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
            direction = "won" if is_winner else "lost"
            emoji = "🏆" if is_winner else "💔"

            notifications = []
            for entry in holders:
                notifications.append(Notification(
                    user=entry.owner,
                    notification_type=Notification.Type.BATTLE_RESULT,
                    title=f"{emoji} {p.creature.name} {direction} the battle!",
                    message=(
                        f"{p.creature.name} {direction} against "
                        f"{battle.participants.exclude(pk=p.pk).first().creature.name}. "
                        f"Winner: {winner_name}."
                    ),
                    related_creature=p.creature,
                ))

            Notification.objects.bulk_create(notifications)

    @staticmethod
    def record_turn_investments(battle, turn_number):
        """
        Count Portfolio entries created for each creature since the last
        BattleAction or battle creation time. Stores results as BattleInvestment.
        """
        from trading.models import Portfolio

        participants = list(battle.participants.select_related('creature').all())
        if len(participants) != 2:
            return

        # Reference time: the previous action's timestamp, or battle creation
        prev_action = BattleAction.objects.filter(
            battle=battle, turn_number=turn_number
        ).order_by('timestamp').last()

        if prev_action:
            since = prev_action.timestamp
        else:
            since = battle.created_at

        for p in participants:
            new_investors = Portfolio.objects.filter(
                creature=p.creature,
                acquired_at__gte=since,
                acquired_at__lte=timezone.now(),
            ).count()

            total_qty = Portfolio.objects.filter(
                creature=p.creature,
                acquired_at__gte=since,
                acquired_at__lte=timezone.now(),
            ).aggregate(total=models_Sum('quantity'))['total'] or Decimal('0')

            BattleInvestment.objects.update_or_create(
                battle=battle,
                turn_number=turn_number,
                creature=p.creature,
                defaults={
                    'investor_count': new_investors,
                    'total_amount': total_qty,
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


class TrainingService:
    """
    Service for training creatures to boost their stats.
    """
    TRAINING_COST_MULTIPLIER = Decimal('0.10')
    STAT_BOOST_RANGE = (1, 5)

    @classmethod
    def get_training_cost(cls, creature):
        return (creature.current_price * cls.TRAINING_COST_MULTIPLIER).quantize(Decimal('0.01'))

    @classmethod
    def get_possible_stat_boosts(cls, creature):
        return random.randint(*cls.STAT_BOOST_RANGE)

    @classmethod
    def train_creature(cls, user, creature, portfolio_entry):
        """
        Pay training fee, boost stats asynchronously.
        """
        from .tasks import complete_training

        cost = cls.get_training_cost(creature)

        with transaction.atomic():
            locked_user = Account.objects.select_for_update().get(pk=user.pk)
            cost_cents = int(cost * 100)
            if locked_user.balance_cents < cost_cents:
                raise ValidationError(
                    f"Insufficient funds. Training costs ${cost}, "
                    f"have ${locked_user.balance_dollars}."
                )

            from trading.models import Portfolio
            locked_portfolio = Portfolio.objects.select_for_update().get(
                pk=portfolio_entry.pk
            )
            locked_user.balance_cents -= cost_cents
            locked_user.save(update_fields=['balance_cents'])

        task = complete_training.apply_async(
            args=[str(creature.pk), str(locked_portfolio.pk)],
            countdown=30,
        )
        return task.id
