from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from trading.models import Portfolio
from trading.views import _compute_income_expenses

@login_required
def dashboard(request):
    account = request.user
    transactions = account.get_transaction_history()

    portfolio_entries = Portfolio.objects.filter(owner=account).select_related('creature')

    total_value = sum(e.current_value for e in portfolio_entries)
    total_cost = sum(e.cost_basis for e in portfolio_entries)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else Decimal('0')

    type_distribution = {}
    for entry in portfolio_entries:
        t = entry.creature.type
        type_distribution[t] = type_distribution.get(t, Decimal('0')) + entry.current_value

    period = request.GET.get('period', 'monthly')
    if period not in ('monthly', 'yearly'):
        period = 'monthly'
    ie_data = _compute_income_expenses(account, period)

    context = {
        'transactions': transactions,
        'portfolio_entries': portfolio_entries,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'type_distribution': type_distribution,
        'period': period,
    }
    context.update(ie_data)
    return render(request, 'dashboard/dashboard.html', context)
