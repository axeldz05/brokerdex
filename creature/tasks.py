import logging

from celery import shared_task
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from .models import Creature, Battle, Incubation
from .services import IncubationService, TrainingService, BattleService
from redis.exceptions import ConnectionError as RedisConnectionError
import random

logger = logging.getLogger(__name__)

@shared_task(autoretry_for=(RedisConnectionError,), retry_backoff=True, max_retries=3)
def matchmake_random_battle():
    now = timezone.now()
    available_creatures = Creature.objects.filter(
        currently_in_battle=False,
        cooldown_expires_at__lte=now
    ).order_by('?')[:2]

    if len(available_creatures) == 2:
        c1, c2 = available_creatures
        battle = BattleService.start_battle(c1, c2)
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)
        process_battle_turn.apply_async(args=[battle.id], countdown=turn_interval)
        return f"Battle {battle.id} initialized: {c1.name} vs {c2.name}"

    return "Not enough creatures to start a battle."

@shared_task(autoretry_for=(RedisConnectionError,), retry_backoff=True, max_retries=3)
def process_battle_turn(battle_id):
    try:
        battle = Battle.objects.get(id=battle_id, status='active')
    except Battle.DoesNotExist:
        return f"The battle {battle_id} was terminated or doesn't exist."

    try:
        participants = list(battle.participants.select_related('creature').all())
        if len(participants) != 2:
            return "Error: invalid amount of participants"

        # Record investments from the previous turn before processing this one
        previous_turn = battle.current_turn - 1
        if previous_turn >= 1:
            BattleService.record_turn_investments(battle, previous_turn)

        # Uneven = participant 1 attacks, Even = participant 2 attacks)
        if battle.current_turn % 2 != 0:
            attacker, defender = participants[0], participants[1]
        else:
            attacker, defender = participants[1], participants[0]

        abilities = list(attacker.creature.abilities.all())
        if not abilities:
            return f"The creature {attacker.creature.name} has no abilities"
        ability = random.choice(abilities)
        power = ability.power if ability.power else 10
        # Use appropriate attack/defense based on move damage class
        if ability.damage_class == 'special':
            attack_stat = attacker.creature.special_attack
            defense_stat = defender.creature.special_defense
        else:
            attack_stat = attacker.creature.attack
            defense_stat = defender.creature.defense
        # Scaled damage formula: (power * attack / defense) / 2.5 with 85-100% random factor
        raw_damage = (power * attack_stat / max(defense_stat, 1)) / 2.5
        random_factor = random.randint(85, 100) / 100.0
        damage = max(1, int(raw_damage * random_factor))

        battle.record_action(
            actor_participant=attacker,
            target_participant=defender,
            ability=ability,
            damage=damage
        )

        battle.refresh_from_db()

        if battle.status == 'active':
            turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)
            battle.next_turn_at = timezone.now() + timedelta(seconds=turn_interval)
            battle.save(update_fields=['next_turn_at'])
            process_battle_turn.apply_async(args=[battle.id], countdown=turn_interval)
            return f"Turn {battle.current_turn - 1} processed. Next turn in {turn_interval}s."
        else:
            # Record investments for the final turn
            BattleService.record_turn_investments(battle, battle.current_turn)
            # Process ELO and price changes
            BattleService.process_battle_result(battle)

            winner_name = battle.winner.creature.name if battle.winner else "Draw"
            return f"Battle {battle.id} has finished! Winner: {winner_name}"

    except Battle.DoesNotExist:
        return f"Battle {battle_id} was already removed."
    except Exception:
        logger.exception(f"process_battle_turn failed for battle {battle_id}")
        # Re-schedule to keep the chain alive despite transient errors
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)
        process_battle_turn.apply_async(args=[battle_id], countdown=turn_interval)
        return f"Battle {battle_id} turn failed, re-scheduled."


@shared_task
def hatch_egg(incubation_id):
    """
    Celery task: hatch an egg when the timer expires.
    Called asynchronously via ETA scheduling.
    """
    try:
        incubation = Incubation.objects.get(pk=incubation_id)
    except Incubation.DoesNotExist:
        return f"Incubation {incubation_id} not found."

    try:
        IncubationService.hatch_egg(incubation)
        return (
            f"Egg {incubation.egg_template.name} hatched! "
            f"Got {incubation.hatched_creature.name}"
        )
    except Exception as e:
        return f"Failed to hatch egg {incubation_id}: {e}"


@shared_task
def complete_training(creature_id, portfolio_id):
    """
    Celery task: complete creature training, boost stats.
    """
    try:
        creature = Creature.objects.get(pk=creature_id)
    except Creature.DoesNotExist:
        return f"Creature {creature_id} not found."

    from trading.models import Portfolio
    try:
        portfolio = Portfolio.objects.get(pk=portfolio_id)
    except Portfolio.DoesNotExist:
        return f"Portfolio {portfolio_id} not found."

    boost = TrainingService.get_possible_stat_boosts(creature)
    stat = random.choice(['hp', 'attack', 'defense', 'special_attack', 'special_defense', 'speed'])

    setattr(creature, stat, getattr(creature, stat) + boost)
    creature.save(update_fields=[stat])

    from trading.services import PricingEngine
    PricingEngine.update_creature_price(creature)

    return (
        f"{creature.name} trained: {stat} +{boost}. "
        f"New price: ${creature.current_price}"
    )
