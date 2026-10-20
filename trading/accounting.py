from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from account.money import cents_to_dollars
from banking.models import Transfer
from creature.models import Incubation, TrainingSession

from .models import Trade


def normalize_period(period):
    return period if period in ('monthly', 'yearly') else 'monthly'


def summarize_portfolio_entries(entries):
    """Aggregate portfolio value without taking ownership of its QuerySet."""
    total_value = Decimal('0')
    total_cost = Decimal('0')
    type_distribution = {}
    for entry in entries:
        total_value += entry.current_value
        total_cost += entry.cost_basis
        creature_type = entry.creature.type
        type_distribution[creature_type] = (
            type_distribution.get(creature_type, Decimal('0'))
            + entry.current_value
        )

    total_pnl = total_value - total_cost
    total_pnl_pct = (
        total_pnl / total_cost * 100
        if total_cost > 0
        else Decimal('0')
    )
    return {
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'type_distribution': type_distribution,
    }


def _local_naive(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
        return value.replace(tzinfo=None)
    return value


def _period_formatters(period):
    if period == 'monthly':
        return (
            lambda value: value.strftime('%Y-%m'),
            lambda key: datetime.strptime(key, '%Y-%m').strftime('%b %Y'),
        )
    return lambda value: str(value.year), lambda key: key


def compute_income_expenses(user, period='monthly'):
    """Return grouped cash flow while retaining Decimal domain values."""
    period = normalize_period(period)
    key_for, label_for = _period_formatters(period)
    income_by_period = defaultdict(
        lambda: {
            'sales': Decimal('0'),
            'deposits': Decimal('0'),
            'transfers_in': Decimal('0'),
            'refunds': Decimal('0'),
        }
    )
    expenses_by_period = defaultdict(
        lambda: {
            'purchases': Decimal('0'),
            'withdrawals': Decimal('0'),
            'transfers_out': Decimal('0'),
            'commissions': Decimal('0'),
            'incubations': Decimal('0'),
            'training': Decimal('0'),
        }
    )

    trades = Trade.objects.filter(
        Q(buyer=user) | Q(seller=user)
    )
    for trade in trades:
        key = key_for(_local_naive(trade.executed_at))
        if trade.buyer_id == user.pk:
            expenses_by_period[key]['purchases'] += trade.total_amount
            expenses_by_period[key]['commissions'] += trade.commission
        if trade.seller_id == user.pk:
            income_by_period[key]['sales'] += trade.total_amount
            expenses_by_period[key]['commissions'] += trade.commission

    transfers = Transfer.objects.filter(
        Q(sender=user) | Q(receiver=user),
        status=Transfer.TransactionStatus.COMPLETED,
    )
    for transfer in transfers:
        key = key_for(_local_naive(transfer.date))
        amount = cents_to_dollars(transfer.amount)
        if (
            transfer.type == Transfer.TransactionType.DEPOSIT
            and transfer.receiver_id == user.pk
        ):
            income_by_period[key]['deposits'] += amount
        elif (
            transfer.type == Transfer.TransactionType.WITHDRAW
            and transfer.sender_id == user.pk
        ):
            expenses_by_period[key]['withdrawals'] += amount
        elif transfer.type == Transfer.TransactionType.TRANSFER:
            if transfer.receiver_id == user.pk:
                income_by_period[key]['transfers_in'] += amount
            elif transfer.sender_id == user.pk:
                expenses_by_period[key]['transfers_out'] += amount

    incubations = Incubation.objects.filter(
        user=user
    ).select_related('egg_template')
    for inc in incubations:
        key = key_for(_local_naive(inc.purchased_at))
        expenses_by_period[key]['incubations'] += inc.egg_template.price
        if inc.status == Incubation.Status.FAILED and inc.refunded_at:
            refund_key = key_for(_local_naive(inc.refunded_at))
            income_by_period[refund_key]['refunds'] += inc.egg_template.price

    trainings = TrainingSession.objects.filter(user=user)
    for session in trainings:
        cost = cents_to_dollars(session.cost_cents)
        key = key_for(_local_naive(session.created_at))
        expenses_by_period[key]['training'] += cost
        if session.status == TrainingSession.Status.FAILED and session.refunded_at:
            refund_key = key_for(_local_naive(session.refunded_at))
            income_by_period[refund_key]['refunds'] += cost

    keys = sorted(set(income_by_period) | set(expenses_by_period))
    if period == 'monthly':
        keys = keys[-12:]

    income_data = []
    expenses_data = []
    net_data = []
    for key in keys:
        income = income_by_period[key]
        expenses = expenses_by_period[key]
        total_income = sum(income.values(), Decimal('0'))
        total_expenses = sum(expenses.values(), Decimal('0'))
        income_data.append({
            'period': label_for(key),
            **income,
            'total': total_income,
        })
        expenses_data.append({
            'period': label_for(key),
            **expenses,
            'total': total_expenses,
        })
        net_data.append({
            'period': label_for(key),
            'net': total_income - total_expenses,
        })

    total_income = sum(
        (item['total'] for item in income_data),
        Decimal('0'),
    )
    total_expenses = sum(
        (item['total'] for item in expenses_data),
        Decimal('0'),
    )
    return {
        'income_data': income_data,
        'expenses_data': expenses_data,
        'net_data': net_data,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_flow': total_income - total_expenses,
        'period': period,
    }
