from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from trading.accounting import (
    compute_income_expenses,
    normalize_period,
    summarize_portfolio_entries,
)
from trading.models import Portfolio

from .ledger import build_dashboard_ledger


@login_required
def dashboard(request):
    account = request.user
    ledger = build_dashboard_ledger(account)

    portfolio_entries = Portfolio.objects.filter(
        owner=account
    ).select_related('creature')
    portfolio_summary = summarize_portfolio_entries(portfolio_entries)
    period = normalize_period(request.GET.get('period', 'monthly'))
    ie_data = compute_income_expenses(account, period)

    context = {
        'ledger': ledger,
        'transactions': ledger,
        'portfolio_entries': portfolio_entries,
        'period': period,
    }
    context.update(portfolio_summary)
    context.update(ie_data)
    return render(request, 'dashboard/dashboard.html', context)
