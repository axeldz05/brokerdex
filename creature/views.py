from decimal import Decimal
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import EggTemplate, Incubation, Creature
from .services import IncubationService, TrainingService
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
