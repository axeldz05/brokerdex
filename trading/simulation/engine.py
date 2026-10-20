import random
from typing import List

from account.models import Account
from creature.models import Creature
from creature.services import BattleService
from .state import is_simulation_active, record_simulation_event
from .strategies import (
    MomentumStrategy,
    ValueStrategy,
    MarketMakerStrategy,
    CasualStrategy,
)


class SimulationEngine:
    def __init__(self):
        self.strategies = [
            MomentumStrategy(),
            ValueStrategy(),
            MarketMakerStrategy(),
            CasualStrategy(),
        ]

    def tick(self) -> List[dict]:
        """
        Execute a single simulation cycle.
        Returns a list of event dictionaries describing the actions performed.
        """
        if not is_simulation_active():
            return []

        events = []
        creatures = list(Creature.objects.all())
        if not creatures:
            return []

        # Find eligible bot users
        users = list(
            Account.objects.filter(is_active=True)
            .exclude(username__in=['market_maker'])
            .order_by('?')[:5]
        )
        if not users:
            return []

        # 25% chance: trigger a random matchmaking battle if available
        if random.random() < 0.25:
            try:
                battle = BattleService.matchmake_random_battle()
                if battle:
                    participants = list(battle.participants.select_related('creature').all())
                    c1_name = participants[0].creature.name if len(participants) > 0 else 'Unknown'
                    c2_name = participants[1].creature.name if len(participants) > 1 else 'Unknown'
                    event = record_simulation_event(
                        'BATTLE',
                        'MATCHMAKE',
                        f"{c1_name} vs {c2_name}",
                        1,
                        f"Battle #{battle.pk} started between {c1_name} and {c2_name}"
                    )
                    events.append(event)
            except Exception:
                pass

        # Select 1 to 3 users to perform trading actions
        action_count = min(len(users), random.randint(1, 3))
        for i in range(action_count):
            user = users[i]
            shuffled_strategies = list(self.strategies)
            random.shuffle(shuffled_strategies)
            for strategy in shuffled_strategies:
                try:
                    event = strategy.evaluate_and_execute(user, creatures)
                    if event:
                        event['username'] = user.username
                        events.append(event)
                        break
                except Exception:
                    continue

        return events
