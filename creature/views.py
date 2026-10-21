import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone

from account.money import cents_to_dollars, dollars_to_cents
from .models import EggTemplate, Incubation, Creature, Battle, TrainingFund
from .services import IncubationService, TrainingService, BattleService
from trading.models import Portfolio

logger = logging.getLogger(__name__)


def training_funds_view(request):
    """
    Public dashboard for syndicated flash training funds.
    Lists active OPEN funds with progress, tranches, and contribution forms,
    as well as recently closed and refunded funds.
    """
    TrainingService.expire_funds()

    open_funds_qs = (
        TrainingFund.objects.filter(status=TrainingFund.Status.OPEN)
        .select_related('creature')
        .order_by('expires_at')
    )
    open_funds = []
    now = timezone.now()
    for fund in open_funds_qs:
        fill_pct = 0.0
        if fund.hard_cap_cents > 0:
            fill_pct = min(100.0, round((fund.funded_cents / fund.hard_cap_cents) * 100, 1))

        u = Decimal(fund.funded_cents) / Decimal(fund.hard_cap_cents) if fund.hard_cap_cents > 0 else Decimal('0')
        if u <= Decimal('0.30'):
            tranche_name = 'Semilla'
            tranche_multiplier = '1.40x'
            tranche_badge = 'bg-success'
        elif u <= Decimal('0.75'):
            tranche_name = 'Principal'
            tranche_multiplier = '1.00x'
            tranche_badge = 'bg-primary'
        else:
            tranche_name = 'Cierre'
            tranche_multiplier = '0.75x'
            tranche_badge = 'bg-warning text-dark'

        remaining_seconds = max(0, int((fund.expires_at - now).total_seconds()))
        remaining_minutes = remaining_seconds // 60
        remaining_secs = remaining_seconds % 60

        open_funds.append({
            'fund': fund,
            'fill_pct': fill_pct,
            'funded_dollars': cents_to_dollars(fund.funded_cents),
            'soft_cap_dollars': cents_to_dollars(fund.soft_cap_cents),
            'hard_cap_dollars': cents_to_dollars(fund.hard_cap_cents),
            'remaining_cap_dollars': cents_to_dollars(max(0, fund.hard_cap_cents - fund.funded_cents)),
            'tranche_name': tranche_name,
            'tranche_multiplier': tranche_multiplier,
            'tranche_badge': tranche_badge,
            'remaining_time_str': f"{remaining_minutes}m {remaining_secs}s",
            'is_expired': remaining_seconds <= 0,
        })

    closed_funds_qs = (
        TrainingFund.objects.filter(
            status__in=[TrainingFund.Status.COMPLETED, TrainingFund.Status.EXPIRED_REFUNDED]
        )
        .select_related('creature')
        .order_by('-closed_at', '-expires_at')[:15]
    )
    closed_funds = []
    for fund in closed_funds_qs:
        closed_funds.append({
            'fund': fund,
            'funded_dollars': cents_to_dollars(fund.funded_cents),
            'soft_cap_dollars': cents_to_dollars(fund.soft_cap_cents),
            'hard_cap_dollars': cents_to_dollars(fund.hard_cap_cents),
        })

    user_balance_dollars = None
    if request.user.is_authenticated:
        user_balance_dollars = cents_to_dollars(request.user.balance_cents)

    return render(request, 'creature/training_funds.html', {
        'open_funds': open_funds,
        'closed_funds': closed_funds,
        'user_balance_dollars': user_balance_dollars,
    })


@login_required
def contribute_fund_view(request, fund_id):
    """
    Handle user contributions to an active syndicated flash training fund.
    """
    if request.method != 'POST':
        return redirect('creature:training_funds')

    fund = get_object_or_404(TrainingFund, pk=fund_id)

    raw_amount = request.POST.get('amount', '').strip()
    try:
        amount_dollars = Decimal(raw_amount)
        if amount_dollars <= Decimal('0'):
            messages.error(request, "El monto a aportar debe ser mayor a 0.")
            return redirect('creature:training_funds')
        amount_cents = dollars_to_cents(amount_dollars)
    except Exception:
        messages.error(request, "Monto inválido ingresado.")
        return redirect('creature:training_funds')

    if request.user.balance_cents < amount_cents:
        messages.error(
            request,
            f"Saldo insuficiente. Tu saldo es ${cents_to_dollars(request.user.balance_cents):.2f}."
        )
        return redirect('creature:training_funds')

    try:
        contribution = TrainingService.contribute_to_fund(
            request.user, fund.pk, amount_cents
        )
        messages.success(
            request,
            f"¡Aportación exitosa de ${amount_dollars:.2f} a {fund.creature.name}! "
            f"Multiplicador asignado: {contribution.tranche_multiplier}x."
        )
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, 'message') else e))
    except Exception as e:
        logger.exception("Failed to contribute to training fund")
        messages.error(request, f"Error al procesar la aportación: {e}")

    return redirect('creature:training_funds')



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
        TrainingService.train_creature(
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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Battle not found.'}, status=404)
        messages.error(request, "Battle not found.")
        return redirect('creature:battle_list')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(BattleService.battle_to_dict(battle))

    # Compute actual price changes for finished battles from snapshots
    price_changes = []
    participants_list = list(battle.participants.select_related('creature').all())
    if battle.status == 'finished':
        for p in participants_list:
            if p.price_before is not None and p.price_after is not None and p.price_before > 0:
                abs_val = p.price_after - p.price_before
                pct = (abs_val / p.price_before) * 100
            else:
                abs_val, pct = Decimal('0'), Decimal('0')
            price_changes.append({
                'name': p.creature.name,
                'pct': round(float(pct), 2),
                'abs': round(abs_val, 2),
                'won': bool(battle.winner and battle.winner.pk == p.pk),
            })

    return render(request, 'creature/battle_detail.html', {
        'battle': battle,
        'participants_list': participants_list,
        'actions_list': list(battle.actions.all()),
        'investments_list': list(battle.investments.all()),
        'price_changes': price_changes,
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
