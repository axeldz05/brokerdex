from django.urls import path
from . import views

app_name = 'trading'
urlpatterns = [
    path('trading/market/', views.market_view, name='market'),
    path('trading/creature/<uuid:creature_id>/', views.creature_detail_view, name='creature_detail'),
    path('trading/order/place/', views.place_order_view, name='place_order'),
    path('trading/order/<uuid:order_id>/cancel/', views.cancel_order_view, name='cancel_order'),
    path('trading/portfolio/', views.portfolio_view, name='portfolio'),
    path('trading/orders/', views.order_history_view, name='orders'),
    path('trading/api/price-history/<uuid:creature_id>/', views.price_history_api, name='price_history_api'),
    path('trading/indices/', views.market_indices_view, name='market_indices'),
    path('trading/api/indices/', views.market_indices_api, name='market_indices_api'),
]
