import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from creature.models import Creature
from banking.models import Transfer
from .forms import MarketOrderForm, LimitOrderForm
from .models import Order, Trade, Portfolio, PriceHistory, MarketIndex, Notification
from .services import TradingEngine
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

    # Top battles for this creature: sorted by absolute valuation change
    battle_history = BattleService.get_creature_battle_history(creature)
    top_battles = []

    for battle in battle_history:
        if battle.status != 'finished':
            continue
        ps = list(battle.participants.all())
        if len(ps) != 2:
            continue

        is_p1 = (ps[0].creature_id == creature.id)
        p = ps[0] if is_p1 else ps[1]
        opp = ps[1] if is_p1 else ps[0]

        win_pct = battle.creature_1_potential_change if is_p1 else battle.creature_2_potential_change
        lose_pct = battle.creature_2_potential_change if is_p1 else battle.creature_1_potential_change
        win_pct = win_pct or Decimal('0')
        lose_pct = lose_pct or Decimal('0')

        won = bool(battle.winner and battle.winner.pk == p.pk)
        change_pct = win_pct if won else -lose_pct
        change_abs = (creature.current_price * change_pct / 100).quantize(Decimal('0.01'))

        top_battles.append({
            'battle': battle,
            'opponent': opp.creature.name,
            'change_pct': change_pct,
            'change_abs': change_abs,
            'won': won,
        })

    top_battles.sort(key=lambda x: abs(x['change_pct']), reverse=True)
    top_battles = top_battles[:5]

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


def _compute_income_expenses(user, period='monthly'):
    """Returns income, expenses, and net data grouped by month or year."""
    user_trades = Trade.objects.filter(
        Q(buyer=user) | Q(seller=user)
    ).select_related('creature')

    user_transfers = Transfer.objects.filter(
        Q(sender=user) | Q(receiver=user),
        status=Transfer.TransactionStatus.COMPLETED,
    )

    if period == 'monthly':
        def key_fn(dt):
            return dt.strftime('%Y-%m')
        def label_fn(key):
            return datetime.strptime(key, '%Y-%m').strftime('%b %Y')
    else:
        def key_fn(dt):
            return str(dt.year)
        def label_fn(key):
            return key

    income_monthly = defaultdict(lambda: {'sales': Decimal('0'), 'deposits': Decimal('0'), 'transfers_in': Decimal('0')})
    expenses_monthly = defaultdict(lambda: {'purchases': Decimal('0'), 'withdrawals': Decimal('0'), 'transfers_out': Decimal('0'), 'commissions': Decimal('0')})

    for t in user_trades:
        dt = t.executed_at
        if tz := getattr(dt, 'tzinfo', None):
            dt = dt.astimezone(datetime.now().astimezone().tzinfo)
        dt_naive = dt.replace(tzinfo=None) if getattr(dt, 'tzinfo', None) else dt
        k = key_fn(dt_naive)
        if t.buyer == user:
            expenses_monthly[k]['purchases'] += t.total_amount + t.commission
            expenses_monthly[k]['commissions'] += t.commission
        if t.seller == user:
            income_monthly[k]['sales'] += t.total_amount - t.commission
            expenses_monthly[k]['commissions'] += t.commission

    for tr in user_transfers:
        dt = tr.date
        if tz := getattr(dt, 'tzinfo', None):
            dt = dt.astimezone(datetime.now().astimezone().tzinfo)
        dt_naive = dt.replace(tzinfo=None) if getattr(dt, 'tzinfo', None) else dt
        k = key_fn(dt_naive)
        amount = Decimal(tr.amount) / Decimal('100')
        if tr.type == Transfer.TransactionType.DEPOSIT and tr.receiver == user:
            income_monthly[k]['deposits'] += amount
        elif tr.type == Transfer.TransactionType.WITHDRAW and tr.sender == user:
            expenses_monthly[k]['withdrawals'] += amount
        elif tr.type == Transfer.TransactionType.TRANSFER:
            if tr.receiver == user:
                income_monthly[k]['transfers_in'] += amount
            elif tr.sender == user:
                expenses_monthly[k]['transfers_out'] += amount

    all_keys = sorted(set(list(income_monthly.keys()) + list(expenses_monthly.keys())))
    if period == 'monthly':
        all_keys = all_keys[-12:]

    income_data = []
    expenses_data = []
    net_data = []

    for k in all_keys:
        inc = income_monthly[k]
        exp = expenses_monthly[k]
        total_income = inc['sales'] + inc['deposits'] + inc['transfers_in']
        total_expenses = exp['purchases'] + exp['withdrawals'] + exp['transfers_out'] + exp['commissions']
        income_data.append({
            'period': label_fn(k),
            'sales': float(inc['sales']),
            'deposits': float(inc['deposits']),
            'transfers_in': float(inc['transfers_in']),
            'total': float(total_income),
        })
        expenses_data.append({
            'period': label_fn(k),
            'purchases': float(exp['purchases']),
            'withdrawals': float(exp['withdrawals']),
            'transfers_out': float(exp['transfers_out']),
            'commissions': float(exp['commissions']),
            'total': float(total_expenses),
        })
        net_data.append({
            'period': label_fn(k),
            'net': float(total_income - total_expenses),
        })

    total_income_sum = sum(d['total'] for d in income_data)
    total_expenses_sum = sum(d['total'] for d in expenses_data)

    return {
        'income_data': income_data,
        'expenses_data': expenses_data,
        'net_data': net_data,
        'total_income': total_income_sum,
        'total_expenses': total_expenses_sum,
        'net_flow': total_income_sum - total_expenses_sum,
        'period': period,
    }


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

    type_distribution = {}
    for entry in entries:
        t = entry.creature.type
        type_distribution[t] = type_distribution.get(t, Decimal('0')) + entry.current_value

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

    sell_trades = Trade.objects.filter(
        seller=request.user
    ).select_related('creature')
    realized_pnl = Decimal('0')
    for t in sell_trades:
        realized_pnl += t.total_amount - t.commission

    portfolio_cost = sum(p.average_cost * p.quantity for p in entries)
    realized_pnl_total = realized_pnl - portfolio_cost

    total_invested = total_cost
    total_return = total_pnl + realized_pnl_total
    total_return_pct = (
        (total_return / total_invested * 100).quantize(Decimal('0.01'))
        if total_invested > 0 else Decimal('0')
    )

    period = request.GET.get('period', 'monthly')
    if period not in ('monthly', 'yearly'):
        period = 'monthly'
    ie_data = _compute_income_expenses(request.user, period)

    context = {
        'entry_list': entry_list,
        'entries': entries,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'realized_pnl': realized_pnl_total,
        'realized_pnl_pct': (realized_pnl_total / total_invested * 100).quantize(Decimal('0.01')) if total_invested > 0 else Decimal('0'),
        'total_return': total_return,
        'total_return_pct': total_return_pct,
        'type_distribution': type_distribution,
        'period': period,
        'income_data_json': json.dumps(ie_data['income_data']),
        'expenses_data_json': json.dumps(ie_data['expenses_data']),
        'net_data_json': json.dumps(ie_data['net_data']),
    }
    context.update(ie_data)
    return render(request, 'trading/portfolio.html', context)


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
    total_value = float(sum(e.current_value for e in entries))
    total_cost = float(sum(e.cost_basis for e in entries))
    total_pnl = total_value - total_cost
    total_pnl_pct = round((total_pnl / total_cost * 100) if total_cost > 0 else 0, 2)
    balance = float(user.balance_dollars)

    period = request.GET.get('period', 'monthly')
    if period not in ('monthly', 'yearly'):
        period = 'monthly'
    ie_data = _compute_income_expenses(user, period)

    return JsonResponse({
        'balance': balance,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'income_data': ie_data['income_data'],
        'expenses_data': ie_data['expenses_data'],
        'net_data': ie_data['net_data'],
        'total_income': ie_data['total_income'],
        'total_expenses': ie_data['total_expenses'],
        'net_flow': ie_data['net_flow'],
        'period': ie_data['period'],
    })
