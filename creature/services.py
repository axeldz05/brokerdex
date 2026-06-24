import random
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from account.models import Account
from .models import Creature, EggTemplate, Incubation, Ability


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
