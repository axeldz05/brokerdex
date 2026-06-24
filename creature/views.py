from decimal import Decimal
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse

from .models import EggTemplate, Incubation, Creature, Battle
from .services import IncubationService, TrainingService, BattleService
from trading.models import Portfolio

logger = logging.getLogger(__name__)


@login_required
def incubation_shop_view(request):
    """
    Show available eggs for purchase and user's active incubations.
    """
    eggs = EggTemplate.objects.filter(is_active=True)
    active_incubations = Incubation.objects.filter(
        user=request.user,
        status=Incubation.Status.INCUBATING,
    ).select_related('egg_template')

    return render(request, 'creature/incubation_shop.html', {
        'eggs': eggs,
        'active_incubations': active_incubations,
    })


@login_required
def purchase_egg_view(request, egg_id):
    """
    Purchase an egg and start incubation.
    """
    if request.method != 'POST':
        return redirect('creature:incubation_shop')

    egg = get_object_or_404(EggTemplate, pk=egg_id, is_active=True)

    try:
        incubation = IncubationService.purchase_egg(request.user, egg)
        messages.success(
            request,
            f"Egg purchased! It will hatch in {egg.hatch_duration}. "
            f"Check back later."
        )
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, 'message') else e))
    except Exception as e:
        logger.exception("Failed to purchase egg")
        msg = str(getattr(e, 'message', e))
        if 'Connection refused' in msg or 'connect' in msg.lower():
            messages.error(request, "Background worker is not running. Please contact an administrator.")
        else:
            messages.error(request, f"Failed to start incubation: {msg}")

    return redirect('creature:incubation_shop')


@login_required
def incubation_status_view(request):
    """
    Show all user's incubations (active and hatched).
    """
    incubations = Incubation.objects.filter(
        user=request.user
    ).select_related('egg_template', 'hatched_creature')

    return render(request, 'creature/incubation_status.html', {
        'incubations': incubations,
    })


@login_required
def train_creature_view(request, portfolio_id):
    """
    Initiate training for a creature in the user's portfolio.
    POST only: deducts training fee and schedules async stat boost.
    """
    if request.method != 'POST':
        return redirect('trading:portfolio')

    portfolio = get_object_or_404(
        Portfolio, pk=portfolio_id, owner=request.user
    )
    creature = portfolio.creature
    cost = TrainingService.get_training_cost(creature)

    try:
        task_id = TrainingService.train_creature(
            request.user, creature, portfolio
        )
        messages.success(
            request,
            f"Training initiated for {creature.name}! Cost: ${cost}. "
            f"Stats will boost in ~30 seconds."
        )
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, 'message') else e))
    except Exception as e:
        logger.exception(f"Failed to start training for creature {creature.pk}")
        msg = str(getattr(e, 'message', e))
        if 'Connection refused' in msg or 'connect' in msg.lower():
            messages.error(
                request,
                "Could not connect to the background worker (Redis/Celery). "
                "Make sure Redis and Celery are running."
            )
        else:
            messages.error(request, f"Failed to start training: {msg}")

    return redirect('trading:portfolio')


@login_required
def battle_list_view(request):
    """
    Main battle page: active battles, historical battles,
    user's creature battles, and leaderboard.
    """
    active_battles = BattleService.get_active_battles()
    historical_battles = BattleService.get_historical_battles(limit=20)
    user_battles = BattleService.get_user_creature_battles(request.user)
    leaderboard = BattleService.get_leaderboard(limit=20)

    return render(request, 'creature/battle_list.html', {
        'active_battles': active_battles,
        'historical_battles': historical_battles,
        'user_battles': user_battles,
        'leaderboard': leaderboard,
    })


@login_required
def battle_detail_view(request, battle_id):
    """
    Full turn-by-turn detail of a specific battle.
    Returns JSON if requested, otherwise HTML.
    """
    battle = BattleService.get_battle_detail(battle_id)
    if not battle:
        messages.error(request, "Battle not found.")
        return redirect('creature:battle_list')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _battle_detail_json(battle)

    return render(request, 'creature/battle_detail.html', {
        'battle': battle,
        'participants_list': list(battle.participants.all()),
        'actions_list': list(battle.actions.all()),
        'investments_list': list(battle.investments.all()),
    })


def _battle_detail_json(battle):
    """Return battle detail as JSON for AJAX loading."""
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
            'description': action.description,
            'timestamp': action.timestamp.isoformat(),
        })

    investments_data = {}
    for inv in battle.investments.all():
        key = inv.turn_number
        if key not in investments_data:
            investments_data[key] = []
        investments_data[key].append({
            'creature': inv.creature.name,
            'count': inv.investor_count,
        })

    return JsonResponse({
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
        'investments_per_turn': investments_data,
    })


@login_required
def creature_battle_history_view(request, creature_id):
    """
    Show all battles a specific creature has participated in.
    """
    creature = get_object_or_404(Creature, pk=creature_id)
    battles = BattleService.get_creature_battle_history(creature)

    return render(request, 'creature/creature_battle_history.html', {
        'creature': creature,
        'battles': battles,
    })

    portfolio = get_object_or_404(
        Portfolio, pk=portfolio_id, owner=request.user
    )
    creature = portfolio.creature
    cost = TrainingService.get_training_cost(creature)

    try:
        task_id = TrainingService.train_creature(
            request.user, creature, portfolio
        )
        messages.success(
            request,
            f"Training initiated for {creature.name}! Cost: ${cost}. "
            f"Stats will boost in ~30 seconds."
        )
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, 'message') else e))
    except Exception as e:
        logger.exception(f"Failed to start training for creature {creature.pk}")
        msg = str(getattr(e, 'message', e))
        if 'Connection refused' in msg or 'connect' in msg.lower():
            messages.error(
                request,
                "Could not connect to the background worker (Redis/Celery). "
                "Make sure Redis and Celery are running."
            )
        else:
            messages.error(request, f"Failed to start training: {msg}")

    return redirect('trading:portfolio')
