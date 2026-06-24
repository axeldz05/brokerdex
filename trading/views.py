from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from creature.models import Creature
from .forms import MarketOrderForm, LimitOrderForm
from .models import Order, Trade, Portfolio, PriceHistory, MarketIndex
from .services import TradingEngine
from creature.services import TrainingService


@login_required
def market_view(request):
    """
    Main market page: list of all creatures with prices, change %, type.
    """
    creatures = Creature.objects.all().order_by('name')

    # Calculate change percentage for each creature
    creature_data = []
    for c in creatures:
        if c.previous_close and c.previous_close > 0:
            change = c.current_price - c.previous_close
            change_pct = (change / c.previous_close) * 100
        else:
            change = Decimal('0')
            change_pct = Decimal('0')

        creature_data.append({
            'creature': c,
            'change': change,
            'change_pct': change_pct,
        })

    return render(request, 'trading/market.html', {
        'creature_data': creature_data,
    })


@login_required
def creature_detail_view(request, creature_id):
    """
    Detail page for a creature: price chart, stats, buy/sell forms,
    recent trades, and open orders.
    """
    creature = get_object_or_404(Creature, pk=creature_id)

    # Price change
    if creature.previous_close and creature.previous_close > 0:
        change = creature.current_price - creature.previous_close
        change_pct = (change / creature.previous_close) * 100
    else:
        change = Decimal('0')
        change_pct = Decimal('0')

    # User's holdings of this creature
    portfolio_entry = None
    if request.user.is_authenticated:
        portfolio_entry = Portfolio.objects.filter(
            owner=request.user,
            creature=creature
        ).first()

    # Recent trades for this creature
    recent_trades = Trade.objects.filter(
        creature=creature
    ).select_related('buyer', 'seller')[:20]

    # User's open orders for this creature
    user_orders = Order.objects.filter(
        account=request.user,
        creature=creature,
        status__in=[Order.Status.OPEN, Order.Status.PARTIALLY_FILLED],
    )

    buy_form = MarketOrderForm(initial={
        'creature': creature.pk,
        'order_type': Order.OrderType.BUY,
    })
    sell_form = MarketOrderForm(initial={
        'creature': creature.pk,
        'order_type': Order.OrderType.SELL,
    })
    limit_form = LimitOrderForm(initial={
        'creature': creature.pk,
    })

    return render(request, 'trading/creature_detail.html', {
        'creature': creature,
        'change': change,
        'change_pct': change_pct,
        'portfolio_entry': portfolio_entry,
        'recent_trades': recent_trades,
        'user_orders': user_orders,
        'buy_form': buy_form,
        'sell_form': sell_form,
        'limit_form': limit_form,
    })


@login_required
def place_order_view(request):
    """
    POST endpoint for placing market or limit orders.
    Redirects back to the creature detail page.
    """
    if request.method != 'POST':
        return redirect('trading:market')

    execution_type = request.POST.get('execution_type', 'MARKET')

    if execution_type == 'LIMIT':
        form = LimitOrderForm(request.POST)
        if form.is_valid():
            creature = form.cleaned_data['creature']
            try:
                TradingEngine.place_limit_order(
                    account=request.user,
                    creature=creature,
                    order_type=form.cleaned_data['order_type'],
                    quantity=form.cleaned_data['quantity'],
                    limit_price=form.cleaned_data['limit_price'],
                )
                messages.success(request, "Limit order placed successfully.")
            except ValidationError as e:
                messages.error(request, str(e.message))
            return redirect('trading:creature_detail', creature_id=creature.pk)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            creature_id = request.POST.get('creature', '')
            return redirect('trading:creature_detail', creature_id=creature_id)
    else:
        form = MarketOrderForm(request.POST)
        if form.is_valid():
            creature = form.cleaned_data['creature']
            order_type = form.cleaned_data['order_type']
            quantity = form.cleaned_data['quantity']
            try:
                if order_type == Order.OrderType.BUY:
                    trade = TradingEngine.execute_market_buy(
                        account=request.user,
                        creature=creature,
                        quantity=quantity,
                    )
                    messages.success(
                        request,
                        f"Bought {quantity} {creature.name} @ ${trade.price_per_unit}. "
                        f"Commission: ${trade.commission}."
                    )
                else:
                    trade = TradingEngine.execute_market_sell(
                        account=request.user,
                        creature=creature,
                        quantity=quantity,
                    )
                    messages.success(
                        request,
                        f"Sold {quantity} {creature.name} @ ${trade.price_per_unit}. "
                        f"Commission: ${trade.commission}."
                    )
            except ValidationError as e:
                messages.error(request, str(e.message))
            return redirect('trading:creature_detail', creature_id=creature.pk)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            creature_id = request.POST.get('creature', '')
            return redirect('trading:creature_detail', creature_id=creature_id)


@login_required
def portfolio_view(request):
    """
    User's portfolio: all holdings with current value, P&L, and distribution.
    """
    entries = Portfolio.objects.filter(
        owner=request.user
    ).select_related('creature')

    total_value = sum(e.current_value for e in entries)
    total_cost = sum(e.cost_basis for e in entries)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else Decimal('0')

    # Distribution by type for chart
    type_distribution = {}
    for entry in entries:
        t = entry.creature.type
        type_distribution[t] = type_distribution.get(t, Decimal('0')) + entry.current_value

    # Annotate entries with training cost
    entry_list = []
    for entry in entries:
        entry_list.append({
            'pk': entry.pk,
            'creature': entry.creature,
            'quantity': entry.quantity,
            'average_cost': entry.average_cost,
            'current_value': entry.current_value,
            'cost_basis': entry.cost_basis,
            'unrealized_pnl': entry.unrealized_pnl,
            'unrealized_pnl_pct': entry.unrealized_pnl_pct,
            'training_cost': TrainingService.get_training_cost(entry.creature),
        })

    return render(request, 'trading/portfolio.html', {
        'entry_list': entry_list,
        'entries': entries,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'type_distribution': type_distribution,
    })


@login_required
def order_history_view(request):
    """
    User's order history with status filtering.
    """
    status_filter = request.GET.get('status', '')
    orders = Order.objects.filter(account=request.user).select_related('creature')

    if status_filter:
        orders = orders.filter(status=status_filter)

    return render(request, 'trading/orders.html', {
        'orders': orders,
        'current_filter': status_filter,
        'status_choices': Order.Status.choices,
    })


@login_required
def cancel_order_view(request, order_id):
    """Cancel an open limit order."""
    if request.method != 'POST':
        return redirect('trading:orders')

    order = get_object_or_404(Order, pk=order_id, account=request.user)
    try:
        TradingEngine.cancel_order(order)
        messages.success(request, "Order cancelled successfully.")
    except ValidationError as e:
        messages.error(request, str(e.message))

    return redirect('trading:orders')


@login_required
def price_history_api(request, creature_id):
    """
    JSON API endpoint for price history data (consumed by Chart.js).
    Returns OHLCV data for a given creature and interval.
    """
    creature = get_object_or_404(Creature, pk=creature_id)
    interval = request.GET.get('interval', '1H')

    if interval not in dict(PriceHistory.Interval.choices):
        interval = '1H'

    history = PriceHistory.objects.filter(
        creature=creature,
        interval=interval,
    ).order_by('timestamp')[:168]  # max 168 data points (7 days of hourly)

    data = [{
        'timestamp': entry.timestamp.isoformat(),
        'open': float(entry.open_price),
        'high': float(entry.high_price),
        'low': float(entry.low_price),
        'close': float(entry.close_price),
        'volume': float(entry.volume),
    } for entry in history]

    return JsonResponse({
        'creature': creature.name,
        'interval': interval,
        'data': data,
    })


@login_required
def market_indices_view(request):
    """
    Show latest market indices for all types.
    """
    latest_indices = []
    for type_choice in MarketIndex._meta.get_field('creature_type').choices:
        type_value = type_choice[0]
        latest = MarketIndex.objects.filter(
            creature_type=type_value
        ).order_by('-timestamp').first()
        if latest:
            latest_indices.append(latest)

    return render(request, 'trading/market_indices.html', {
        'indices': latest_indices,
    })


@login_required
def market_indices_api(request):
    """
    JSON API for market indices data.
    """
    type_filter = request.GET.get('type', '')
    indices = MarketIndex.objects.all()

    if type_filter:
        indices = indices.filter(creature_type=type_filter)

    latest = indices.order_by('-timestamp').select_related()[:50]

    data = [{
        'type': idx.creature_type,
        'value': float(idx.value),
        'previous_value': float(idx.previous_value),
        'change_pct': float(idx.change_pct),
        'creature_count': idx.creature_count,
        'total_volume': float(idx.total_volume),
        'timestamp': idx.timestamp.isoformat(),
    } for idx in latest]

    return JsonResponse({'indices': data})
