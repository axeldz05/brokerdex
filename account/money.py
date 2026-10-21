from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal('0.01')
QUANTITY_QUANTUM = Decimal('0.00000001')


def quantize_money(amount):
    """Round a monetary amount to persisted two-decimal precision."""
    return Decimal(str(amount)).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def quantize_quantity(quantity):
    """Round an asset quantity to persisted eight-decimal precision."""
    return Decimal(str(quantity)).quantize(
        QUANTITY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def dollars_to_cents(amount):
    """Convert a dollar Decimal into authoritative integer cents."""
    return int(quantize_money(amount) * 100)


def cents_to_dollars(amount_cents):
    """Convert authoritative integer cents into a display Decimal."""
    return quantize_money(Decimal(amount_cents) / Decimal('100'))
