from django.contrib import admin
from .models import Portfolio, Order, Trade, PriceHistory


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ('owner', 'creature', 'quantity', 'average_cost', 'updated_at')
    list_filter = ('creature__type',)
    search_fields = ('owner__username', 'creature__name')
    readonly_fields = ('id', 'acquired_at', 'updated_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'account', 'creature', 'order_type', 'execution_type',
        'quantity', 'filled_quantity', 'status', 'created_at'
    )
    list_filter = ('status', 'order_type', 'execution_type')
    search_fields = ('account__username', 'creature__name')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        'creature', 'buyer', 'seller', 'quantity',
        'price_per_unit', 'commission', 'executed_at'
    )
    list_filter = ('creature__type',)
    search_fields = ('buyer__username', 'seller__username', 'creature__name')
    readonly_fields = ('id', 'executed_at')


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = (
        'creature', 'interval', 'timestamp',
        'open_price', 'high_price', 'low_price', 'close_price', 'volume'
    )
    list_filter = ('interval', 'creature__type')
    search_fields = ('creature__name',)
    readonly_fields = ('id',)
