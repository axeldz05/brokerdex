from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import EggTemplate, Incubation, Creature
from .services import IncubationService


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
