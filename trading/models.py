import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from account.models import Account
from creature.models import Creature


class Portfolio(models.Model):
    """
    Tracks a user's holdings of a specific creature.
    3NF: All non-key attributes (quantity, average_cost, acquired_at) depend
    solely on the composite key (owner, creature). No transitive dependencies.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='portfolio_entries'
    )
    creature = models.ForeignKey(
        Creature,
        on_delete=models.CASCADE,
        related_name='holders'
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))]
    )
    average_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        help_text="Weighted average purchase price per unit"
    )
    acquired_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['owner', 'creature']
        verbose_name_plural = 'portfolios'
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.owner.username} — {self.creature.name} ×{self.quantity}"

    @property
    def current_value(self):
        """Derived: quantity × creature.current_price. Not stored (3NF)."""
        return self.quantity * self.creature.current_price

    @property
    def cost_basis(self):
        """Derived: quantity × average_cost. Not stored (3NF)."""
        return self.quantity * self.average_cost

    @property
    def unrealized_pnl(self):
        """Derived: current_value - cost_basis. Not stored (3NF)."""
        return self.current_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self):
        """Derived percentage P&L. Not stored (3NF)."""
        if self.cost_basis == 0:
            return Decimal('0')
        return (self.unrealized_pnl / self.cost_basis) * 100


class Order(models.Model):
    """
    Represents a buy or sell order in the order book.
    3NF: filled_quantity tracks actual execution state (independent fact).
    limit_price is only relevant for LIMIT orders (nullable).
    No derived/transitive fields stored.
    """

    class OrderType(models.TextChoices):
        BUY = 'BUY', 'Buy'
        SELL = 'SELL', 'Sell'

    class ExecutionType(models.TextChoices):
        MARKET = 'MARKET', 'Market Order'
        LIMIT = 'LIMIT', 'Limit Order'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        FILLED = 'FILLED', 'Filled'
        PARTIALLY_FILLED = 'PARTIALLY_FILLED', 'Partially Filled'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    creature = models.ForeignKey(
        Creature,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    order_type = models.CharField(max_length=4, choices=OrderType.choices)
    execution_type = models.CharField(max_length=6, choices=ExecutionType.choices)
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        validators=[MinValueValidator(Decimal('0.00000001'))]
    )
    limit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Required for LIMIT orders only"
    )
    filled_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal('0')
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['creature', 'status', 'order_type']),
            models.Index(fields=['account', 'status']),
        ]

    def clean(self):
        if self.execution_type == self.ExecutionType.LIMIT and self.limit_price is None:
            raise ValidationError("Limit orders require a limit_price.")
        if self.execution_type == self.ExecutionType.MARKET and self.limit_price is not None:
            raise ValidationError("Market orders must not have a limit_price.")

    @property
    def remaining_quantity(self):
        """Derived: quantity - filled_quantity. Not stored (3NF)."""
        return self.quantity - self.filled_quantity

    @property
    def is_fully_filled(self):
        return self.filled_quantity >= self.quantity

    def __str__(self):
        return (
            f"{self.get_order_type_display()} {self.get_execution_type_display()} "
            f"— {self.creature.name} ×{self.quantity} [{self.status}]"
        )


class Trade(models.Model):
    """
    Immutable record of an executed transaction.
    3NF compliance:
    - total_amount is NOT stored; it's derived as quantity × price_per_unit.
    - commission is stored because the commission RATE can change over time,
      so the actual charged amount at execution time is an independent fact.
    - seller is nullable to represent Market Maker (liquidity pool) trades.
    """

    COMMISSION_RATE = Decimal('0.015')  # 1.5%

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        related_name='trades_as_buyer'
    )
    seller = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trades_as_seller',
        help_text="NULL when the Market Maker (liquidity pool) is the counterparty"
    )
    creature = models.ForeignKey(
        Creature,
        on_delete=models.CASCADE,
        related_name='trades'
    )
    buy_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        related_name='trades_as_buy'
    )
    sell_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trades_as_sell'
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=8)
    price_per_unit = models.DecimalField(max_digits=12, decimal_places=2)
    commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Actual commission charged at execution time"
    )
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-executed_at']
        indexes = [
            models.Index(fields=['creature', 'executed_at']),
            models.Index(fields=['buyer', 'executed_at']),
        ]

    @property
    def total_amount(self):
        """Derived: quantity × price_per_unit. Not stored (3NF)."""
        return self.quantity * self.price_per_unit

    def __str__(self):
        return (
            f"Trade: {self.creature.name} ×{self.quantity} "
            f"@ ${self.price_per_unit} [{self.executed_at}]"
        )


class PriceHistory(models.Model):
    """
    OHLCV (Open/High/Low/Close/Volume) candlestick data for charting.
    3NF: All fields are independent measurements recorded at a specific
    timestamp+interval. No field is derivable from another.
    """

    class Interval(models.TextChoices):
        ONE_HOUR = '1H', '1 Hour'
        ONE_DAY = '1D', '1 Day'
        ONE_WEEK = '1W', '1 Week'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creature = models.ForeignKey(
        Creature,
        on_delete=models.CASCADE,
        related_name='price_history'
    )
    open_price = models.DecimalField(max_digits=12, decimal_places=2)
    high_price = models.DecimalField(max_digits=12, decimal_places=2)
    low_price = models.DecimalField(max_digits=12, decimal_places=2)
    close_price = models.DecimalField(max_digits=12, decimal_places=2)
    volume = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal('0'),
        help_text="Total units traded in this interval"
    )
    interval = models.CharField(max_length=2, choices=Interval.choices)
    timestamp = models.DateTimeField()

    class Meta:
        unique_together = ['creature', 'interval', 'timestamp']
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['creature', 'interval', 'timestamp']),
        ]
        verbose_name_plural = 'price histories'

    def __str__(self):
        return (
            f"{self.creature.name} [{self.interval}] "
            f"{self.timestamp:%Y-%m-%d %H:%M} "
            f"O:{self.open_price} H:{self.high_price} "
            f"L:{self.low_price} C:{self.close_price}"
        )


class MarketIndex(models.Model):
    """
    Aggregated market index for a given type (e.g. Water-Type Index).
    Weighted average of all creatures of that type.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creature_type = models.CharField(max_length=15, choices=Creature._meta.get_field('type').choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    previous_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total_volume = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0'),
        help_text="Total trading volume across all creatures in this index",
    )
    creature_count = models.PositiveIntegerField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['creature_type', 'timestamp']),
        ]
        verbose_name_plural = 'market indices'

    def __str__(self):
        return (
            f"{self.get_creature_type_display()}-Type Index: "
            f"{self.value} [{self.timestamp:%Y-%m-%d}]"
        )

    @property
    def change_pct(self):
        if self.previous_value == 0:
            return Decimal('0')
        return ((self.value - self.previous_value) / self.previous_value * 100).quantize(Decimal('0.01'))
