import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from account.money import cents_to_dollars
from creature.models import Creature
from .accounting import (
    compute_income_expenses,
    normalize_period,
    summarize_portfolio_entries,
)
from .forms import MarketOrderForm, LimitOrderForm
from .models import (
    MarketIndex,
    Notification,
    Order,
    Portfolio,
    PriceHistory,
    Trade,
)
from .services import TradingEngine, MarketIndicesService
from creature.services import TrainingService, BattleService


@login_required
def market_view(request):
    """
    Main market page: list of all creatures with prices, change %, type.
    """
    creatures = Creature.objects.all().order_by('name')

    # Calculate change percentage for each creature
    creature_data = []
    for c in creatures:
        change, change_pct = c.price_change()
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
    change, change_pct = creature.price_change()

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

    # Top battles sorted by absolute valuation impact
    top_battles = BattleService.get_top_battles(creature, limit=5)

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
        'top_battles': top_battles,
    })


def _market_execution_message(action, quantity, creature, order):
    summary = order.execution_summary()
    return (
        f"{action} {quantity} {creature.name} "
        f"@ ${summary['average_price']}. "
        f"Commission: ${summary['commission']}."
    )


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
                        _market_execution_message(
                            'Bought',
                            quantity,
                            creature,
                            trade.buy_order,
                        )
                    )
                else:
                    trade = TradingEngine.execute_market_sell(
                        account=request.user,
                        creature=creature,
                        quantity=quantity,
                    )
                    messages.success(
                        request,
                        _market_execution_message(
                            'Sold',
                            quantity,
                            creature,
                            trade.sell_order,
                        )
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


def _json_financial_values(value):
    """Convert Decimal values only at the existing JSON presentation edge."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_json_financial_values(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _json_financial_values(item)
            for key, item in value.items()
        }
    return value


def _realized_summary(user, portfolio_summary):
    """Summarize snapshotted realized results and combined return."""
    sell_trades = Trade.objects.filter(
        seller=user,
        realized_cost_basis__isnull=False,
    )
    realized_pnl = Decimal('0')
    realized_cost = Decimal('0')
    for trade in sell_trades:
        realized_pnl += (
            trade.total_amount
            - trade.commission
            - trade.realized_cost_basis
        )
        realized_cost += trade.realized_cost_basis

    total_return = portfolio_summary['total_pnl'] + realized_pnl
    total_invested = portfolio_summary['total_cost'] + realized_cost
    return {
        'realized_pnl': realized_pnl,
        'realized_pnl_pct': (
            (realized_pnl / realized_cost * 100).quantize(Decimal('0.01'))
            if realized_cost > 0 else Decimal('0')
        ),
        'total_return': total_return,
        'total_return_pct': (
            (total_return / total_invested * 100).quantize(Decimal('0.01'))
            if total_invested > 0 else Decimal('0')
        ),
    }


@login_required
def portfolio_view(request):
    """
    User's portfolio: all holdings with current value, P&L, and distribution.
    """
    entries = Portfolio.objects.filter(
        owner=request.user
    ).select_related('creature')
    portfolio_summary = summarize_portfolio_entries(entries)
    realized_summary = _realized_summary(request.user, portfolio_summary)

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

    period = normalize_period(request.GET.get('period', 'monthly'))
    ie_data = compute_income_expenses(request.user, period)

    context = {
        'entry_list': entry_list,
        'entries': entries,
        'period': period,
        'income_data_json': json.dumps(
            _json_financial_values(ie_data['income_data'])
        ),
        'expenses_data_json': json.dumps(
            _json_financial_values(ie_data['expenses_data'])
        ),
        'net_data_json': json.dumps(
            _json_financial_values(ie_data['net_data'])
        ),
    }
    context.update(portfolio_summary)
    context.update(realized_summary)
    context.update(ie_data)
    return render(request, 'trading/portfolio.html', context)


@login_required
def order_history_view(request):
    """
    User's order history with status filtering.
    """
    status_filter = request.GET.get('status', '')
    orders = Order.objects.filter(
        account=request.user
    ).select_related('creature')

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

    cache_key = f"price_history:{creature.pk}:{interval}"
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return JsonResponse(cached_payload)

    limits = {
        PriceHistory.Interval.ONE_HOUR: 168,
        PriceHistory.Interval.ONE_DAY: 365,
        PriceHistory.Interval.ONE_WEEK: 52,
    }
    history = list(
        PriceHistory.objects.filter(
            creature=creature,
            interval=interval,
        ).order_by('-timestamp')[:limits[interval]]
    )
    history.reverse()

    data = [{
        'timestamp': entry.timestamp.isoformat(),
        'open': float(entry.open_price),
        'high': float(entry.high_price),
        'low': float(entry.low_price),
        'close': float(entry.close_price),
        'volume': float(entry.volume),
    } for entry in history]

    payload = {
        'creature': creature.name,
        'interval': interval,
        'data': data,
    }
    cache.set(cache_key, payload, timeout=60)

    return JsonResponse(payload)


@login_required
def market_indices_view(request):
    """
    Show latest market indices for all types.
    """
    latest_indices = MarketIndicesService.get_latest_indices()
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
        latest = indices.order_by('-timestamp')[:50]
    else:
        latest = indices.order_by(
            'creature_type',
            '-timestamp',
        ).distinct('creature_type')

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


@login_required
def notifications_view(request):
    """Show user notifications with mark-as-read."""
    if request.method == 'POST':
        notification_id = request.POST.get('notification_id')
        if notification_id:
            Notification.objects.filter(
                pk=notification_id, user=request.user
            ).update(is_read=True)
        return redirect('trading:notifications')

    unread_count = Notification.objects.filter(
        user=request.user, is_read=False
    ).count()

    notifications = Notification.objects.filter(
        user=request.user
    ).select_related('related_creature')[:50]

    return render(request, 'trading/notifications.html', {
        'notifications': notifications,
        'unread_count': unread_count,
    })


@login_required
def notifications_api(request):
    """JSON API for unread notifications count."""
    count = Notification.objects.filter(
        user=request.user, is_read=False
    ).count()
    return JsonResponse({'unread_count': count})


@login_required
def portfolio_summary_api(request):
    """JSON API: current portfolio summary for auto-refresh."""
    user = request.user
    entries = Portfolio.objects.filter(owner=user).select_related('creature')
    portfolio_summary = summarize_portfolio_entries(entries)
    realized_summary = _realized_summary(user, portfolio_summary)
    total_pnl_pct = portfolio_summary['total_pnl_pct'].quantize(
        Decimal('0.01')
    )
    balance = cents_to_dollars(user.balance_cents)

    period = normalize_period(request.GET.get('period', 'monthly'))
    ie_data = compute_income_expenses(user, period)

    payload = {
        'balance': balance,
        'total_value': portfolio_summary['total_value'],
        'total_cost': portfolio_summary['total_cost'],
        'total_pnl': portfolio_summary['total_pnl'],
        'total_pnl_pct': total_pnl_pct,
        **realized_summary,
        'income_data': ie_data['income_data'],
        'expenses_data': ie_data['expenses_data'],
        'net_data': ie_data['net_data'],
        'total_income': ie_data['total_income'],
        'total_expenses': ie_data['total_expenses'],
        'net_flow': ie_data['net_flow'],
        'period': ie_data['period'],
    }
    return JsonResponse(_json_financial_values(payload))
