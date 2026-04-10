from celery import shared_task
from django.utils import timezone
from .models import Creature, Battle
import random

@shared_task
def matchmake_random_battle():
    now = timezone.now()
    # order_by('?') orders by random
    available_creatures = Creature.objects.filter(
        currently_in_battle=False,
        cooldown_expires_at__lte=now
    ).order_by('?')[:2]

    if available_creatures.count() == 2:
        c1, c2 = available_creatures
        battle = Battle.objects.start_battle(c1, c2)
        process_battle_turn.apply_async(args=[battle.id], countdown=5)
        return f"Battle {battle.id} initialized: {c1.name} vs {c2.name}"
        
    return "Not enough creatures to start a battle."

@shared_task
def process_battle_turn(battle_id):
    try:
        battle = Battle.objects.get(id=battle_id, status='active')
    except Battle.DoesNotExist:
        return f"The battle {battle_id} was terminated or doesn't exist."

    participants = list(battle.participants.select_related('creature').all())
    if len(participants) != 2:
        return "Error: invalid amount of participants"

    # Uneven = participant 1 attacks, Even = participant 2 attacks)
    if battle.current_turn % 2 != 0:
        attacker, defender = participants[0], participants[1]
    else:
        attacker, defender = participants[1], participants[0]
    
    # TODO: each creature should select its next ability instead of random
    # this assumes that the ability is an attack targeted to the other participant
    abilities = list(attacker.creature.abilities.all())
    if not abilities:
        return f"The creature {attacker.creature.name} has no abilities"
    ability = random.choice(abilities)
    power = ability.power if ability.power else 10 
    raw_damage = (power * attacker.creature.attack) / defender.creature.defense
    damage = max(1, int(raw_damage)) 

    battle.record_action(
        actor_participant=attacker,
        target_participant=defender,
        ability=ability,
        damage=damage
    )

    battle.refresh_from_db()
    
    if battle.status == 'active':
        process_battle_turn.apply_async(args=[battle.id], countdown=10)
        return f"Turn {battle.current_turn - 1} processed. Next turn is queued."
    else:
        winner_name = battle.winner.creature.name if battle.winner else "Draw"
        return f"Battle {battle.id} has finished! Winner: {winner_name}"
