import random
from decimal import Decimal
from typing import List

from account.models import Account
from creature.models import Creature, TrainingFund
from creature.services import BattleService, TrainingService
from trading.models import Portfolio
from trading.services import PricingEngine
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

        # Evaluate open TrainingFunds
        open_funds = list(
            TrainingFund.objects.filter(status=TrainingFund.Status.OPEN).select_related('creature')
        )
        if open_funds and users:
            solvent_users = [u for u in users if u.balance_cents >= 500]
            if solvent_users and random.random() < 0.35:
                bot_user = random.choice(solvent_users)
                held_creature_ids = set(
                    Portfolio.objects.filter(owner=bot_user, quantity__gt=0).values_list('creature_id', flat=True)
                )

                def _fund_priority(fund):
                    score = 1.0
                    fill_ratio = (
                        Decimal(fund.funded_cents) / Decimal(fund.hard_cap_cents)
                        if fund.hard_cap_cents > 0
                        else Decimal('1.0')
                    )
                    if fill_ratio <= Decimal('0.30'):
                        score += 2.0  # Priority for seed tranche 1.40x
                    if fund.creature_id in held_creature_ids:
                        score += 1.5  # Priority for owned creatures
                    return score

                sorted_funds = sorted(open_funds, key=_fund_priority, reverse=True)
                target_fund = sorted_funds[0]
                remaining_cap = target_fund.hard_cap_cents - target_fund.funded_cents

                if remaining_cap > 0:
                    max_contrib = min(remaining_cap, int(bot_user.balance_cents * 0.20))
                    contrib_cents = max(100, min(max_contrib, remaining_cap))
                    if bot_user.balance_cents >= contrib_cents:
                        try:
                            contrib = TrainingService.contribute_to_fund(
                                bot_user, target_fund.pk, contrib_cents
                            )
                            c_event = record_simulation_event(
                                'TRAINING_FUND',
                                'CONTRIBUTE',
                                target_fund.creature.name,
                                contrib_cents / 100.0,
                                f"Contributed ${contrib_cents / 100:.2f} ({contrib.tranche_multiplier}x)"
                            )
                            c_event['username'] = bot_user.username
                            events.append(c_event)
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
                        # When a trade occurs, update the spot price and broadcast over WebSockets
                        creature_name = event.get('creature')
                        if creature_name:
                            matched = next((c for c in creatures if c.name == creature_name), None)
                            if matched:
                                try:
                                    PricingEngine.update_creature_price(matched)
                                except Exception:
                                    pass
                        break
                except Exception:
                    continue

        return events
