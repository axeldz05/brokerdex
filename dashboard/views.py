from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from trading.models import Portfolio

@login_required
def dashboard(request):
    account = request.user
    transactions = account.get_transaction_history()
    
    # Get portfolio entries to render the investment chart and details
    portfolio_entries = Portfolio.objects.filter(owner=account).select_related('creature')
    
    total_value = sum(e.current_value for e in portfolio_entries)
    total_cost = sum(e.cost_basis for e in portfolio_entries)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else Decimal('0')
    
    # Distribution by type for the dashboard investment chart
    type_distribution = {}
    for entry in portfolio_entries:
        t = entry.creature.type
        type_distribution[t] = type_distribution.get(t, Decimal('0')) + entry.current_value

    return render(request, 'dashboard/dashboard.html', {
        'transactions': transactions,
        'portfolio_entries': portfolio_entries,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'type_distribution': type_distribution,
    })
