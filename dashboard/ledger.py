from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Q

from account.money import cents_to_dollars
from banking.models import Transfer
from trading.models import Trade


@dataclass(frozen=True)
class LedgerEntry:
    occurred_at: datetime
    label: str
    amount: Decimal

    @property
    def absolute_amount(self):
        return abs(self.amount)

    @property
    def is_credit(self):
        return self.amount >= Decimal('0')


def _account_label(account):
    return str(account) if account is not None else 'unknown account'


def _transfer_entry(transfer, user):
    amount = cents_to_dollars(transfer.amount)
    if (
        transfer.type == Transfer.TransactionType.DEPOSIT
        and transfer.receiver_id == user.pk
    ):
        return LedgerEntry(transfer.date, 'Deposit', amount)
    if (
        transfer.type == Transfer.TransactionType.WITHDRAW
        and transfer.sender_id == user.pk
    ):
        return LedgerEntry(transfer.date, 'Withdrawal', -amount)
    if transfer.type != Transfer.TransactionType.TRANSFER:
        return None
    if transfer.receiver_id == user.pk:
        sender = _account_label(transfer.sender)
        return LedgerEntry(
            transfer.date,
            f'Received from {sender}',
            amount,
        )
    if transfer.sender_id == user.pk:
        receiver = _account_label(transfer.receiver)
        return LedgerEntry(
            transfer.date,
            f'Sent to {receiver}',
            -amount,
        )
    return None


def _trade_entries(trade, user):
    subtotal = trade.total_amount
    creature = trade.creature.name
    if trade.buyer_id == user.pk:
        if trade.seller_id is None:
            label = f'Market buy: {creature}'
        else:
            seller = _account_label(trade.seller)
            label = f'P2P buy: {creature} from {seller}'
        yield LedgerEntry(
            trade.executed_at,
            label,
            -(subtotal + trade.commission),
        )
    if trade.seller_id == user.pk:
        if trade.buyer_id is None:
            label = f'Market sell: {creature}'
        else:
            buyer = _account_label(trade.buyer)
            label = f'P2P sell: {creature} to {buyer}'
        yield LedgerEntry(
            trade.executed_at,
            label,
            subtotal - trade.commission,
        )


def build_dashboard_ledger(user):
    """Build signed presentation entries from persisted cash-flow facts."""
    transfers = Transfer.objects.filter(
        Q(sender=user) | Q(receiver=user),
        status=Transfer.TransactionStatus.COMPLETED,
    ).select_related('sender', 'receiver')
    trades = Trade.objects.filter(
        Q(buyer=user) | Q(seller=user)
    ).select_related('buyer', 'seller', 'creature')

    entries = [
        entry
        for transfer in transfers
        if (entry := _transfer_entry(transfer, user)) is not None
    ]
    for trade in trades:
        entries.extend(_trade_entries(trade, user))
    return sorted(
        entries,
        key=lambda entry: entry.occurred_at,
        reverse=True,
    )
