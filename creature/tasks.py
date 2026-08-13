from celery import shared_task
from django.conf import settings
from redis.exceptions import ConnectionError as RedisConnectionError

from .models import Incubation
from .services import (
    BattleService,
    HatchConfigurationError,
    IncubationService,
    TrainingService,
)


@shared_task
def matchmake_random_battle():
    battle = BattleService.matchmake_random_battle()
    if battle:
        participants = list(
            battle.participants.select_related('creature').all()
        )
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)
        process_battle_turn.apply_async(
            args=[battle.id, battle.current_turn],
            countdown=turn_interval,
        )
        return (
            f"Battle {battle.id} initialized: "
            f"{participants[0].creature.name} vs "
            f"{participants[1].creature.name}"
        )

    return "Not enough creatures to start a battle."


@shared_task(
    autoretry_for=(RedisConnectionError,),
    retry_backoff=True,
    max_retries=3,
)
def process_battle_turn(battle_id, expected_turn=None):
    result = BattleService.process_turn(battle_id, expected_turn)
    battle = result['battle']
    if not result['processed']:
        return (
            f"Battle {battle.id} turn "
            f"{expected_turn or battle.current_turn} was already processed."
        )
    if battle.status == 'active':
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)
        process_battle_turn.apply_async(
            args=[battle.id, battle.current_turn],
            countdown=turn_interval,
        )
        return (
            f"Turn {result.get('turn_number', battle.current_turn - 1)} "
            f"processed. Next turn in {turn_interval}s."
        )

    winner_name = (
        battle.winner.creature.name if battle.winner else "Draw"
    )
    return f"Battle {battle.id} has finished! Winner: {winner_name}"


@shared_task
def hatch_egg(incubation_id):
    """
    Celery task: hatch an egg when the timer expires.
    Called asynchronously via ETA scheduling.
    """
    try:
        incubation = IncubationService.hatch_egg(incubation_id)
    except HatchConfigurationError as error:
        incubation, refunded = IncubationService.fail_hatch(
            incubation_id,
            error,
        )
        outcome = 'refunded' if refunded else 'already resolved'
        return f"Egg {incubation_id} failed permanently and was {outcome}."

    if incubation.status != Incubation.Status.HATCHED:
        return f"Egg {incubation_id} was already resolved."
    return (
        f"Egg {incubation.egg_template.name} hatched! "
        f"Got {incubation.hatched_creature.name}"
    )


@shared_task(bind=True)
def complete_training(self, session_id, portfolio_id=None):
    """
    Complete current sessions and pre-deploy two-argument training messages.
    """
    if portfolio_id is None:
        session = TrainingService.complete_training(session_id)
    else:
        session = TrainingService.complete_legacy_training(
            creature_id=session_id,
            portfolio_id=portfolio_id,
            task_id=self.request.id,
        )
    return (
        f"{session.creature.name} trained: "
        f"{session.result_stat} +{session.result_boost}."
    )


@shared_task
def recover_creature_operations():
    def schedule_turn(battle_id, current_turn):
        process_battle_turn.apply_async(
            args=[battle_id, current_turn],
            countdown=0,
        )

    return {
        'incubations': IncubationService.recover_scheduling(),
        'trainings': TrainingService.recover_scheduling(),
        'battles': BattleService.recover_battles(
            schedule_turn=schedule_turn,
        ),
    }
